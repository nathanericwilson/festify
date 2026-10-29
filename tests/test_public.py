import json
import threading
import urllib.request

import pytest

from festify.cache import Cache
from festify.llm import BillingError
from festify.public import Gate, Service, make_handler, page, shortcode

PL = [{"name": "Show – 2026-11-01", "url": "https://open.spotify.com/playlist/X", "description": "festify: all 3 matched."}]


def svc(tmp_path, builder=None, gate=None, **k):
    return Service(Cache(tmp_path / "c.json"), builder or (lambda url, progress: PL), gate or Gate(), log=lambda *_: None, **k)


@pytest.mark.parametrize("url,code", [
    ("https://www.instagram.com/p/DeMqScQhUpj/", "DeMqScQhUpj"),
    ("instagram.com/reel/Abc_12-x/?igsh=1", "Abc_12-x"),
    ("https://www.instagram.com/gnarwhaljrz/p/DeMqScQhUpj/", "DeMqScQhUpj"),
])
def test_accepts_post_and_reel_links(url, code):
    assert shortcode(url) == code


@pytest.mark.parametrize("url", ["", "hello", "https://www.instagram.com/gnarwhaljrz/",
                                 "https://evil.example/instagram.com/p/ABCDEF/", "https://open.spotify.com/p/ABCDEF"])
def test_rejects_everything_else(url):
    assert shortcode(url) is None


def test_job_runs_and_already_made_posts_are_free(tmp_path):
    calls = []
    s = svc(tmp_path, builder=lambda url, progress: calls.append(url) or PL)
    code, out = s.make("https://www.instagram.com/p/DeMqScQhUpj/", "1.2.3.4")
    assert code == 200 and "job" in out
    s.work_one(s.q.get())
    assert s.status(out["job"])["status"] == "done"
    assert calls == ["https://www.instagram.com/p/DeMqScQhUpj/"]
    code, out = s.make("https://instagram.com/p/DeMqScQhUpj/?x=1", "5.6.7.8")
    assert code == 200 and out == {"done": True, "playlists": PL} and len(calls) == 1   # no second build
    assert s.recent()[0]["url"] == PL[0]["url"]


def test_per_visitor_and_daily_caps(tmp_path):
    s = svc(tmp_path, gate=Gate(daily=3, per_visitor=2, max_queue=10))
    assert s.make("https://www.instagram.com/p/AAAAA1/", "a")[0] == 200
    assert s.make("https://www.instagram.com/p/AAAAA2/", "a")[0] == 200
    code, out = s.make("https://www.instagram.com/p/AAAAA3/", "a")
    assert code == 429 and "daily limit" in out["error"]
    assert s.make("https://www.instagram.com/p/AAAAA4/", "b")[0] == 200
    code, out = s.make("https://www.instagram.com/p/AAAAA5/", "c")
    assert code == 429 and "tomorrow" in out["error"]


def test_queue_limit_and_duplicate_requests_share_a_job(tmp_path):
    s = svc(tmp_path, gate=Gate(daily=99, per_visitor=99, max_queue=1))
    _, first = s.make("https://www.instagram.com/p/AAAAA1/", "a")
    assert s.make("https://www.instagram.com/p/AAAAA1/", "b")[1] == first   # same post: same job, no extra cost
    code, out = s.make("https://www.instagram.com/p/AAAAA2/", "c")
    assert code == 429 and "busy" in out["error"]


def test_spotify_ban_pauses_new_jobs_and_refunds(tmp_path):
    def banned(url, progress):
        e = BillingError("Spotify is rate-limiting this app")
        e.retry_after = 600
        raise e
    gate = Gate(daily=5, per_visitor=1)
    s = svc(tmp_path, builder=banned, gate=gate)
    _, out = s.make("https://www.instagram.com/p/AAAAA1/", "a")
    s.work_one(s.q.get())
    st = s.status(out["job"])
    assert st["status"] == "failed" and "Spotify" in st["error"]
    assert gate.visitors["a"] == 0 and gate.total == 0                       # failed jobs don't count
    code, out = s.make("https://www.instagram.com/p/AAAAA2/", "a")
    assert code == 429 and "break" in out["error"] and "minutes" in out["error"]


def test_internal_errors_are_not_shown(tmp_path):
    s = svc(tmp_path, builder=lambda url, progress: 1 / 0)
    _, out = s.make("https://www.instagram.com/p/AAAAA1/", "a")
    s.work_one(s.q.get())
    assert s.status(out["job"])["error"] == "Something went wrong. Try again later."


def test_turnstile_only_when_configured(tmp_path, monkeypatch):
    s = svc(tmp_path)
    assert s.make("https://www.instagram.com/p/AAAAA1/", "a")[0] == 200     # no secret: no check
    s2 = svc(tmp_path, turnstile_secret="sekrit")
    monkeypatch.setattr(s2, "_human", lambda token, visitor: token == "ok")
    assert s2.make("https://www.instagram.com/p/AAAAA2/", "a", token="bad")[0] == 403
    assert s2.make("https://www.instagram.com/p/AAAAA2/", "a", token="ok")[0] == 200
    assert 'data-sitekey="SITEKEY"' in page("SITEKEY", False) and "data-sitekey" not in page("", False)


def test_http_headers_and_embed(tmp_path):
    from http.server import ThreadingHTTPServer

    s = svc(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(s, embed_origins="https://me.example"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        r = urllib.request.urlopen(base + "/?embed=1")
        assert r.headers["Content-Security-Policy"] == "frame-ancestors https://me.example"
        assert 'class="embed"' in r.read().decode()
        req = urllib.request.Request(base + "/make", data=json.dumps({"url": "nope"}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
        req = urllib.request.Request(base + "/make", data=b"{}", method="POST", headers={"Origin": "https://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
    finally:
        srv.shutdown()


def test_ping_allows_only_embed_origins(tmp_path):
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc(tmp_path), embed_origins="https://me.example https://b.example"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for origin, want in [("https://me.example", "https://me.example"), ("https://b.example", "https://b.example"),
                             ("https://evil.example", None), ("", None)]:
            r = urllib.request.urlopen(urllib.request.Request(base + "/ping", headers={"Origin": origin} if origin else {}))
            assert json.loads(r.read()) == {"ok": True}
            assert r.headers["Access-Control-Allow-Origin"] == want
        r = urllib.request.urlopen(urllib.request.Request(base + "/recent", headers={"Origin": "https://me.example"}))
        assert r.headers["Access-Control-Allow-Origin"] is None  # only /ping is readable cross-origin
    finally:
        srv.shutdown()


class FakeRun:
    def __init__(self, sha="", get_rc=0, put_rc=0):
        self.calls, self.sha, self.get_rc, self.put_rc = [], sha, get_rc, put_rc

    def __call__(self, cmd, **_):
        import types
        self.calls.append(cmd)
        put = "PUT" in cmd
        return types.SimpleNamespace(returncode=self.put_rc if put else self.get_rc,
                                     stdout="" if put else self.sha, stderr="boom" if put and self.put_rc else "")


def test_publish_address_updates_or_creates_the_site_file():
    import base64

    from festify.public import publish_address

    run = FakeRun(sha="abc123")
    assert publish_address("https://x.trycloudflare.com", "me/site", lambda *_: None, run=run)
    put = run.calls[1]
    assert "repos/me/site/contents/data/festify.json" in put and "sha=abc123" in put
    content = next(a for a in put if a.startswith("content="))[len("content="):]
    assert json.loads(base64.b64decode(content))["url"] == "https://x.trycloudflare.com"

    run = FakeRun(get_rc=1)  # file doesn't exist yet
    assert publish_address("https://y.trycloudflare.com", "me/site", lambda *_: None, run=run)
    assert not any(a.startswith("sha=") for a in run.calls[1])

    logs = []
    assert not publish_address("https://z.trycloudflare.com", "me/site", logs.append, run=FakeRun(put_rc=1))
    assert "boom" in logs[0]
