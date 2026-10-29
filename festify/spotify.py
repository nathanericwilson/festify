"""Spotify Web API client for the Feb-2026 Development Mode API (official API only).

Uses spotipy for auth/session/retries but calls endpoints directly (spotipy's helpers
may still hit removed ones). Removed: top-tracks, batch ?ids=, popularity/followers.
"""
from __future__ import annotations

import spotipy
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from .config import Config
from .llm import BillingError

import time

SCOPES = "playlist-modify-private playlist-modify-public ugc-image-upload"


def slim_track(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "uri": t.get("uri"),
        "name": t.get("name", ""),
        "artists": [{"id": a.get("id"), "name": a.get("name", "")} for a in t.get("artists", [])],
        "album": (t.get("album") or {}).get("name", ""),
    }


class Spotify:
    def __init__(self, cfg: Config, write: bool):
        if not (cfg.spotify_id and cfg.spotify_secret):
            raise SystemExit("Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in .env (see README).")
        if write:
            auth = SpotifyOAuth(
                client_id=cfg.spotify_id, client_secret=cfg.spotify_secret, redirect_uri=cfg.redirect_uri,
                scope=SCOPES, cache_path=str(cfg.token_cache), open_browser=True,
            )
        else:  # dry run: search/read only, no login needed
            auth = SpotifyClientCredentials(client_id=cfg.spotify_id, client_secret=cfg.spotify_secret,
                                            cache_handler=MemoryCacheHandler())
        self.sp = spotipy.Spotify(auth_manager=auth, retries=0, status_retries=0, requests_timeout=20)

    MIN_INTERVAL = 0.35  # seconds between calls: bursts are what trip Development Mode's rate limit

    def _call(self, fn, *a, **k):
        """Brief 429s are retried; a long rate-limit ban stops the run cleanly instead of sleeping for hours."""
        for attempt in range(4):
            wait = self.MIN_INTERVAL - (time.monotonic() - getattr(self, "_last", 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                return fn(*a, **k)
            except spotipy.SpotifyException as e:
                if e.http_status == 429:
                    if attempt < 3:
                        time.sleep(5 * (attempt + 1))
                        continue
                    err = BillingError("Spotify is rate-limiting this app (in Development Mode this can last many hours). "
                                       "Everything resolved so far is cached; re-run later to finish.")
                    try:  # how long Spotify says to wait, for callers that want to pause (the public page)
                        err.retry_after = int((getattr(e, "headers", None) or {}).get("Retry-After", 0))
                    except (TypeError, ValueError):
                        pass
                    raise err from e
                raise

    def _get(self, path: str, **params):
        try:
            return self._call(self.sp._get, path, **params) or {}
        except spotipy.SpotifyException as e:
            if e.http_status in (400, 404):
                return {}
            raise

    def get_artist(self, artist_id: str) -> dict | None:
        return self._get(f"artists/{artist_id}") or None

    def release_artists(self, kind: str, release_id: str) -> list[dict]:
        """Artists credited on an album or track (single-item endpoints are allowed in Development Mode)."""
        return (self._get(f"{kind}s/{release_id}") or {}).get("artists") or []

    # --- search ---------------------------------------------------------
    def search(self, q: str, type_: str, offset: int = 0) -> list[dict]:
        res = self._get("search", q=q, type=type_, limit=10, offset=offset)  # limit max is 10 now
        return (res.get(type_ + "s") or {}).get("items") or []

    def search_artists(self, name: str, pages: int = 2) -> list[dict]:
        out = []
        for p in range(pages):
            items = self.search(f'artist:"{name}"', "artist", offset=10 * p)
            out += [i for i in items if i]
            if len(items) < 10:
                break
        return out

    def track_by_isrc(self, isrc: str) -> list[dict]:
        return [slim_track(t) for t in self.search(f"isrc:{isrc}", "track") if t]

    def search_tracks(self, q: str, offset: int = 0) -> list[dict]:
        return [slim_track(t) for t in self.search(q, "track", offset) if t]

    # --- catalog --------------------------------------------------------
    def artist_albums(self, artist_id: str, limit: int = 10) -> list[dict]:
        res = self._get(f"artists/{artist_id}/albums", include_groups="album,single", limit=limit)
        return res.get("items") or []

    def album_tracks(self, album_id: str, album_name: str = "") -> list[dict]:
        items = self._get(f"albums/{album_id}/tracks", limit=50).get("items") or []
        return [slim_track({**t, "album": {"name": album_name}}) for t in items]

    # --- playlists ------------------------------------------------------
    def create_playlist(self, name: str, description: str, public: bool = False) -> dict:
        return self._call(self.sp._post, "me/playlists", payload={"name": name, "description": description[:300], "public": public})

    def add_items(self, playlist_id: str, uris: list[str]):
        for i in range(0, len(uris), 100):
            self._call(self.sp._post, f"playlists/{playlist_id}/items", payload={"uris": uris[i : i + 100]})

    def update_description(self, playlist_id: str, description: str):
        self._call(self.sp._put, f"playlists/{playlist_id}", payload={"description": description[:300]})

    def replace_items(self, playlist_id: str, uris: list[str]):
        """Replace a playlist's contents (PUT /playlists/{id}/items)."""
        self._call(self.sp._put, f"playlists/{playlist_id}/items", payload={"uris": uris[:100]})
        if len(uris) > 100:
            self.add_items(playlist_id, uris[100:])

    def set_cover(self, playlist_id: str, image_path: str) -> bool:
        """Upload the post image as the playlist cover (square JPEG, <256KB). Best effort."""
        import base64, io
        from PIL import Image

        try:
            img = Image.open(image_path).convert("RGB")
            side = min(img.size)
            l, t = (img.width - side) // 2, (img.height - side) // 2
            img = img.crop((l, t, l + side, t + side)).resize((min(side, 640),) * 2)
            for q in (88, 75, 60, 45):
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=q)
                if buf.tell() < 250_000:
                    break
            self._call(self.sp.playlist_upload_cover_image, playlist_id, base64.b64encode(buf.getvalue()))
            return True
        except Exception as e:
            print(f"  ! couldn't set playlist cover: {e}")
            return False
