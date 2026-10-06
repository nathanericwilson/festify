from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    cid: str  # "deezer:123" | "lastfm:<name>" | "spotify:<id>"
    source: str  # deezer | lastfm | spotify
    name: str
    url: str = ""
    fans: int | None = None  # Deezer nb_fan
    listeners: int | None = None  # Last.fm
    genres: list[str] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    songs: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)
    spotify_id: str | None = None

    @property
    def native_id(self) -> str:
        return self.cid.split(":", 1)[1]

    def to_choice(self) -> dict:
        return {"cid": self.cid, "source": self.source, "name": self.name, "url": self.url,
                "spotify_id": self.spotify_id}

    @staticmethod
    def from_choice(c: dict) -> "Candidate":
        return Candidate(cid=c["cid"], source=c["source"], name=c.get("name", ""), url=c.get("url", ""),
                         spotify_id=c.get("spotify_id"))


@dataclass
class SongPick:
    title: str
    uri: str
    via: str  # isrc | title-search | lastfm-title | spotify-search | spotify-albums
    source: str  # Deezer | Last.fm | Spotify


@dataclass
class ArtistResult:
    name: str
    outcome: str  # matched | picked | manual | not_found
    reason: str = ""
    nonlocal_flag: bool = False
    local_evidence: str = "unknown"  # yes | no | unknown
    evidence: list[str] = field(default_factory=list)
    chosen: str = ""  # human label of chosen candidate
    spotify_link: str = ""
    songs: list[SongPick] = field(default_factory=list)
    song_sources: list[str] = field(default_factory=list)
    n_candidates: int = 0

    @property
    def has_songs(self) -> bool:
        return bool(self.songs)
