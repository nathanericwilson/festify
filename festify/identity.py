"""Find a band's own Spotify artist page from the Instagram handle tagged on the poster.

Bands usually put their Spotify (or a Linktree that has it) in their Instagram bio, which identifies
them far better than name matching. Sources, in order:
  1. the Instagram profile: bio text, external URL and bio links (then the pages those link to)
  2. a Linktree named after the handle, accepted only if it links back to that Instagram account
Instagram blocks anonymous profile lookups quickly; a saved login session (IG_USERNAME +
`instaloader -l <user>`) works much better. On a block we stop asking Instagram for the rest of the run.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

SPOTIFY_ARTIST_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?artist/([A-Za-z0-9]{22})")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130 Safari/537.36"}


def spotify_ids(text: str) -> list[str]:
    return list(dict.fromkeys(SPOTIFY_ARTIST_RE.findall(text or "")))


class HandleLinker:
    def __init__(self, cache, ig_username: str = "", log=print, session=None, ig_fetch=None):
        self.cache, self.ig_username, self.log = cache, ig_username, log
        self.s = session or requests.Session()
        self.s.headers.update(UA)
        self.ig_ok = True
        self._ig_fetch = ig_fetch  # injectable for tests: handle -> list[str] | None
        self._loader = None

    # ---- Instagram -----------------------------------------------------------
    def _instaloader(self):
        if self._loader is None:
            import instaloader

            class NoWait(instaloader.RateController):  # never sleep for minutes on a 429: fail fast instead
                def wait_before_query(self, query_type):
                    pass

                def handle_429(self, query_type):
                    raise instaloader.exceptions.TooManyRequestsException("429 Too Many Requests")

            self._loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1,
                                                   rate_controller=lambda ctx: NoWait(ctx))
            if self.ig_username:
                try:
                    self._loader.load_session_from_file(self.ig_username)
                except FileNotFoundError:
                    pass
        return self._loader

    def _ig_texts(self, handle: str) -> list[str] | None:
        """Bio text + links for a profile; [] if the profile doesn't exist; None if Instagram can't be asked."""
        if self._ig_fetch:
            return self._ig_fetch(handle)
        if not self.ig_ok:
            return None
        import instaloader

        try:
            p = instaloader.Profile.from_username(self._instaloader().context, handle)
        except instaloader.exceptions.ProfileNotExistsException:
            return []
        except Exception as e:
            self.ig_ok = False
            limited = "429" in str(e)
            if self._instaloader().context.is_logged_in:
                why = "rate-limiting profile lookups (usually clears within an hour or so)" if limited else type(e).__name__
                self.log(f"  ! Instagram is {why}; skipping bio links this run. Re-run later; results are cached.")
            else:
                self.log(f"  ! Instagram won't show profiles without a login ({type(e).__name__}); skipping bio links "
                         f"this run. See README: 'Finding bands through their Instagram'.")
            return None
        links = []
        try:
            links = [l.get("url", "") for l in (p._node.get("bio_links") or []) if isinstance(l, dict)]
        except Exception:
            pass
        return [p.biography or "", p.external_url or "", *links]

    # ---- web pages -----------------------------------------------------------
    def _get(self, url: str) -> tuple[int, str]:
        try:
            r = self.s.get(url, timeout=12)
            return r.status_code, r.text if r.status_code == 200 else ""
        except requests.RequestException:
            return 0, ""

    def find(self, handle: str) -> dict | None:
        """{"spotify_id", "via"} for the band behind @handle, or None. Results are cached per handle."""
        handle = handle.strip().lstrip("@").lower()
        if not handle:
            return None
        hit = self.cache.get("handles", handle)
        if hit is not None:
            return hit or None
        result = None
        texts = self._ig_texts(handle)
        if texts:
            ids = spotify_ids(" ".join(texts))
            if ids:
                result = {"spotify_id": ids[0], "via": "Instagram bio"}
            else:
                urls = [u for t in texts for u in URL_RE.findall(t if "://" in t else ("https://" + t if t and "." in t and " " not in t else ""))]
                for u in dict.fromkeys(urls):
                    host = urlparse(u).netloc.lower()
                    if not host or "instagram.com" in host:
                        continue
                    status, html = self._get(u)
                    ids = spotify_ids(html)
                    if ids:
                        result = {"spotify_id": ids[0], "via": f"link in Instagram bio ({host})"}
                        break
        if not result:
            status, html = self._get(f"https://linktr.ee/{handle}")
            backlink = re.search(rf"instagram\.com/{re.escape(handle)}(?![\w.])", html, re.I)
            ids = spotify_ids(html)
            if backlink and ids:
                result = {"spotify_id": ids[0], "via": f"Linktree linked to @{handle}"}
        if result or texts is not None:  # don't remember a miss if Instagram couldn't be asked
            self.cache.set("handles", handle, result or {})
        return result
