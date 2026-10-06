"""Deezer (primary) and Last.fm (secondary) clients for artist identity and top songs."""
from __future__ import annotations

from .http import Http
from .names import names_match


class Deezer:
    BASE = "https://api.deezer.com"

    def __init__(self, http: Http | None = None):
        self.http = http or Http(min_interval=0.15)

    def _get(self, path: str, **params):
        data = self.http.get_json(f"{self.BASE}{path}", params or None)
        if not data or (isinstance(data, dict) and "error" in data):
            return {}
        return data

    def exact_artists(self, name: str) -> list[dict]:
        """Artists whose normalized name equals `name`, most fans first."""
        res = self._get("/search/artist", q=name, limit=25).get("data", [])
        hits = [a for a in res if names_match(a.get("name", ""), name)]
        return sorted(hits, key=lambda a: a.get("nb_fan", 0), reverse=True)

    def top_tracks(self, artist_id, limit: int = 25) -> list[dict]:
        return self._get(f"/artist/{artist_id}/top", limit=limit).get("data", [])

    def albums(self, artist_id, limit: int = 8) -> list[dict]:
        return self._get(f"/artist/{artist_id}/albums", limit=limit).get("data", [])

    def track(self, track_id) -> dict:
        return self._get(f"/track/{track_id}")


class LastFm:
    BASE = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str, http: Http | None = None):
        self.key = api_key
        self.http = http or Http(min_interval=0.25)

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _call(self, method: str, **params):
        if not self.available:
            return {}
        data = self.http.get_json(
            self.BASE,
            {"method": method, "api_key": self.key, "format": "json", "autocorrect": 0, **params},
        )
        if not data or "error" in data:
            return {}
        return data

    def artist_info(self, name: str) -> dict | None:
        a = self._call("artist.getInfo", artist=name).get("artist")
        if not a or not names_match(a.get("name", ""), name):
            return None
        return {
            "name": a["name"],
            "url": a.get("url", ""),
            "mbid": a.get("mbid", ""),
            "listeners": int((a.get("stats") or {}).get("listeners") or 0),
            "playcount": int((a.get("stats") or {}).get("playcount") or 0),
            "tags": [t["name"] for t in (a.get("tags") or {}).get("tag", []) if isinstance(t, dict)],
        }

    def top_tracks(self, name: str, limit: int = 25) -> list[dict]:
        tr = self._call("artist.getTopTracks", artist=name, limit=limit).get("toptracks", {}).get("track", [])
        if isinstance(tr, dict):
            tr = [tr]
        return [
            {"title": t["name"], "listeners": int(t.get("listeners") or 0), "playcount": int(t.get("playcount") or 0)}
            for t in tr
        ]
