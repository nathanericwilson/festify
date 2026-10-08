"""Free web search through Tavily (1,000 free searches/month, no card): https://tavily.com

Used for two things, only for bands festify can't settle on its own:
  * identity: find the band's own Spotify link on its Linktree / Bandcamp / Spotify / Instagram pages
  * location: snippets that say where the band is from (handed to the LLM to attribute per candidate)
Every query is cached, there is a per-run cap, and any quota/auth error turns search off for the run.
"""
from __future__ import annotations

import re

import requests

from .identity import spotify_ids
from .names import normalize_name

API = "https://api.tavily.com/search"
LINK_SITES = ["linktr.ee", "open.spotify.com", "bandcamp.com", "instagram.com", "beacons.ai", "solo.to"]


class WebSearch:
    def __init__(self, api_key: str, cache, max_searches: int = 30, log=print, session=None):
        self.key, self.cache, self.max, self.log = api_key, cache, max_searches, log
        self.s = session or requests.Session()
        self.used = 0
        self.on = bool(api_key)

    def search(self, query: str, domains: list[str] | None = None, raw: bool = False, n: int = 5) -> list[dict]:
        ck = f"{query}|{','.join(domains or [])}|{int(raw)}"
        hit = self.cache.get("search", ck)
        if hit is not None:
            return hit
        if not self.on:
            return []
        if self.used >= self.max:
            self.on = False
            self.log(f"  ! web-search cap reached ({self.max} this run); the rest use other evidence only.")
            return []
        self.used += 1
        body = {"query": query, "search_depth": "basic", "max_results": n, "include_raw_content": "text" if raw else False}
        if domains:
            body["include_domains"] = domains
        try:
            r = self.s.post(API, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
        except requests.RequestException as e:
            self.log(f"  ! web search failed ({type(e).__name__}); continuing without it.")
            return []
        if r.status_code != 200:
            self.on = False  # 401 bad key, 429/432 limits: stop for this run, don't cache
            self.log(f"  ! web search unavailable (HTTP {r.status_code}); continuing without it this run.")
            return []
        results = [{"url": x.get("url", ""), "title": x.get("title", ""), "content": x.get("content", ""),
                    "raw": (x.get("raw_content") or "")[:20000]} for x in r.json().get("results", [])]
        self.cache.set("search", ck, results)
        return results

    def extract(self, url: str) -> str | None:
        """Text of a public page via Tavily Extract (1 credit per 5 pages; cached). None if unavailable."""
        hit = self.cache.get("extract", url)
        if hit:
            return hit
        for _ in range(2):  # pages sometimes come back empty on the first try
            if not self.on:
                return None
            try:
                r = self.s.post(API.replace("/search", "/extract"), json={"urls": [url], "extract_depth": "basic"},
                                headers={"Authorization": f"Bearer {self.key}"}, timeout=60)
            except requests.RequestException as e:
                self.log(f"  ! page read failed ({type(e).__name__}); continuing without it.")
                return None
            if r.status_code != 200:
                self.log(f"  ! page reader unavailable (HTTP {r.status_code}).")
                return None
            res = r.json().get("results") or []
            text = (res[0].get("raw_content") or "")[:20000] if res else ""
            if text:
                self.cache.set("extract", url, text)  # only cache real pages, so a flaky miss is retried next run
                return text
        return None

    # ---- identity ---------------------------------------------------------------
    def spotify_links(self, name: str, handle: str | None, region_words: list[str]) -> list[dict]:
        """Spotify artist IDs found on pages that are clearly about this band.

        A page counts only if it mentions the Instagram handle, or the band name together with one of the
        relevant places, or it is the band's own link page (Linktree/Bandcamp/etc. whose title has the band name).
        Returns [{"spotify_id", "via", "url"}], best first; the caller still checks the Spotify artist's name.
        """
        queries = []
        if handle:
            queries.append((f'"{handle}"', LINK_SITES))
        queries.append((f'"{name}" band {" ".join(region_words[:2])}', LINK_SITES))
        nname = normalize_name(name)
        found: list[dict] = []
        for q, domains in queries:
            for res in self.search(q, domains, raw=True):
                text = " ".join((res["url"], res["title"], res["content"], res["raw"]))
                low = text.lower()
                ids = spotify_ids(text)
                if not ids:
                    continue
                host = re.sub(r"^https?://(www\.)?", "", res["url"]).split("/")[0]
                own_page = host.endswith(("linktr.ee", "bandcamp.com", "beacons.ai", "solo.to")) and nname in normalize_name(res["title"])
                tied = (handle and handle.lower() in low) or (nname in normalize_name(text) and any(w.lower() in low for w in region_words)) or own_page
                if tied:
                    why = f"@{handle} page" if handle and handle.lower() in low else ("band's own page" if own_page else "local page")
                    for sid in ids:
                        if sid not in {f["spotify_id"] for f in found}:
                            found.append({"spotify_id": sid, "via": f"web search ({why}: {host})", "url": res["url"]})
            if found:
                break  # the handle query is the strongest; don't spend another search if it worked
        return found

    def find_handle(self, name: str, region_words: list[str]) -> str | None:
        """An Instagram handle for a band the poster didn't tag, e.g. 'disillusioned_band', taken from the
        same (cached) search results. Accepted only if the handle is just the band name plus a decorative word."""
        want = normalize_name(name)
        if not want:
            return None
        for r in self.search(f'"{name}" band {" ".join(region_words[:2])}', LINK_SITES, raw=True):
            text = " ".join((r["url"], r["title"], r["content"]))
            for h in re.findall(r"(?:instagram\.com/|@|\b)([a-z0-9][a-z0-9._]{2,29})", text.lower()):
                core = re.sub(r"[._]+", " ", h).strip()
                core = re.sub(r"\b(the|band|official|music|slc|utah|ut)\b", " ", core)
                if normalize_name(core) == want and h != want.replace(" ", ""):
                    return h.rstrip(".")
        return None

    # ---- location ---------------------------------------------------------------
    def location_snippets(self, name: str, handle: str | None, region_words: list[str]) -> list[dict]:
        """Same queries as spotify_links (so usually cached = free). Bands' own Instagram/Bandcamp snippets
        typically say where they're from and name their releases."""
        out = []
        queries = ([f'"{handle}"'] if handle else []) + [f'"{name}" band {" ".join(region_words[:2])}']
        for q in queries:
            for r in self.search(q, LINK_SITES, raw=True):
                text = (r["content"] + " " + r["raw"][:1500]).strip()
                if text and r["url"] not in {o["url"] for o in out}:
                    out.append({"url": r["url"], "title": r["title"], "text": text[:900]})
        return out[:8]
