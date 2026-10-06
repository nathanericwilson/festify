"""Step 3: location evidence from MusicBrainz and Anthropic web search (Bandcamp-aware)."""
from __future__ import annotations

import re

from .http import Http
from .llm import BillingError
from .models import Candidate
from .names import names_match, normalize_name

MB = "https://musicbrainz.org/ws/2"

WEB_SYSTEM = """You gather location evidence about small bands. Use web search (Bandcamp pages usually show an artist's
location; also check Spotify/Deezer/social/news/venue listings). Return STRICT JSON only, as the final thing you write:
{"results": [{"cid": str, "location": str, "evidence": str, "url": str}]}
- One result per candidate id given. "location" is where that specific band is from/based ("" if you cannot tell).
- Only attribute evidence to a candidate if the page clearly refers to the same band (match song/release titles or genre).
- Never invent. If nothing is found, return location "" and say so in evidence."""


class MusicBrainz:
    def __init__(self, contact: str, http: Http | None = None):
        self.http = http or Http(min_interval=1.1, headers={"User-Agent": f"festify/0.1 ( {contact} )"})

    def lookup(self, name: str) -> list[dict]:
        data = self.http.get_json(f"{MB}/artist", {"query": f'artist:"{name}"', "fmt": "json", "limit": 8}) or {}
        hits = [a for a in data.get("artists", []) if names_match(a.get("name", ""), name)][:4]
        out = []
        for a in hits:
            full = self.http.get_json(f"{MB}/artist/{a['id']}", {"inc": "url-rels+tags", "fmt": "json"}) or {}
            urls = [r["url"]["resource"] for r in full.get("relations", []) if r.get("url")]
            out.append({
                "mbid": a["id"],
                "area": (a.get("area") or {}).get("name", ""),
                "begin_area": (a.get("begin-area") or {}).get("name", ""),
                "country": a.get("country", ""),
                "disambiguation": a.get("disambiguation", ""),
                "tags": [t["name"] for t in full.get("tags", [])][:5],
                "urls": urls,
            })
        return out


def _ids_in(urls: list[str]) -> set[str]:
    ids = set()
    for u in urls:
        m = re.search(r"deezer\.com/(?:\w+/)?artist/(\d+)", u)
        if m:
            ids.add(f"deezer:{m.group(1)}")
        m = re.search(r"open\.spotify\.com/artist/(\w+)", u)
        if m:
            ids.add(f"spotify:{m.group(1)}")
        if "last.fm/music/" in u:
            ids.add("lastfm-url")
    return ids


def spotify_id_from_mb(entries: list[dict]) -> str | None:
    for e in entries:
        for u in e["urls"]:
            m = re.search(r"open\.spotify\.com/artist/(\w+)", u)
            if m:
                return m.group(1)
    return None


def describe_mb(e: dict, tie: str) -> str:
    place = ", ".join(x for x in (e["begin_area"], e["area"], e["country"]) if x) or "no area listed"
    dis = f" ({e['disambiguation']})" if e["disambiguation"] else ""
    bc = [u for u in e["urls"] if "bandcamp" in u]
    extra = f"; Bandcamp {bc[0]}" if bc else ""
    return f"MusicBrainz{dis}: {place} [{tie}]{extra}"


class Locator:
    def __init__(self, cfg, cache, llm, use_web: bool = True, mb: MusicBrainz | None = None, log=print,
                 max_web: int = 25, deep: bool = False):
        self.cfg, self.cache, self.llm, self.use_web, self.log = cfg, cache, llm, use_web, log
        self.max_web, self.deep, self.web_calls, self._cap_noted = max_web, deep, 0, False
        self.mb = mb or MusicBrainz(cfg.mb_contact)

    def target(self, event) -> str:
        return event.city or self.cfg.home_region

    def mb_entries(self, name: str) -> list[dict]:
        key = normalize_name(name)
        hit = self.cache.get("mb", key)
        if hit is None:
            hit = self.mb.lookup(name)
            self.cache.set("mb", key, hit)
        return hit

    def annotate(self, name: str, cands: list[Candidate], event):
        """Fill cand.location for every candidate (cached per candidate id)."""
        # Web search is the expensive step: only for same-name ambiguity (or --deep), and capped per run.
        want_web = self.use_web and self.llm is not None and (len(cands) > 1 or self.deep)
        need = []
        for c in cands:
            cached = self.cache.get("location", c.cid)
            if cached is not None and (cached.get("web") or not want_web):
                c.location = cached["lines"]
                c.spotify_id = c.spotify_id or cached.get("spotify_id")
            else:
                need.append(c)
        if not need:
            return
        entries = self.mb_entries(name)
        lines = {c.cid: [] for c in need}
        for c in need:
            for e in entries:
                tied = c.cid in _ids_in(e["urls"])
                if tied:
                    lines[c.cid].append(describe_mb(e, "linked to this candidate by URL"))
                    for u in e["urls"]:
                        m = re.search(r"open\.spotify\.com/artist/(\w+)", u)
                        if m and not c.spotify_id:
                            c.spotify_id = m.group(1)
                elif not any(c.cid in _ids_in(x["urls"]) for x in entries):
                    lines[c.cid].append(describe_mb(e, "same name only, not tied to a candidate"))
        web_ok = False
        if want_web and self.web_calls >= self.max_web:
            if not self._cap_noted:
                self._cap_noted = True
                self.log(f"  ! web-search cap reached ({self.max_web}); remaining ambiguous artists use MusicBrainz only. "
                         f"Re-run later or raise --max-searches.")
            want_web_now = False
        else:
            want_web_now = want_web
        if want_web_now:
            self.web_calls += 1
            try:
                for r in self._web(name, need, event).get("results", []):
                    if r.get("cid") in lines and (r.get("location") or r.get("evidence")):
                        lines[r["cid"]].append(
                            f"web: {r.get('location') or 'location unknown'} - {r.get('evidence', '')} {r.get('url', '')}".strip()
                        )
                web_ok = True
            except BillingError:
                raise
            except Exception as e:  # web search is best-effort
                self.log(f"  ! web location search failed for {name}: {e}")
        for c in need:
            c.location = lines[c.cid]
            if web_ok or not want_web:  # never persist evidence from a failed or skipped search
                self.cache.set("location", c.cid, {"lines": c.location, "spotify_id": c.spotify_id, "web": web_ok})

    def _web(self, name: str, cands: list[Candidate], event) -> dict:
        desc = "\n".join(
            f"- id={c.cid}: genres={c.genres}; releases={c.releases[:4]}; songs={c.songs[:4]}; "
            f"fans={c.fans}; listeners={c.listeners}" for c in cands
        )
        prompt = (
            f'Band name: "{name}". Event city: {event.city or "unknown"}; home region: {self.cfg.home_region}; '
            f"genre hints: {event.genre_hints}.\nCandidates (different bands may share this name):\n{desc}\n"
            f'Searches to try: "{name} band {event.city or self.cfg.home_region}", "{name} bandcamp".'
        )
        h = getattr(event, 'handle_map', {}).get(normalize_name(name), {}).get('handle')
        if h:
            prompt += f' The poster tags this band as Instagram @{h}; use instagram.com/{h} / Linktree / Bandcamp links as location evidence.'
        return self.llm.ask_json_web(WEB_SYSTEM, prompt)
