from __future__ import annotations

import time

import requests


class Http:
    """requests wrapper with a minimum interval between calls and light retry."""

    def __init__(self, min_interval: float = 0.0, headers: dict | None = None):
        self.s = requests.Session()
        if headers:
            self.s.headers.update(headers)
        self.min_interval = min_interval
        self._last = 0.0

    def get_json(self, url: str, params: dict | None = None, retries: int = 3):
        for attempt in range(retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.get(url, params=params, timeout=20)
            except requests.RequestException:
                if attempt == retries - 1:
                    return None
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (429, 503) and attempt < retries - 1:
                time.sleep(float(r.headers.get("Retry-After", 2)) + attempt)
                continue
            if r.status_code != 200:
                return None
            try:
                return r.json()
            except ValueError:
                return None
        return None
