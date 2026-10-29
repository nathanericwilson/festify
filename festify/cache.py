from __future__ import annotations

import json
import os
import time
from pathlib import Path


class Cache:
    """JSON cache: artists[normalized name] -> pick; location[candidate id] -> evidence lines.

    The personal app and the public page can run at the same time, so each write re-reads the file,
    applies just its own change, and replaces the file atomically under a short lock file.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"artists": {}, "location": {}, "mb": {}}
        self.data.update(self._read())

    def _read(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        return {}

    def _lock(self):
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        for _ in range(100):  # up to ~5 s
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return lock
            except FileExistsError:
                try:  # a lock left behind by a crashed process
                    if time.time() - lock.stat().st_mtime > 30:
                        lock.unlink()
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
        return None

    def _write(self, data: dict):
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def save(self):
        self._write(self.data)

    def get(self, section: str, key: str):
        return self.data.setdefault(section, {}).get(key)

    def set(self, section: str, key: str, value):
        lock = self._lock()
        try:
            fresh = self._read()  # pick up what another process wrote meanwhile
            for s, entries in fresh.items():
                if isinstance(entries, dict):
                    self.data.setdefault(s, {}).update({k: v for k, v in entries.items() if k not in self.data.get(s, {})})
            self.data.setdefault(section, {})[key] = value
            self._write(self.data)
        finally:
            if lock:
                try:
                    lock.unlink()
                except OSError:
                    pass
