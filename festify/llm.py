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


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class LLM:
    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def ask_json(self, system: str, content, max_tokens: int = 4000):
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
        )
        return extract_json(_text(resp))

    def ask_json_web(self, system: str, prompt: str, max_uses: int = 4, max_tokens: int = 4000):
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            messages=[{"role": "user", "content": prompt}],
        )
        return extract_json(_text(resp))
