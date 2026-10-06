"""Name normalization and matching, plus track-title dedupe helpers."""
from __future__ import annotations

import re
import unicodedata


def normalize_name(s: str) -> str:
    """Case/accent/punctuation-insensitive form: 'P.S. Destroy This' -> 'ps destroy this'."""
    raw = (s or "").strip()
    t = unicodedata.normalize("NFKD", raw)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[.'’`]", "", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^the\s+", "", t)
    return t or raw.lower()


def names_match(a: str, b: str) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    return na == nb or na.replace(" ", "") == nb.replace(" ", "")


_VERSION = (
    r"remaster(?:ed)?|live|acoustic|demo|remix|mix|version|mono|stereo|radio edit|edit|"
    r"instrumental|deluxe|reprise|sped up|slowed|session|unplugged|bonus|extended|"
    r"original|anniversary|re-?recorded|clean|explicit|single"
)
_GROUP_RE = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
_DASH_RE = re.compile(r"\s+[-–—]\s+(.*)$")
_FEAT_RE = re.compile(r"\b(?:feat|ft|featuring)\b\.?", re.I)
_VERSION_RE = re.compile(rf"\b(?:{_VERSION})\b", re.I)


def base_title(title: str) -> str:
    """Strip version/feature decorations so remasters, live cuts etc. collapse to one key."""
    t = title or ""
    t = _GROUP_RE.sub(lambda m: "" if (_VERSION_RE.search(m.group(1)) or _FEAT_RE.search(m.group(1))) else m.group(0), t)
    m = _DASH_RE.search(t)
    if m and (_VERSION_RE.search(m.group(1)) or _FEAT_RE.search(m.group(1))):
        t = t[: m.start()]
    t = _FEAT_RE.split(t)[0]
    return normalize_name(t)


def is_alt_version(title: str) -> bool:
    t = title or ""
    for m in _GROUP_RE.finditer(t):
        if _VERSION_RE.search(m.group(1)):
            return True
    m = _DASH_RE.search(t)
    return bool(m and _VERSION_RE.search(m.group(1)))


def dedupe_tracks(items: list, key=lambda x: x) -> list:
    """Keep one entry per base title, in first-seen order, preferring a non-alt version."""
    order: list[str] = []
    best: dict[str, object] = {}
    for it in items:
        title = key(it)
        b = base_title(title)
        if b not in best:
            best[b] = it
            order.append(b)
        elif is_alt_version(key(best[b])) and not is_alt_version(title):
            best[b] = it
    return [best[b] for b in order]
