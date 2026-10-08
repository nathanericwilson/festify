"""Turn a band name from a poster into Spotify artist(s) + songs, without ever asking the user.

Order of trust:
  1. your pin (--pin / the app's "fix a band")
  2. the band's own link: Instagram bio / Linktree (identity.py)
  3. candidates from Deezer, Last.fm and Spotify, placed on the map (location.py + geo.py), then the rules
     in decide() with the LLM judge as tie-breaker
  4. if still unsure: web search for the band's own Spotify link (websearch.py)
Uncertainty becomes warnings (shown in the report and the playlist description), never a question.
"""
from __future__ import annotations

from .models import ArtistResult, Candidate, SongPick
from .names import names_match, normalize_name
from .songs import pick_songs

ENTRY_VERSION = 2


def spotify_url(sid: str | None) -> str:
    return f"https://open.spotify.com/artist/{sid}" if sid else ""


def pin_entry(name: str, sid: str, by: str = "pin", reason: str = "pinned by you") -> dict:
    return {"v": ENTRY_VERSION, "kind": "single", "by": by, "reason": reason, "warnings": [],
            "choices": [{"choice": {"cid": f"spotify:{sid}", "source": "spotify", "name": name,
                                    "url": spotify_url(sid), "spotify_id": sid, "pinned": True}, "n": None}],
            "candidates": []}


def song_counts(n: int, k: int) -> list[int]:
    """Songs per included artist when several same-name artists are included: e.g. 3/2/1."""
    return [max(1, n - i) for i in range(k)]


def decide(name: str, cands: list[Candidate], verdict: dict, n: int) -> tuple[list[tuple[Candidate, int]], list[str], str, bool]:
    """Pure decision rules. Returns (choices with song counts, warnings, kind, confident)."""
    by_id = {c.cid: c for c in cands}
    pick = by_id.get(verdict.get("pick"))
    conf = verdict.get("confidence", "low")
    plausible = [by_id[x] for x in verdict.get("plausible", []) if x in by_id]
    if pick and pick not in plausible:
        plausible.insert(0, pick)

    def far_note(c):
        return f"unsure: ~{c.distance_km:,} km from {c.nearest.split(',')[0]}"

    if len(cands) == 1:  # unique exact match: keep it, but say so if it's clearly from elsewhere
        c = cands[0]
        notes = [far_note(c)] if c.tier == "far" else (["unsure: only same-name match, location unknown"]
                                                        if c.tier == "unknown" else [])
        return [(c, n)], notes, "single", c.tier in ("local", "regional")
    local = [c for c in cands if c.tier == "local"]
    if len(local) == 1:
        return [(local[0], n)], [], "single", True
    if pick and conf == "high" and pick.tier != "far":
        return [(pick, n)], [], "single", True
    near = [c for c in plausible if c.tier in ("local", "regional")]
    pool = near[:3] if near else [c for c in plausible if c.tier == "unknown"][:2]
    if len(pool) == 1:
        return [(pool[0], n)], ([] if conf == "high" else ["unsure: same-name artists, best guess"]), "single", conf == "high"
    if len(pool) > 1:
        return list(zip(pool, song_counts(n, len(pool)))), [f"multi: {len(pool)} possible matches"], "multi", False
    if pick and pick.tier == "far" and conf == "high":
        return [(pick, n)], [far_note(pick)], "single", False
    return [], ["not found nearby"], "none", False


class Resolver:
    def __init__(self, cfg, cache, deezer, lastfm, spotify, locator, judge, n_tracks=3, log=print,
                 linker=None, web=None, ignore_pins=False):
        self.cfg, self.cache = cfg, cache
        self.deezer, self.lastfm, self.sp = deezer, lastfm, spotify
        self.locator, self.judge, self.linker, self.web = locator, judge, linker, web
        self.n, self.log, self.ignore_pins = n_tracks, log, ignore_pins
        self._run: dict[str, ArtistResult] = {}

    # ---- candidates --------------------------------------------------------------
    def candidates(self, name: str) -> tuple[list[Candidate], dict | None]:
        key = normalize_name(name)
        hit = self.cache.get("cands", key)
        if hit is not None:
            return [Candidate.from_dict(d) for d in hit["cands"]], hit.get("lastfm")
        lf = self.lastfm.artist_info(name) if self.lastfm and self.lastfm.available else None
        lf_ctx = {**lf, "top_songs": [t["title"] for t in self.lastfm.top_tracks(name, 6)]} if lf else None
        cands: list[Candidate] = []
        for a in self.deezer.exact_artists(name)[:6]:
            cands.append(Candidate(f"deezer:{a['id']}", "deezer", a["name"], a.get("link", ""), fans=a.get("nb_fan"),
                                   releases=[x["title"] for x in self.deezer.albums(a["id"], 6)],
                                   songs=[t.get("title_short") or t["title"] for t in self.deezer.top_tracks(a["id"], 6)]))
        cands += self._spotify_only_candidates(name, cands)
        if not cands and lf:
            cands.append(Candidate(f"lastfm:{normalize_name(name)}", "lastfm", lf["name"], lf["url"],
                                   listeners=lf["listeners"], genres=lf["tags"][:5], songs=lf_ctx["top_songs"]))
        self.cache.set("cands", key, {"cands": [c.to_dict() for c in cands], "lastfm": lf_ctx})
        return cands, lf_ctx

    def _spotify_only_candidates(self, name: str, deezer_cands: list[Candidate]) -> list[Candidate]:
        """Same-name Spotify artists that aren't already one of the Deezer candidates (judged by shared releases)."""
        out = []
        for a in [a for a in self.sp.search_artists(name, pages=1) if names_match(a.get("name", ""), name)][:4]:
            releases = [x["name"] for x in self.sp.artist_albums(a["id"], 6)]
            rel = {normalize_name(r) for r in releases}
            twin = next((c for c in deezer_cands if rel & {normalize_name(r) for r in c.releases}), None)
            if twin:
                twin.spotify_id = twin.spotify_id or a["id"]
                twin.genres = twin.genres or a.get("genres", [])
                continue
            out.append(Candidate(f"spotify:{a['id']}", "spotify", a["name"], spotify_url(a["id"]),
                                 genres=a.get("genres", []), releases=releases, spotify_id=a["id"]))
        return out

    # ---- main ----------------------------------------------------------------------
    def resolve(self, name: str, event, songs: bool = True, fresh: bool = False) -> ArtistResult:
        key = normalize_name(name)
        if key in self._run and not fresh:
            return self._run[key]
        handle = (getattr(event, "handle_map", None) or {}).get(key, {}).get("handle")
        entry = self.cache.get("artists", key)
        if entry and entry.get("v") != ENTRY_VERSION:
            entry = self._migrate(name, entry)
        if entry and entry.get("by") == "pin" and self.ignore_pins:
            entry = None
        reuse = entry and not fresh and (entry.get("by") in ("pin", "instagram", "websearch")
                                         or entry.get("handle") == handle)
        if not reuse:
            entry = self._decide_entry(name, key, event, handle, entry)
        res = self._result(name, key, entry, songs)
        self._run[key] = res
        return res

    def _migrate(self, name: str, old: dict) -> dict | None:
        ch = old.get("choice")
        if old.get("by") in ("manual", "instagram") and ch and ch.get("spotify_id"):
            e = pin_entry(name, ch["spotify_id"], by="pin" if old["by"] == "manual" else "instagram",
                          reason=old.get("reason", "pinned by you"))
            e["handle"] = old.get("via_handle")
            self.cache.set("artists", normalize_name(name), e)
            return e
        return None  # anything else is re-decided with the new rules

    def _decide_entry(self, name, key, event, handle, old) -> dict:
        if old and old.get("by") == "pin" and not self.ignore_pins:
            return old
        if not handle and self.web and self.web.on:  # untagged band: its handle often shows up in search results
            handle = self.web.find_handle(name, self.locator._region_words(event))
            if handle:
                self.log(f"  {name}: found Instagram @{handle} via web search")
        linked = self._linked(name, handle)
        if linked:
            return self._save(key, linked)
        cands, lf_ctx = self.candidates(name)
        if not cands:
            web = self._web_identity(name, handle, event)
            return self._save(key, web or {"v": ENTRY_VERSION, "kind": "none", "by": "rules", "warnings": ["not on Spotify"],
                                            "reason": "no match on Deezer, Last.fm or Spotify", "choices": [],
                                            "candidates": [], "handle": handle})
        self.locator.annotate(name, cands, event, handle)
        if len(cands) > 1 or any(c.tier == "far" for c in cands):
            verdict = self.judge.pick(name, cands, lf_ctx, event, handle)
        else:
            verdict = {"pick": cands[0].cid, "confidence": "high", "plausible": [cands[0].cid], "reason": "only match"}
        if verdict.get("failed"):
            self.log(f"  ! {verdict['reason']}")
        choices, warnings, kind, confident = decide(name, cands, verdict, self.n)
        if not confident:
            web = self._web_identity(name, handle, event)
            if web:
                return self._save(key, web)
        entry = {
            "v": ENTRY_VERSION, "kind": kind, "by": "rules", "handle": handle, "warnings": warnings,
            "reason": verdict.get("reason", ""),
            "choices": [{"choice": c.to_choice(), "n": k, "distance_km": c.distance_km, "tier": c.tier,
                         "nearest": c.nearest} for c, k in choices],
            "candidates": [{"name": c.name, "cid": c.cid, "url": c.url, "tier": c.tier, "distance_km": c.distance_km,
                            "nearest": c.nearest, "fans": c.fans} for c in cands],
            "ambiguous": len(cands) > 1, "confident": confident,
        }
        return entry if verdict.get("failed") else self._save(key, entry)

    def _save(self, key, entry):
        self.cache.set("artists", key, entry)
        return entry

    def _verified(self, sid: str, name: str) -> dict | None:
        art = self.sp.get_artist(sid)
        if not art:
            return None
        a, b = normalize_name(art.get("name", "")), normalize_name(name)
        if names_match(art.get("name", ""), name) or (a and b and (a in b or b in a)):
            return art
        self.log(f"  ! a link for '{name}' points to Spotify artist '{art.get('name')}'; ignoring it")
        return None

    def _linked(self, name, handle) -> dict | None:
        if not (handle and self.linker):
            return None
        hit = self.linker.find(handle)
        if hit and not hit.get("spotify_id"):  # only a release was linked: use its artist that has the band's name
            for kind, rid in hit.get("releases", []):
                a = next((a for a in self.sp.release_artists(kind, rid) if names_match(a.get("name", ""), name)), None)
                if a:
                    hit = {**hit, "spotify_id": a["id"]}
                    break
        if hit and hit.get("spotify_id") and self._verified(hit["spotify_id"], name):
            e = pin_entry(name, hit["spotify_id"], by="instagram", reason=f"Spotify link from @{handle} ({hit['via']})")
            e["handle"] = handle
            return e
        return None

    def _web_identity(self, name, handle, event) -> dict | None:
        if not (self.web and self.web.on):
            return None
        for hit in self.web.spotify_links(name, handle, self.locator._region_words(event))[:3]:
            if self._verified(hit["spotify_id"], name):
                e = pin_entry(name, hit["spotify_id"], by="websearch", reason=f"Spotify link via {hit['via']}")
                e["handle"] = handle
                return e
        return None

    # ---- songs -----------------------------------------------------------------------
    def _result(self, name, key, entry, songs: bool) -> ArtistResult:
        res = ArtistResult(name, entry["kind"], entry.get("reason", ""), warnings=list(entry.get("warnings", [])),
                           candidates=entry.get("candidates", []), by=entry.get("by", ""))
        strict = bool(entry.get("ambiguous"))
        dirty = False
        for slot in entry.get("choices", []):
            ch = slot["choice"]
            n = slot.get("n") or self.n
            label = ch.get("name", name) + (f" ({slot['nearest'].split(',')[0]} area)" if slot.get("tier") == "local" and slot.get("nearest") else "")
            pick = {"label": label, "url": ch.get("url", ""), "distance_km": slot.get("distance_km"), "tier": slot.get("tier", "")}
            if songs:
                cached = slot.get("songs")
                if cached and len(cached["items"]) >= n:
                    items, sources, sid = [SongPick(**s) for s in cached["items"][:n]], cached["sources"], cached.get("spotify_artist_id")
                else:
                    sr = pick_songs(self.sp, self.deezer, self.lastfm, name, ch, n,
                                    strict=strict and not ch.get("pinned"), confident=bool(entry.get("confident")))
                    items, sources, sid = sr.songs, sr.sources, sr.spotify_artist_id
                    for s in items:
                        s.artist_label = label
                    if items:
                        slot["songs"] = {"items": [s.__dict__ for s in items], "sources": sources, "spotify_artist_id": sid}
                        dirty = True
                res.songs += items
                res.song_sources += [s for s in sources if s not in res.song_sources]
                pick["spotify_link"] = spotify_url(sid or ch.get("spotify_id"))
            res.picks.append(pick)
        if dirty and self.cache.get("artists", key) is not None:
            self.cache.set("artists", key, entry)
        if songs and entry.get("choices") and not res.songs:
            res.outcome = "none"
            if not any(w == "not on Spotify" for w in res.warnings):
                res.warnings.append("not on Spotify")
        return res
