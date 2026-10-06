"""Step 5 (with the Deezer/Last.fm override): choose top songs and find them on Spotify."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import SongPick
from .names import base_title, dedupe_tracks, names_match


@dataclass
class SongResult:
    songs: list[SongPick] = field(default_factory=list)
    spotify_artist_id: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        out = []
        for s in self.songs:
            label = f"{s.source} ({'ISRC' if s.via == 'isrc' else 'title search' if 'title' in s.via else s.via})"
            if label not in out:
                out.append(label)
        return out


def credited(track: dict, name: str) -> dict | None:
    for a in track["artists"]:
        if names_match(a["name"], name):
            return a
    return None


class _Picker:
    def __init__(self, n: int, name: str):
        self.n, self.name = n, name
        self.res = SongResult()
        self.uris: set[str] = set()
        self.bases: set[str] = set()

    @property
    def full(self) -> bool:
        return len(self.res.songs) >= self.n

    def add(self, track: dict, via: str, source: str, require_credit: bool = True) -> bool:
        a = credited(track, self.name)
        if require_credit and not a:
            return False
        b = base_title(track["name"])
        if track["uri"] in self.uris or b in self.bases:
            return False
        self.uris.add(track["uri"])
        self.bases.add(b)
        if a and not self.res.spotify_artist_id:
            self.res.spotify_artist_id = a["id"]
        self.res.songs.append(SongPick(track["name"], track["uri"], via, source))
        return True


def _find_on_spotify(sp, pick: _Picker, title: str, isrc: str | None, source: str, title_via: str):
    if isrc:
        hits = sp.track_by_isrc(isrc)
        if hits:
            best = next((t for t in hits if credited(t, pick.name)), None)
            # An ISRC identifies the recording, so accept it even if the credit is oddly spelled.
            if pick.add(best or hits[0], "isrc", source, require_credit=False):
                return True
    q = f'track:"{title}" artist:"{pick.name}"'
    for t in sp.search_tracks(q):
        if pick.add(t, title_via, source):
            return True
    return False


def pick_songs(sp, deezer, lastfm, name: str, choice: dict, n: int = 3) -> SongResult:
    pick = _Picker(n, name)
    source = choice.get("source")
    known_sp = choice.get("spotify_id")
    if known_sp:
        pick.res.spotify_artist_id = known_sp

    # 1) Deezer top tracks -> ISRC -> Spotify
    pinned = bool(choice.get("pinned"))
    if source == "deezer" and not pinned:
        tops = dedupe_tracks(deezer.top_tracks(choice["cid"].split(":", 1)[1], 25), key=lambda t: t["title"])
        for t in tops:
            if pick.full:
                break
            isrc = deezer.track(t["id"]).get("isrc")
            _find_on_spotify(sp, pick, t["title"], isrc, "Deezer", "title-search")
        if not tops:
            pick.res.notes.append("Deezer has no top tracks for this artist")

    # 2) Last.fm top tracks (title + artist search on Spotify)
    if not pick.full and not pinned and lastfm and lastfm.available and (source == "lastfm" or not pick.res.songs):
        tops = dedupe_tracks(lastfm.top_tracks(name, 25), key=lambda t: t["title"])
        for t in tops:
            if pick.full:
                break
            _find_on_spotify(sp, pick, t["title"], None, "Last.fm", "lastfm-title")

    # 3) Spotify-only fallback (Step 5): artist-name track search filtered by artist ID, then albums
    if not pick.full:
        sid = pick.res.spotify_artist_id or (choice.get("spotify_id") if source == "spotify" else None)
        if not sid:
            exact = [a for a in sp.search_artists(name) if names_match(a.get("name", ""), name)]
            if len(exact) == 1:
                sid = exact[0]["id"]
            elif len(exact) > 1:
                pick.res.notes.append("several same-name artists on Spotify; couldn't pick one for fallback")
        if sid:
            pick.res.spotify_artist_id = sid
            _spotify_only(sp, pick, sid)
    return pick.res


def _spotify_only(sp, pick: _Picker, sid: str):
    found = []
    for off in (0, 10, 20):
        batch = sp.search_tracks(f'artist:"{pick.name}"', offset=off)
        if not batch:
            break
        found += [t for t in batch if any(a["id"] == sid for a in t["artists"])]
    for t in dedupe_tracks(found, key=lambda t: t["name"]):
        if pick.full:
            return
        pick.add(t, "spotify-search", "Spotify", require_credit=False)
    if pick.full:
        return
    for alb in sp.artist_albums(sid):
        for t in sp.album_tracks(alb["id"], alb.get("name", "")):
            if pick.full:
                return
            if any(a["id"] == sid for a in t["artists"]):
                pick.add(t, "spotify-albums", "Spotify", require_credit=False)
