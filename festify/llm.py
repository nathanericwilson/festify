"""Thin Anthropic wrapper: JSON extraction helpers, plain calls, web-search calls."""
from __future__ import annotations

import json


def extract_json(text: str):
    """Return the largest JSON object/array found in text (handles code fences and chatter)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    best, best_len = None, -1
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, end = dec.raw_decode(text[i:])
            except ValueError:
                continue
            if end > best_len:
                best, best_len = obj, end
    if best is None:
        raise ValueError(f"No JSON found in model output: {text[:200]!r}")
    return best


class BillingError(Exception):
    """Anthropic account can't be used (no credit / bad key). Fatal: never cache results past this."""


def _guard(fn):
    import anthropic

    def wrapped(*a, **k):
        try:
            return fn(*a, **k)
        except anthropic.AuthenticationError as e:
            raise BillingError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.") from e
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                raise BillingError("Your Anthropic credit balance is too low. Add credit at "
                                   "console.anthropic.com -> Plans & Billing, then re-run (finished work is cached).") from e
            raise
    return wrapped


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class LLM:
    def __init__(self, api_key: str, model: str, fast_model: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.fast_model = fast_model or model
        self.calls = self.searches = self.in_tokens = self.out_tokens = 0
        self.ask_json = _guard(self.ask_json)
        self.ask_json_web = _guard(self.ask_json_web)

    def _track(self, resp):
        u = getattr(resp, "usage", None)
        self.calls += 1
        if u:
            self.in_tokens += getattr(u, "input_tokens", 0) or 0
            self.out_tokens += getattr(u, "output_tokens", 0) or 0
            stu = getattr(u, "server_tool_use", None)
            self.searches += getattr(stu, "web_search_requests", 0) or 0

    def usage_line(self) -> str:
        return (f"Claude usage this run: {self.calls} calls, {self.searches} web searches, "
                f"{self.in_tokens:,} input / {self.out_tokens:,} output tokens")

    def ask_json(self, system: str, content, max_tokens: int = 4000, fast: bool = False):
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        resp = self.client.messages.create(
            model=self.fast_model if fast else self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
        )
        self._track(resp)
        return extract_json(_text(resp))

    def ask_json_web(self, system: str, prompt: str, max_uses: int = 2, max_tokens: int = 2000):
        resp = self.client.messages.create(
            model=self.fast_model, max_tokens=max_tokens, system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            messages=[{"role": "user", "content": prompt}],
        )
        self._track(resp)
        return extract_json(_text(resp))
