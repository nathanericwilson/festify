"""Free alternative to the Anthropic client: Google Gemini (AI Studio free tier).

Same interface as llm.LLM (ask_json / ask_json_web / usage_line) so the rest of festify
doesn't care which provider is used. Free-tier limits change; 429s are retried and a
sustained quota error stops the run cleanly (finished work is cached).
"""
from __future__ import annotations

import time

import requests

from .llm import BillingError, extract_json

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    out = []
    for b in content:
        if b.get("type") == "image":
            src = b["source"]
            out.append({"inline_data": {"mime_type": src["media_type"], "data": src["data"]}})
        else:
            out.append({"text": b["text"]})
    return out


class GeminiLLM:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", min_interval: float = 4.0, session=None):
        self.key, self.model, self.min_interval = api_key, model, min_interval
        self.s = session or requests.Session()
        self.calls = self.searches = self.in_tokens = self.out_tokens = 0
        self._last = 0.0

    def usage_line(self) -> str:
        return (f"Gemini usage this run (free tier): {self.calls} calls, {self.searches} grounded searches, "
                f"{self.in_tokens:,} input / {self.out_tokens:,} output tokens")

    def _post(self, body: dict) -> dict:
        url = f"{BASE}/{self.model}:generateContent"
        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.s.post(url, json=body, headers={"x-goog-api-key": self.key}, timeout=120)
            if r.status_code == 200:
                data = r.json()
                self.calls += 1
                u = data.get("usageMetadata", {})
                self.in_tokens += u.get("promptTokenCount", 0)
                self.out_tokens += u.get("candidatesTokenCount", 0)
                return data
            if r.status_code in (400, 401, 403) and ("API key" in r.text or "API_KEY" in r.text or r.status_code in (401, 403)):
                raise BillingError("Gemini rejected the API key. Check GEMINI_API_KEY in .env "
                                   "(free key: https://aistudio.google.com/apikey).")
            if r.status_code in (429, 500, 503) and attempt < 4:
                time.sleep(min(60, 8 * (attempt + 1)))
                continue
            if r.status_code == 429:
                raise BillingError("Gemini free-tier quota is used up for now (per-minute or per-day limit). "
                                   "Wait and re-run; finished work is cached.")
            raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:300]}")
        raise RuntimeError("Gemini: too many retries")

    @staticmethod
    def _text(data: dict) -> str:
        cands = data.get("candidates") or []
        if not cands:
            raise ValueError(f"Gemini returned no candidates: {str(data)[:200]}")
        return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))

    def _body(self, system: str, content, max_tokens: int, json_mode: bool, tools=None) -> dict:
        gen = {"maxOutputTokens": max(max_tokens, 4096)}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        if "flash" in self.model:
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": _parts(content)}], "generationConfig": gen}
        if tools:
            body["tools"] = tools
        return body

    def ask_json(self, system: str, content, max_tokens: int = 4000, fast: bool = False):
        data = self._post(self._body(system, content, max_tokens, json_mode=True))
        return extract_json(self._text(data))

    def ask_json_web(self, system: str, prompt: str, max_uses: int = 2, max_tokens: int = 2000):
        # JSON mode can't be combined with the search tool, so the prompt asks for JSON and we parse it.
        body = self._body(system, prompt, max_tokens, json_mode=False, tools=[{"google_search": {}}])
        data = self._post(body)
        self.searches += 1
        return extract_json(self._text(data))
