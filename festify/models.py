from __future__ import annotations

from dataclasses import asdict, dataclass, field

LOCAL_KM, REGIONAL_KM = 80, 400  # distance tiers from the nearest relevant city


def tier_for(km: float | None) -> str:
    if km is None:
        return "unknown"
    return "local" if km <= LOCAL_KM else "regional" if km <= REGIONAL_KM else "far"


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
    location: list[str] = field(default_factory=list)  # human-readable evidence lines
    places: list[dict] = field(default_factory=list)  # {"place", "source", "tied"} where this artist is from/based
    spotify_id: str | None = None
    distance_km: float | None = None  # to the nearest relevant city (tied places only)
    nearest: str = ""  # which relevant city that was

    @property
    def native_id(self) -> str:
        return self.cid.split(":", 1)[1]

    @property
    def tier(self) -> str:
        return tier_for(self.distance_km)

    def to_choice(self) -> dict:
        return {"cid": self.cid, "source": self.source, "name": self.name, "url": self.url,
                "spotify_id": self.spotify_id}

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Candidate":
        return Candidate(**{k: v for k, v in d.items() if k in Candidate.__dataclass_fields__})


@dataclass
class SongPick:
    title: str
    uri: str
    via: str  # isrc | title-search | lastfm-title | spotify-search | spotify-albums
    source: str  # Deezer | Last.fm | Spotify
    artist_label: str = ""  # which artist this song came from (matters when several same-name artists are included)


@dataclass
class ArtistResult:
    name: str
    outcome: str  # single | multi | none
    reason: str = ""
    warnings: list[str] = field(default_factory=list)  # short notes that also go into the playlist description
    picks: list[dict] = field(default_factory=list)  # [{"label", "url", "spotify_link", "distance_km", "tier"}]
    songs: list[SongPick] = field(default_factory=list)
    song_sources: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # summaries of every same-name candidate considered
    by: str = ""  # pin | instagram | websearch | rules | judge | cache

    @property
    def has_songs(self) -> bool:
        return bool(self.songs)

    @property
    def unsure(self) -> bool:
        return any(w.startswith("unsure") for w in self.warnings)
