from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REDIRECT_URI = "http://127.0.0.1:8888/callback"
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    spotify_id: str = ""
    spotify_secret: str = ""
    anthropic_key: str = ""
    lastfm_key: str = ""
    gemini_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    provider: str = "anthropic"  # anthropic | gemini (free)
    ig_username: str = ""
    home_region: str = "Utah, Salt Lake City, Provo"
    model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-4-5-20251001"
    mb_contact: str = "festify-personal-project"
    redirect_uri: str = REDIRECT_URI
    cache_path: Path = ROOT / ".festify_cache.json"
    token_cache: Path = ROOT / ".spotify_token_cache"


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    g = lambda k, d="": (os.environ.get(k) or d).strip()
    return Config(
        spotify_id=g("SPOTIFY_CLIENT_ID"),
        spotify_secret=g("SPOTIFY_CLIENT_SECRET"),
        anthropic_key=g("ANTHROPIC_API_KEY"),
        lastfm_key=g("LASTFM_API_KEY"),
        gemini_key=g("GEMINI_API_KEY"),
        gemini_model=g("GEMINI_MODEL", "gemini-3.8-flash"),
        provider=g("LLM_PROVIDER", "anthropic").lower(),
        ig_username=g("IG_USERNAME"),
        home_region=g("HOME_REGION", "Utah, Salt Lake City, Provo"),
        model=g("FESTIFY_MODEL", "claude-sonnet-5-5"),
        fast_model=g("FESTIFY_FAST_MODEL", "claude-haiku-4-5-20251001"),
        mb_contact=g("MUSICBRAINZ_CONTACT", "festify-personal-project"),
    )
