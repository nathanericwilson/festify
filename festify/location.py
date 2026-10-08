"""Where is each candidate from? Evidence -> places -> distance to the user's relevant cities.

Sources (all free):
  * MusicBrainz: begin-area/area, expanded to "City, State, Country" via area relations; tied to a candidate
    when MusicBrainz links to that exact Deezer/Spotify artist.
  * Web search (Tavily) snippets, attributed per candidate by the LLM (plain call, no paid search tool).
Places are geocoded (geo.py) and the closest tied place sets the candidate's distance and tier.
"""
from __future__ import annotations

import json
import re

from .http import Http
from .llm import BillingError
from .models import Candidate
from .names import names_match, normalize_name

MB = "https://musicbrainz.org/ws/2"

ATTRIBUTE_SYSTEM = """You decide where bands are from, using only the evidence given. Several different artists may
share one name. Return STRICT JSON only:
{"results": [{"cid": str, "location": str, "same_band": bool, "evidence": str, "url": str}]}
- One result per candidate id. "location": where THAT candidate is from/based, as "City, State/Region, Country"
  (as specific as the evidence allows; "" if unknown). Never guess a state or country that isn't supported.
- same_band = true only if the evidence clearly refers to that exact candidate (matching song/release titles,
  label, or the Instagram handle given). A page about a different band with the same name is not evidence.
- "evidence": one short sentence; "url": the source URL if any. Never invent."""


class MusicBrainz:
    def __init__(self, contact: str, cache=None, http: Http | None = None):
        self.http = http or Http(min_interval=1.1, headers={"User-Agent": f"festify/0.2 ( {contact} )"})
        self.cache = cache

    def lookup(self, name: str) -> list[dict]:
        data = self.http.get_json(f"{MB}/artist", {"query": f'artist:"{name}"', "fmt": "json", "limit": 8}) or {}
        hits = [a for a in data.get("artists", []) if names_match(a.get("name", ""), name)][:4]
        out = []
        for a in hits:
            full = self.http.get_json(f"{MB}/artist/{a['id']}", {"inc": "url-rels+tags", "fmt": "json"}) or {}
            urls = [r["url"]["resource"] for r in full.get("relations", []) if r.get("url")]
            places = [p for p in (self.area_label(a.get("begin-area")), self.area_label(a.get("area"))) if p]
            out.append({
                "mbid": a["id"], "places": list(dict.fromkeys(places)), "country": a.get("country", ""),
                "disambiguation": a.get("disambiguation", ""),
                "tags": [t["name"] for t in full.get("tags", [])][:5], "urls": urls,
            })
        return out

    def area_label(self, area: dict | None) -> str:
        """'Springville' -> 'Springville, Utah, United States' by walking MusicBrainz 'part of' relations."""
        if not area or not area.get("id"):
            return ""
        key = area["id"]
        if self.cache is not None:
            hit = self.cache.get("mb_area", key)
            if hit is not None:
                return hit
        parts, cur = [area.get("name", "")], area
        for _ in range(4):  # city -> county -> state -> country
            if cur.get("type") == "Country":
                break
            data = self.http.get_json(f"{MB}/area/{cur['id']}", {"inc": "area-rels", "fmt": "json"}) or {}
            parent = next((r["area"] for r in data.get("relations", [])
                           if r.get("type") == "part of" and r.get("direction") == "backward" and r.get("area")), None)
            if not parent:
                break
            if parent.get("type") in ("Subdivision", "Country"):
                parts.append(parent.get("name", ""))
            cur = parent
        label = ", ".join(dict.fromkeys(p for p in parts if p))
        if self.cache is not None:
            self.cache.set("mb_area", key, label)
        return label


def _ids_in(urls: list[str]) -> set[str]:
    ids = set()
    for u in urls:
        m = re.search(r"deezer\.com/(?:\w+/)?artist/(\d+)", u)
        if m:
            ids.add(f"deezer:{m.group(1)}")
        m = re.search(r"open\.spotify\.com/artist/(\w+)", u)
        if m:
            ids.add(f"spotify:{m.group(1)}")
    return ids


class Locator:
    def __init__(self, cfg, cache, llm, geo, web=None, mb: MusicBrainz | None = None, log=print):
        self.cfg, self.cache, self.llm, self.geo, self.web, self.log = cfg, cache, llm, geo, web, log
        self.mb = mb or MusicBrainz(cfg.mb_contact, cache)

    def cities(self, event) -> list[str]:
        return list(dict.fromkeys(([event.city] if getattr(event, "city", "") else []) + list(self.cfg.local_cities)))

    def mb_entries(self, name: str) -> list[dict]:
        key = normalize_name(name)
        hit = self.cache.get("mb2", key)
        if hit is None:
            hit = self.mb.lookup(name)
            self.cache.set("mb2", key, hit)
        return hit

    def annotate(self, name: str, cands: list[Candidate], event, handle: str | None = None):
        """Fill location lines, places, distance and tier for each candidate."""
        web_on = bool(self.web and self.web.on)
        need = []
        for c in cands:
            ev = self.cache.get("evidence", c.cid)
            if ev is not None and (ev.get("web") or not web_on):
                c.location, c.places = ev["lines"], ev["places"]
                c.spotify_id = c.spotify_id or ev.get("spotify_id")
            else:
                need.append(c)
        if need:
            self._gather(name, cands, need, event, handle, web_on)
        for c in cands:
            self._distance(c, event)

    def _gather(self, name, cands, need, event, handle, web_on):
        entries = self.mb_entries(name)
        lines = {c.cid: [] for c in need}
        places = {c.cid: [] for c in need}
        for c in need:
            for e in entries:
                where = "; ".join(e["places"]) or "no area listed"
                dis = f" ({e['disambiguation']})" if e["disambiguation"] else ""
                if c.cid in _ids_in(e["urls"]):
                    lines[c.cid].append(f"MusicBrainz{dis}: {where} [linked to this artist]")
                    places[c.cid] += [{"place": p, "source": "musicbrainz", "tied": True} for p in e["places"]]
                    for u in e["urls"]:
                        m = re.search(r"open\.spotify\.com/artist/(\w+)", u)
                        if m and not c.spotify_id:
                            c.spotify_id = m.group(1)
                elif not any(x.cid in _ids_in(e["urls"]) for x in cands):
                    lines[c.cid].append(f"MusicBrainz{dis}: {where} [same name only]")
        # Web evidence only where it can change something: same-name conflicts, or no tied place yet.
        want_web = web_on and self.llm is not None and (len(cands) > 1 or not any(places[c.cid] for c in need))
        web_ok = False
        if want_web:
            snippets = self.web.location_snippets(name, handle, self._region_words(event))
            if snippets and self.web.on:
                try:
                    got = self._attribute(name, need, entries, snippets, handle)
                    rows = got.get("results", []) if isinstance(got, dict) else got  # models sometimes return the bare list
                    for r in rows if isinstance(rows, list) else []:
                        if not isinstance(r, dict):
                            continue
                        cid = r.get("cid")
                        if cid not in lines:
                            continue
                        if r.get("location") or r.get("evidence"):
                            lines[cid].append(f"web: {r.get('location') or 'location unknown'} - {r.get('evidence', '')} {r.get('url', '')}".strip())
                        if r.get("location"):
                            places[cid].append({"place": r["location"], "source": "web", "tied": bool(r.get("same_band"))})
                    web_ok = True
                except BillingError:
                    raise
                except Exception as e:  # best effort
                    self.log(f"  ! couldn't read location evidence for {name}: {e}")
            elif self.web.on:
                web_ok = True  # searched, nothing relevant: that's an answer worth caching
        for c in need:
            c.location, c.places = lines[c.cid], places[c.cid]
            if web_ok or not want_web:
                self.cache.set("evidence", c.cid, {"lines": c.location, "places": c.places,
                                                   "spotify_id": c.spotify_id, "web": web_ok})

    def _attribute(self, name, cands, entries, snippets, handle) -> dict:
        ctx = {
            "band_name": name, "instagram_handle_on_poster": handle,
            "candidates": [{"cid": c.cid, "releases": c.releases[:6], "songs": c.songs[:6], "genres": c.genres[:4],
                            "deezer_fans": c.fans} for c in cands],
            "musicbrainz_entries": [{"places": e["places"], "disambiguation": e["disambiguation"],
                                     "tags": e["tags"], "links": e["urls"][:6]} for e in entries],
            "web_snippets": snippets,
        }
        return self.llm.ask_json(ATTRIBUTE_SYSTEM, json.dumps(ctx, ensure_ascii=False), max_tokens=1200, fast=True)

    def _region_words(self, event) -> list[str]:
        words = []
        for c in self.cities(event):
            parts = [p.strip() for p in c.split(",") if p.strip()]
            words += parts[:1] + [STATES.get(p.upper(), p) for p in parts[1:2]]
        return list(dict.fromkeys(w for w in words if w))

    def _distance(self, c: Candidate, event):
        c.distance_km, c.nearest = None, ""
        cities = self.cities(event)
        for p in c.places:
            if not p.get("tied"):
                continue
            hit = self.geo.nearest(p["place"], cities)
            if hit and (c.distance_km is None or hit[0] < c.distance_km):
                c.distance_km, c.nearest = round(hit[0]), hit[1]


STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
          "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
          "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
          "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
          "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
          "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
          "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
          "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
          "WI": "Wisconsin", "WY": "Wyoming"}
