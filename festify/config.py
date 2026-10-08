from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REDIRECT_URI = "http://127.0.0.1:8888/callback"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CITIES = "Provo, UT; Orem, UT; Salt Lake City, UT; Ogden, UT"


@dataclass
class Config:
    spotify_id: str = ""
    spotify_secret: str = ""
    gemini_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    anthropic_key: str = ""  # optional paid alternative to Gemini (use --claude)
    model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-4-5-20251001"
    lastfm_key: str = ""
    tavily_key: str = ""
    ig_username: str = ""
    local_cities: list[str] = field(default_factory=lambda: [c.strip() for c in DEFAULT_CITIES.split(";")])
    mb_contact: str = "festify-personal-project"
    redirect_uri: str = REDIRECT_URI
    cache_path: Path = ROOT / ".festify_cache.json"
    token_cache: Path = ROOT / ".spotify_token_cache"

    @property
    def home_region(self) -> str:  # human-readable, for prompts
        return "; ".join(self.local_cities)


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    g = lambda k, d="": (os.environ.get(k) or d).strip()
    cities = [c.strip() for c in g("LOCAL_CITIES", g("HOME_REGION", DEFAULT_CITIES)).split(";") if c.strip()]
    return Config(
        spotify_id=g("SPOTIFY_CLIENT_ID"),
        spotify_secret=g("SPOTIFY_CLIENT_SECRET"),
        gemini_key=g("GEMINI_API_KEY"),
        gemini_model=g("GEMINI_MODEL", "gemini-3.8-flash"),
        anthropic_key=g("ANTHROPIC_API_KEY"),
        model=g("FESTIFY_MODEL", "claude-sonnet-5-5"),
        fast_model=g("FESTIFY_FAST_MODEL", "claude-haiku-4-5-20251001"),
        lastfm_key=g("LASTFM_API_KEY"),
        tavily_key=g("TAVILY_API_KEY"),
        ig_username=g("IG_USERNAME"),
        local_cities=cities,
        mb_contact=g("MUSICBRAINZ_CONTACT", "festify-personal-project"),
    )
