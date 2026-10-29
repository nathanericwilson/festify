"""festify's public page: anyone pastes an Instagram post link and gets a Spotify playlist link.

`python -m festify.public` serves it on 127.0.0.1:8770. Public.bat also starts a Cloudflare Tunnel so
other people can reach it. Playlists are made PUBLIC on the owner's Spotify account, named "festify · …".

It's built to be safe to leave open:
  * only Instagram post/reel links are accepted (no uploads, no options);
  * a post that was already made returns its playlist instantly (no API calls);
  * per-visitor and daily caps, a short queue, and one job at a time;
  * a Spotify rate-limit ban or a free-tier quota pauses new jobs instead of failing repeatedly;
  * optional Cloudflare Turnstile bot check; visitor IPs are only counted in memory, never written to disk;
  * it can be embedded (?embed=1) only on sites listed in PUBLIC_EMBED_ORIGINS.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

from .config import ROOT, load_config
from .instagram import IG_RE

HOST, PORT = "127.0.0.1", int(os.environ.get("PUBLIC_PORT", "8770"))
TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def shortcode(url: str) -> str | None:
    """The post's shortcode if this is an Instagram post/reel link, else None."""
    url = (url or "").strip()
    if len(url) > 300 or not re.match(r"^(https?://)?([\w-]+\.)?instagram\.com/", url, re.I):
        return None
    m = IG_RE.search(url)
    return m.group(1) if m and 5 <= len(m.group(1)) <= 40 else None


class Gate:
    """Daily caps, queue size and pauses. Counts reset each day and live only in memory."""

    def __init__(self, daily: int = 20, per_visitor: int = 3, max_queue: int = 5, clock=time.time):
        self.daily, self.per_visitor, self.max_queue, self.clock = daily, per_visitor, max_queue, clock
        self.day, self.total, self.visitors = date.today(), 0, {}
        self.paused_until, self.pause_reason = 0.0, ""
        self.lock = threading.Lock()

    def _roll(self):
        if date.today() != self.day:
            self.day, self.total, self.visitors = date.today(), 0, {}

    def check(self, visitor: str, queued: int) -> str | None:
        """None if a new job may start, else a friendly reason."""
        with self.lock:
            self._roll()
            if self.clock() < self.paused_until:
                mins = int((self.paused_until - self.clock()) / 60) + 1
                when = f"{mins} minutes" if mins < 120 else f"{mins // 60} hours"
                return f"festify is taking a break ({self.pause_reason}). Try again in about {when}."
            if self.total >= self.daily:
                return "festify has made all the playlists it can today. Try again tomorrow."
            if self.visitors.get(visitor, 0) >= self.per_visitor:
                return f"You've made {self.per_visitor} playlists today, the daily limit. Try again tomorrow."
            if queued >= self.max_queue:
                return "festify is busy right now. Try again in a few minutes."
            return None

    def take(self, visitor: str):
        with self.lock:
            self._roll()
            self.total += 1
            self.visitors[visitor] = self.visitors.get(visitor, 0) + 1

    def refund(self, visitor: str):
        with self.lock:
            self.total = max(0, self.total - 1)
            if self.visitors.get(visitor):
                self.visitors[visitor] -= 1

    def pause(self, seconds: float, reason: str):
        with self.lock:
            self.paused_until = max(self.paused_until, self.clock() + seconds)
            self.pause_reason = reason


class Service:
    """Jobs, the queue and the worker. `builder(url, progress)` makes the playlists (injectable for tests)."""

    def __init__(self, cache, builder, gate: Gate, log=print, turnstile_secret: str = ""):
        self.cache, self.builder, self.gate, self.log = cache, builder, gate, log
        self.turnstile_secret = turnstile_secret
        self.jobs: dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        self.by_code: dict[str, str] = {}  # shortcode -> job id while it's queued/running

    # ---- requests -----------------------------------------------------------
    def make(self, url: str, visitor: str, token: str = "") -> tuple[int, dict]:
        code = shortcode(url)
        if not code:
            return 400, {"error": "Paste a link to an Instagram post, like https://www.instagram.com/p/ABC123/"}
        done = self.cache.get("public_posts", code)
        if done and done.get("playlists"):
            return 200, {"done": True, "playlists": done["playlists"]}
        if code in self.by_code:  # someone already asked for this post: follow that job
            return 200, {"job": self.by_code[code]}
        if self.turnstile_secret and not self._human(token, visitor):
            return 403, {"error": "Please complete the check and try again."}
        reason = self.gate.check(visitor, self.q.qsize())
        if reason:
            return 429, {"error": reason}
        self.gate.take(visitor)
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {"code": code, "status": "queued", "step": "Waiting in line…", "visitor": visitor,
                          "started": time.time(), "playlists": [], "error": ""}
        self.by_code[code] = jid
        self.q.put(jid)
        return 200, {"job": jid}

    def status(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if not j:
            return None
        return {"status": j["status"], "step": j["step"], "playlists": j["playlists"], "error": j["error"]}

    def recent(self, n: int = 20) -> list[dict]:
        posts = (self.cache.data.get("public_posts") or {}).values()
        items = [dict(p, made=v.get("made", "")) for v in posts for p in v.get("playlists", [])]
        return sorted(items, key=lambda p: p["made"], reverse=True)[:n]

    def _human(self, token: str, visitor: str) -> bool:
        try:
            r = requests.post(TURNSTILE_VERIFY, data={"secret": self.turnstile_secret, "response": token,
                                                      "remoteip": visitor}, timeout=10)
            return bool(r.json().get("success"))
        except Exception:
            return False

    # ---- worker -------------------------------------------------------------
    def work_one(self, jid: str):
        from .cli import TooBig
        from .instagram import IGFetchError
        from .llm import BillingError

        j = self.jobs[jid]
        j["status"], j["step"] = "running", "Starting…"
        url = f"https://www.instagram.com/p/{j['code']}/"
        self.log(f"job {jid} start {url}")

        def progress(msg):
            j["step"] = msg

        try:
            made = self.builder(url, progress)
            if made:
                j["playlists"], j["status"], j["step"] = made, "done", "Done"
                self.cache.set("public_posts", j["code"], {"playlists": made, "made": datetime.now().isoformat(timespec="seconds")})
            else:
                j["status"], j["error"] = "failed", "Couldn't find songs on Spotify for the bands in that post."
        except TooBig as e:
            j["status"], j["error"] = "failed", str(e)
        except IGFetchError:
            j["status"], j["error"] = "failed", ("Couldn't open that Instagram post. It may be private, or Instagram "
                                                 "is limiting requests. Try again later.")
        except BillingError as e:
            wait = getattr(e, "retry_after", 0) or 3600
            reason = "Spotify's limit" if "Spotify" in str(e) else "a free-tier limit"
            self.gate.pause(wait, reason)
            j["status"], j["error"] = "failed", f"festify hit {reason} and is taking a break. Try again later."
        except Exception:
            j["status"], j["error"] = "failed", "Something went wrong. Try again later."
            self.log(traceback.format_exc())
        finally:
            if j["status"] != "done":
                self.gate.refund(j["visitor"])
            self.by_code.pop(j["code"], None)
            j.pop("visitor", None)  # don't keep visitor addresses around
            self.log(f"job {jid} {j['status']} {j['error']}")

    def worker(self):
        while True:
            jid = self.q.get()
            try:
                self.work_one(jid)
            finally:
                self.q.task_done()


# ---------------------------------------------------------------------------- web
def make_handler(svc: Service, site_key: str = "", embed_origins: str = ""):
    frame = "frame-ancestors " + (embed_origins.strip() or "'none'")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "image" not in ctype else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", frame)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _visitor(self) -> str:
            return self.headers.get("CF-Connecting-IP") or self.client_address[0]

        def do_GET(self):
            path, _, qs = self.path.partition("?")
            if path == "/":
                embed = "embed=1" in qs
                return self._send(200, page(site_key, embed), "text/html")
            if path == "/favicon.png":
                return self._send(200, (ROOT / "assets" / "festify.png").read_bytes(), "image/png")
            if path == "/recent":
                return self._send(200, {"playlists": svc.recent()})
            m = re.fullmatch(r"/job/([0-9a-f]{12})", path)
            if m:
                st = svc.status(m.group(1))
                return self._send(200 if st else 404, st or {"error": "not found"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/make":
                return self._send(404, {"error": "not found"})
            origin, host = self.headers.get("Origin", ""), self.headers.get("Host", "")
            if origin and re.sub(r"^https?://", "", origin) != host:
                return self._send(403, {"error": "forbidden"})
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n > 4096:
                return self._send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad request"})
            code, out = svc.make(str(body.get("url", "")), self._visitor(), str(body.get("token", "")))
            self._send(code, out)

    return Handler


def page(site_key: str, embed: bool) -> str:
    ts = (f'<div class="cf-turnstile" data-sitekey="{site_key}" data-theme="dark"></div>'
          '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>') if site_key else ""
    return (PAGE.replace("__TURNSTILE__", ts).replace("__EMBED__", "embed" if embed else ""))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>festify</title><link rel="icon" href="/favicon.png">
<meta name="description" content="Paste an Instagram concert post, get a Spotify playlist of the bands on it.">
<style>
:root{--bg:#14102c;--card:#1d1740;--ink:#f1eefc;--muted:#a49cc9;--line:#342b66;--accent:#1ed760;--pink:#ff5fbe;--warn:#ffc35a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
body.embed{background:transparent}
.wrap{max-width:640px;margin:auto;padding:28px 16px 40px}.embed .wrap{padding:8px}
header{display:flex;gap:14px;align-items:center;margin-bottom:6px}header img{width:64px;height:64px;border-radius:14px}
h1{margin:0;font-size:30px;letter-spacing:-.02em}h1 span{color:var(--accent)}
.sub{color:var(--muted);margin:4px 0 18px}.embed header,.embed .sub,.embed .recent,.embed footer{display:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:10px;flex-wrap:wrap}
input{flex:1 1 260px;min-width:0;border:1px solid var(--line);border-radius:12px;padding:13px;background:#120e28;color:var(--ink);font:inherit}
button{border:0;border-radius:999px;padding:13px 22px;font:700 15px system-ui;background:var(--accent);color:#06210f;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.cf-turnstile{margin-top:10px}
#status{margin-top:12px;color:var(--muted);min-height:1.5em}#status.err{color:var(--warn)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:8px;animation:p 1s infinite}@keyframes p{50%{opacity:.25}}
a.pl{display:block;margin-top:12px;padding:14px;border:1px solid var(--line);border-radius:12px;color:var(--ink);text-decoration:none;font-weight:600}
a.pl:hover{border-color:var(--accent)}a.pl small{display:block;color:var(--muted);font-weight:400;font-size:13px;margin-top:4px}
.recent h2{font-size:15px;color:var(--muted);margin:0 0 8px;font-weight:600}
.recent a{display:block;color:var(--ink);text-decoration:none;padding:7px 0;border-bottom:1px solid var(--line);font-size:14px}
.recent a:hover{color:var(--accent)}
footer{color:var(--muted);font-size:12.5px}footer a{color:var(--muted)}
</style></head><body class="__EMBED__"><div class="wrap">
<header><img src="/favicon.png" alt=""><div><h1>fest<span>ify</span></h1></div></header>
<p class="sub">Paste an Instagram concert post. Get a Spotify playlist of the bands on it, so you can decide if the show's worth going to.</p>
<div class="card">
  <div class="row"><input id="url" type="url" inputmode="url" placeholder="https://www.instagram.com/p/…" aria-label="Instagram post link" autocomplete="off">
  <button id="go">Make playlist</button></div>
  __TURNSTILE__
  <div id="status" role="status" aria-live="polite"></div>
  <div id="result"></div>
</div>
<div class="card recent"><h2>Recently made</h2><div id="recent"><span style="color:var(--muted);font-size:14px">Nothing yet.</span></div></div>
<footer>Playlists are created on the festify Spotify account and open in Spotify. Not affiliated with Spotify or Instagram.
Small local bands sometimes share names with other artists; anything uncertain is noted in the playlist description.</footer>
</div>
<script>
const $=id=>document.getElementById(id);let timer=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const safe=u=>/^https:\/\/open\.spotify\.com\//.test(u)?u:"#";
function show(pls){$("result").innerHTML=pls.map(p=>`<a class="pl" href="${safe(p.url)}" target="_blank" rel="noopener">▶ ${esc(p.name)}<small>${esc((p.description||"").replace(/\s*Made with festify\.$/,"").replace(/^festify:\s*/,""))}</small></a>`).join("")}
function status(t,err,busy){$("status").className=err?"err":"";$("status").innerHTML=(busy?'<span class="dot"></span>':"")+esc(t)}
async function poll(id){const r=await fetch("/job/"+id);if(!r.ok){status("Lost track of that request. Try again.",true);return done()}
  const j=await r.json();if(j.status==="done"){status("Here's your playlist:");show(j.playlists);loadRecent();return done()}
  if(j.status==="failed"){status(j.error,true);return done()}status(j.step,false,true);timer=setTimeout(()=>poll(id),1500)}
function done(){$("go").disabled=false;if(window.turnstile)try{turnstile.reset()}catch(e){}}
async function go(){const url=$("url").value.trim();if(!url)return;$("go").disabled=true;$("result").innerHTML="";status("Starting…",false,true);
  let token="";const t=document.querySelector('[name="cf-turnstile-response"]');if(t)token=t.value;
  try{const r=await fetch("/make",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,token})});
    const j=await r.json();if(j.error){status(j.error,true);return done()}
    if(j.done){status("Already made, here it is:");show(j.playlists);return done()}poll(j.job)}
  catch(e){status("festify isn't reachable right now. Try again later.",true);done()}}
async function loadRecent(){try{const j=await (await fetch("/recent")).json();if(j.playlists.length)
  $("recent").innerHTML=j.playlists.map(p=>`<a href="${safe(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>`).join("")}catch(e){}}
$("go").onclick=go;$("url").onkeydown=e=>{if(e.key==="Enter")go()};loadRecent();
</script></body></html>"""


def default_builder(log):
    from .cli import make_public_playlists

    def build(url, progress):
        return make_public_playlists(url, progress=progress, log=log,
                                     max_artists=env_int("PUBLIC_MAX_ARTISTS", 25))
    return build


def main():
    import sys

    from .cache import Cache
    from .gui import Server  # same no-port-sharing server class as the personal app

    cfg = load_config()  # also loads .env into the environment
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)

    def log(msg):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        with open(logdir / f"public_{date.today():%Y%m%d}.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    gate = Gate(daily=env_int("PUBLIC_DAILY_LIMIT", 20), per_visitor=env_int("PUBLIC_PER_VISITOR", 3))
    svc = Service(Cache(cfg.cache_path), default_builder(log), gate, log=log,
                  turnstile_secret=os.environ.get("TURNSTILE_SECRET", "").strip())
    threading.Thread(target=svc.worker, daemon=True).start()
    handler = make_handler(svc, os.environ.get("TURNSTILE_SITE_KEY", "").strip(),
                           os.environ.get("PUBLIC_EMBED_ORIGINS", "").strip())
    try:
        srv = Server((HOST, PORT), handler)
    except OSError:
        sys.exit(f"Port {PORT} is in use. Is festify's public page already running? Close it and try again.")
    log(f"festify public page running at http://{HOST}:{PORT}/ (limits: {gate.daily}/day, {gate.per_visitor} per visitor)")
    tunnel = start_tunnel(log) if "--tunnel" in sys.argv else None
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()


def cloudflared_path() -> str | None:
    import shutil

    for p in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe"):
        if p and Path(p).exists():
            return p
    return None


def start_tunnel(log):
    """A free Cloudflare quick tunnel: a public https address (it changes each time) that forwards here.
    For a fixed address on your own domain, see README "Share it with others"."""
    import subprocess
    import sys
    import webbrowser

    exe = cloudflared_path()
    if not exe:
        log("! cloudflared isn't installed (winget install Cloudflare.cloudflared); the page is only on this PC.")
        return None
    name = os.environ.get("PUBLIC_TUNNEL_NAME", "").strip()  # a named tunnel on your own domain, once set up
    cmd = [exe, "tunnel", "run", name] if name else [exe, "tunnel", "--no-autoupdate", "--url", f"http://{HOST}:{PORT}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def watch():
        for line in proc.stdout:
            m = re.search(r"https://[\w-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                (ROOT / "logs" / "public_url.txt").write_text(url + "\n", encoding="utf-8")
                log(f"PUBLIC ADDRESS: {url}  (share this; it changes each time festify public restarts)")
                if "--no-browser" not in sys.argv:
                    webbrowser.open(url)
            elif "ERR" in line:
                log("tunnel: " + line.strip()[:200])
    threading.Thread(target=watch, daemon=True).start()
    log("starting the Cloudflare tunnel…" + (f" (named tunnel '{name}')" if name else ""))
    return proc


if __name__ == "__main__":
    main()
