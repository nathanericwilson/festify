"""Steps 2-4: find candidates, gather location evidence, pick (Claude or --ask), then pick songs."""
from __future__ import annotations

from .models import ArtistResult, Candidate, SongPick
from .names import names_match, normalize_name
from .songs import pick_songs

NONE_LABEL = "none of these"


def spotify_url(sid: str | None) -> str:
    return f"https://open.spotify.com/artist/{sid}" if sid else ""


class Resolver:
    def __init__(self, cfg, cache, deezer, lastfm, spotify, locator, judge, n_tracks=3, ask=False,
                 input_fn=input, log=print, linker=None):
        self.cfg, self.cache = cfg, cache
        self.linker = linker  # finds a band's own Spotify link via its tagged Instagram handle
        self.deezer, self.lastfm, self.sp = deezer, lastfm, spotify
        self.locator, self.judge = locator, judge
        self.n, self.ask, self.input, self.log = n_tracks, ask, input_fn, log
        self._run: dict[str, ArtistResult] = {}

    # ---- candidates ------------------------------------------------------
    def candidates(self, name: str) -> tuple[list[Candidate], dict | None]:
        cands: list[Candidate] = []
        lf = self.lastfm.artist_info(name) if self.lastfm.available else None
        lf_ctx = None
        if lf:
            lf_ctx = {**lf, "top_songs": [t["title"] for t in self.lastfm.top_tracks(name, 6)]}
        for a in self.deezer.exact_artists(name)[:6]:
            albums = [x["title"] for x in self.deezer.albums(a["id"], 6)]
            songs = [t.get("title_short") or t["title"] for t in self.deezer.top_tracks(a["id"], 6)]
            cands.append(Candidate(f"deezer:{a['id']}", "deezer", a["name"], a.get("link", ""),
                                   fans=a.get("nb_fan"), releases=albums, songs=songs))
        if not cands and lf:
            cands.append(Candidate(f"lastfm:{normalize_name(name)}", "lastfm", lf["name"], lf["url"],
                                   listeners=lf["listeners"], genres=lf["tags"][:5], songs=lf_ctx["top_songs"]))
        if not cands:  # Spotify is the last resort for identity
            for a in self.sp.search_artists(name):
                if names_match(a.get("name", ""), name):
                    albums = [x["name"] for x in self.sp.artist_albums(a["id"], 6)]
                    songs = [t["name"] for t in self.sp.search_tracks(f'artist:"{name}"')
                             if any(x["id"] == a["id"] for x in t["artists"])][:6]
                    cands.append(Candidate(f"spotify:{a['id']}", "spotify", a["name"], spotify_url(a["id"]),
                                           genres=a.get("genres", []), releases=albums, songs=songs,
                                           spotify_id=a["id"]))
        return cands, lf_ctx

    # ---- manual picker ---------------------------------------------------
    def _spotify_id_for(self, c: Candidate) -> str | None:
        """Best-effort Spotify artist ID so the user can open the About tab."""
        if c.spotify_id:
            return c.spotify_id
        if c.source == "deezer":
            for t in self.deezer.top_tracks(c.native_id, 3):
                isrc = self.deezer.track(t["id"]).get("isrc")
                for tr in (self.sp.track_by_isrc(isrc) if isrc else []):
                    for a in tr["artists"]:
                        if names_match(a["name"], c.name):
                            c.spotify_id = a["id"]
                            return a["id"]
        return None

    def _prompt(self, name: str, cands: list[Candidate], verdict: dict) -> Candidate | None:
        print(f"\n=== Which '{name}'? ({len(cands)} candidates) ===")
        for i, c in enumerate(cands, 1):
            sid = self._spotify_id_for(c)
            print(f"\n[{i}] {c.name}  ({c.source}) " + ("<- suggested" if verdict.get("pick") == c.cid else ""))
            print(f"    genres:   {', '.join(c.genres) or '-'}")
            print(f"    releases: {', '.join(c.releases[:5]) or '-'}")
            print(f"    songs:    {', '.join(c.songs[:5]) or '-'}")
            if c.fans is not None:
                print(f"    Deezer fans: {c.fans}")
            for line in c.location or ["no location evidence found"]:
                print(f"    location: {line}")
            print(f"    Spotify:  {spotify_url(sid) or '(not found; search: https://open.spotify.com/search/' + name.replace(' ', '%20') + ')'}")
            print(f"    Deezer/other: {c.url}")
        print(f"\nSuggestion: {verdict.get('reason', '')}")
        while True:
            ans = self.input(f"Pick 1-{len(cands)}, 0 = {NONE_LABEL}, Enter = suggestion: ").strip()
            if ans == "" and verdict.get("pick"):
                return next(c for c in cands if c.cid == verdict["pick"])
            if ans == "0" or (ans == "" and not verdict.get("pick")):
                return None
            if ans.isdigit() and 1 <= int(ans) <= len(cands):
                return cands[int(ans) - 1]

    # ---- main ------------------------------------------------------------
    def resolve(self, name: str, event) -> ArtistResult:
        key = normalize_name(name)
        if key in self._run:
            return self._run[key]
        entry = self.cache.get("artists", key)
        handle = (getattr(event, "handle_map", None) or {}).get(key, {}).get("handle")
        if handle and self.linker and not (entry and (entry.get("by") == "manual" or entry.get("via_handle") == handle)):
            linked = self._from_handle(name, key, handle)
            if linked:
                self._run[key] = linked
                return linked
        if entry and not (self.ask and entry.get("ambiguous") and entry.get("by") != "manual"):
            res = self._from_entry(name, entry)
        else:
            res = self._fresh(name, key, event)
        self._run[key] = res
        return res

    def _from_handle(self, name, key, handle) -> ArtistResult | None:
        """The band's own Instagram links to its Spotify: that beats any name-based guess."""
        hit = self.linker.find(handle)
        if not hit:
            return None
        art = self.sp.get_artist(hit["spotify_id"])
        if not art:
            return None
        a, b = normalize_name(art.get("name", "")), normalize_name(name)
        if not (names_match(art.get("name", ""), name) or (a and b and (a in b or b in a))):
            self.log(f"  ! @{handle} links to Spotify artist '{art.get('name')}', not '{name}'; ignoring that link")
            return None
        sid = art["id"]
        entry = {
            "choice": {"cid": f"spotify:{sid}", "source": "spotify", "name": art.get("name", name),
                       "url": spotify_url(sid), "spotify_id": sid, "pinned": True},
            "by": "instagram", "via_handle": handle, "ambiguous": False, "confidence": "high",
            "reason": f"Spotify link from @{handle} ({hit['via']})", "local_evidence": "yes",
            "nonlocal_suspected": False, "evidence": [], "n_candidates": 1,
        }
        self.cache.set("artists", key, entry)
        return self._from_entry(name, entry)

    def _fresh(self, name, key, event) -> ArtistResult:
        cands, lf_ctx = self.candidates(name)
        if not cands:
            self.cache.set("artists", key, {"choice": None, "by": "none", "ambiguous": False,
                                           "reason": "no match on Deezer, Last.fm or Spotify"})
            return ArtistResult(name, "not_found", "no match on Deezer, Last.fm or Spotify")
        self.locator.annotate(name, cands, event)
        verdict = self.judge.pick(name, cands, lf_ctx, event)
        ambiguous = len(cands) > 1
        failed = bool(verdict.get("judge_failed"))
        if failed and ambiguous and not self.ask:
            return ArtistResult(name, "not_found", f"could not decide between {len(cands)} same-name artists "
                                f"({verdict.get('reason')}); not cached, re-run to retry", n_candidates=len(cands))
        by = "single" if not ambiguous else "claude"
        if ambiguous and self.ask:
            chosen, by = self._prompt(name, cands, verdict), "manual"
            verdict = {**verdict, "pick": chosen.cid if chosen else None,
                       "reason": "chosen manually", "nonlocal_suspected": False}
            if chosen:
                verdict["local_evidence"] = "yes" if any("web:" in l or "MusicBrainz" in l for l in chosen.location) else "unknown"
        chosen = next((c for c in cands if c.cid == verdict.get("pick")), None)
        if not ambiguous and chosen is None:
            chosen = cands[0]  # single exact match: use it, flag if suspicious
            verdict["nonlocal_suspected"] = verdict.get("nonlocal_suspected", False)
        entry = {
            "choice": chosen.to_choice() if chosen else None, "by": by, "ambiguous": ambiguous,
            "reason": verdict.get("reason", ""), "confidence": verdict.get("confidence", ""),
            "local_evidence": verdict.get("local_evidence", "unknown"),
            "nonlocal_suspected": bool(verdict.get("nonlocal_suspected")),
            "evidence": chosen.location if chosen else [], "n_candidates": len(cands),
            "candidates": [{"name": c.name, "url": c.url, "fans": c.fans, "listeners": c.listeners,
                            "where": (c.location[:2] if c.location else [])} for c in cands] if ambiguous else [],
        }
        if not failed:
            self.cache.set("artists", key, entry)
        return self._from_entry(name, entry)

    def _from_entry(self, name, entry) -> ArtistResult:
        key = normalize_name(name)
        ch = entry.get("choice")
        base = dict(reason=entry.get("reason", ""), evidence=entry.get("evidence", []),
                    nonlocal_flag=entry.get("nonlocal_suspected", False),
                    local_evidence=entry.get("local_evidence", "unknown"),
                    n_candidates=entry.get("n_candidates", 1 if ch else 0))
        if not ch:
            return ArtistResult(name, "not_found", entry.get("reason") or "no plausible candidate", **{
                k: v for k, v in base.items() if k != "reason"})
        if entry.get("ambiguous") and entry.get("by") == "claude" and entry.get("confidence") != "high":
            # Not sure enough to guess between same-name artists: ask the user instead of adding wrong songs.
            return ArtistResult(name, "needs_review", entry.get("reason", ""), chosen=f"{ch['name']} [{ch['source']}] {ch.get('url', '')}".strip(),
                                candidates=entry.get("candidates", []), **{k: v for k, v in base.items() if k != "reason"})
        outcome = "manual" if entry.get("by") == "manual" else ("picked" if entry.get("ambiguous") else "matched")
        res = ArtistResult(name, outcome, chosen=f"{ch['name']} [{ch['source']}] {ch.get('url', '')}".strip(), **base)
        cached = entry.get("songs")
        if cached and len(cached["items"]) >= self.n:  # reuse earlier matches: saves Spotify calls on re-runs
            res.songs = [SongPick(**s) for s in cached["items"][: self.n]]
            res.song_sources = cached["sources"]
            sr_id = cached.get("spotify_artist_id")
            notes = []
        else:
            sr = pick_songs(self.sp, self.deezer, self.lastfm, name, ch, self.n,
                            strict=bool(entry.get("ambiguous")) and not ch.get("pinned"),
                            confident=entry.get("confidence") == "high" or entry.get("by") == "manual")
            res.songs, res.song_sources, sr_id, notes = sr.songs, sr.sources, sr.spotify_artist_id, sr.notes
            if res.songs and self.cache.get("artists", key) is not None:
                self.cache.set("artists", key, {**entry, "songs": {
                    "items": [s.__dict__ for s in res.songs], "sources": res.song_sources, "spotify_artist_id": sr_id}})
        sid = sr_id or ch.get("spotify_id")
        res.spotify_link = spotify_url(sid)
        if sid and not ch.get("spotify_id") and self.cache.get("artists", key) is not None:
            ch["spotify_id"] = sid
            self.cache.set("artists", key, {**self.cache.get("artists", key), "choice": ch})
        if not res.songs:
            res.outcome, res.reason = "not_found", "; ".join(notes) or "no songs found on Spotify"
        return res
