import pytest

from festify.gemini import GeminiLLM
from festify.llm import BillingError


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload or {}, text

    def json(self):
        return self._p


class Sess:
    def __init__(self, resps):
        self.resps, self.bodies = list(resps), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append((url, json, headers))
        return self.resps.pop(0)


def ok(text):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}],
                      "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


def make(resps):
    s = Sess(resps)
    return GeminiLLM("k", min_interval=0, session=s), s


def test_images_and_json_mode_request_shape():
    g, s = make([ok('{"events": []}')])
    out = g.ask_json("sys", [{"type": "text", "text": "hi"},
                             {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}])
    assert out == {"events": []}
    url, body, headers = s.bodies[0]
    assert url.endswith("/gemini-3.8-flash:generateContent") and headers["x-goog-api-key"] == "k"
    assert body["contents"][0]["parts"][1] == {"inline_data": {"mime_type": "image/png", "data": "AAA"}}
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert g.usage_line().startswith("Gemini usage") and g.in_tokens == 10


def test_web_call_uses_search_tool_without_json_mode():
    g, s = make([ok('Found it. {"results": []}')])
    assert g.ask_json_web("sys", "find") == {"results": []}
    body = s.bodies[0][1]
    assert body["tools"] == [{"google_search": {}}] and "responseMimeType" not in body["generationConfig"]
    assert g.searches == 1


def test_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("festify.gemini.time.sleep", lambda *_: None)
    g, s = make([Resp(429), ok("{}")])
    assert g.ask_json("s", "p") == {}


def test_persistent_429_is_a_clean_stop(monkeypatch):
    monkeypatch.setattr("festify.gemini.time.sleep", lambda *_: None)
    g, _ = make([Resp(429)] * 12)
    with pytest.raises(BillingError):
        g.ask_json("s", "p")


def test_bad_key_is_fatal():
    g, _ = make([Resp(403, text="API key not valid")])
    with pytest.raises(BillingError):
        g.ask_json("s", "p")


def test_persistent_503_is_a_clean_stop(monkeypatch):
    monkeypatch.setattr("festify.gemini.time.sleep", lambda *_: None)
    g, _ = make([Resp(503)] * 12)
    with pytest.raises(BillingError):
        g.ask_json("s", "p")


def test_overloaded_model_falls_back_to_next_model(monkeypatch):
    monkeypatch.setattr("festify.gemini.time.sleep", lambda *_: None)
    g, s = make([Resp(503), Resp(503), ok("{}")])
    assert g.ask_json("s", "p") == {}
    assert g.model == "gemini-3.6-flash" and s.bodies[-1][0].endswith("gemini-3.6-flash:generateContent")


def test_search_quota_is_not_fatal_and_fails_fast(monkeypatch):
    from festify.gemini import SearchUnavailable

    monkeypatch.setattr("festify.gemini.time.sleep", lambda *_: None)
    g, s = make([Resp(429)])
    with pytest.raises(SearchUnavailable):
        g.ask_json_web("s", "p")
    assert len(s.bodies) == 1  # no retries / model hopping for the search quota


def test_timeout_retries_then_falls_back(monkeypatch):
    import requests
    from festify import gemini as g
    monkeypatch.setattr(g.time, "sleep", lambda *_: None)

    class Resp:
        status_code = 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

    class Sess:
        def __init__(self):
            self.models = []
        def post(self, url, **k):
            self.models.append(url.split("/")[-1].split(":")[0])
            if len(self.models) <= 2:
                raise requests.ReadTimeout("slow")
            return Resp()

    s = Sess()
    llm = g.GeminiLLM("k", min_interval=0, session=s)
    assert llm.ask_json("sys", "hi") == {"ok": True}
    assert s.models[0] == "gemini-3.8-flash" and s.models[2] == g.FALLBACKS[0]  # switched model after 2 timeouts
