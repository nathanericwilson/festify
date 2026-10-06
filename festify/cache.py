from __future__ import annotations

import json
from pathlib import Path


class Cache:
    """JSON cache: artists[normalized name] -> pick; location[candidate id] -> evidence lines."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"artists": {}, "location": {}, "mb": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get(self, section: str, key: str):
        return self.data.setdefault(section, {}).get(key)

    def set(self, section: str, key: str, value):
        self.data.setdefault(section, {})[key] = value
        self.save()
