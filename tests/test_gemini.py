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
    assert url.endswith("/gemini-2.5-flash:generateContent") and headers["x-goog-api-key"] == "k"
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
    g, _ = make([Resp(429)] * 5)
    with pytest.raises(BillingError):
        g.ask_json("s", "p")


def test_bad_key_is_fatal():
    g, _ = make([Resp(403, text="API key not valid")])
    with pytest.raises(BillingError):
        g.ask_json("s", "p")
