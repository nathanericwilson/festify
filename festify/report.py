from __future__ import annotations

from .extract import Event
from .models import ArtistResult
from .names import normalize_name

DESC_LIMIT = 300  # Spotify's playlist description limit


def playlist_name(ev: Event) -> str:
    return f"{ev.name} – {ev.date}" if ev.date else ev.name


def _join(names: list[str]) -> str:
    return ", ".join(names)


def playlist_description(ev: Event, results: list[ArtistResult], limit: int = DESC_LIMIT) -> str:
    """Short, prioritized summary of what to double-check. Least important parts are dropped first."""
    unsure, multi, nearby, missing = [], [], [], []
    for r in results:
        far = next((w.split(": ", 1)[1] for w in r.warnings if w.startswith("unsure: ~")), None)
        if r.outcome == "multi":
            multi.append(f"{r.name} ({len(r.picks)})")
        elif r.has_songs and far:
            unsure.append(f"{r.name} ({far})")
        elif r.has_songs and r.unsure:
            unsure.append(r.name)
        elif r.outcome == "none" and "not found nearby" in r.warnings:
            nearby.append(r.name)
        elif not r.has_songs:
            missing.append(r.name)
    parts = [
        ("Maybe another band with the same name: ", unsure),
        ("Songs from several same-name artists: ", multi),
        ("Not found nearby: ", nearby),
        ("Not on Spotify: ", missing),
    ]
    head = "festify"
    tail = f"{len(ev.unreadable)} unreadable logo(s)." if ev.unreadable else ""
    pieces = [f"{label}{_join(names)}." for label, names in parts if names]
    if not pieces and not tail:
        return f"{head}: all {len(results)} artists matched."
    while True:
        text = " ".join([head + ":"] + pieces + ([tail] if tail else []))
        if len(text) <= limit:
            return text
        if tail:
            tail = ""
            continue
        # shorten the longest list first, then drop whole sections from the end
        longest = max(range(len(parts)), key=lambda i: len(parts[i][1]))
        if len(parts[longest][1]) > 1:
            label, names = parts[longest]
            parts[longest] = (label, names[:-1])
            pieces = [f"{l}{_join(n)}{' …' if l == label else ''}." for l, n in parts if n]
        elif pieces:
            pieces.pop()
        else:
            return text[: limit - 1] + "…"


def _tag(ev: Event, r: ArtistResult) -> str:
    h = ev.handle_map.get(normalize_name(r.name))
    return (f" (name read from @{h['handle']})" if h and h.get("inferred") else f" (@{h['handle']})" if h else "")


def _songs(r: ArtistResult) -> str:
    return "; ".join(s.title for s in r.songs) + (f"  <- {', '.join(r.song_sources)}" if r.song_sources else "")


def _where(p: dict) -> str:
    if p.get("distance_km") is None:
        return "location unknown"
    return f"~{p['distance_km']:,} km away ({p['tier']})"


def print_report(ev: Event, results: list[ArtistResult], playlist: str, out=print):
    out("\n" + "=" * 72)
    out(f"{playlist_name(ev)}   [{ev.venue or 'venue ?'}, {ev.city or 'city ?'}]")
    out("=" * 72)
    ok = [r for r in results if r.has_songs and r.outcome == "single" and not r.unsure]
    unsure = [r for r in results if r.has_songs and r.outcome == "single" and r.unsure]
    multi = [r for r in results if r.outcome == "multi" and r.has_songs]
    missing = [r for r in results if not r.has_songs]

    def section(title, rows, fmt):
        if not rows:
            return
        out(f"\n{title} ({len(rows)})")
        for r in rows:
            fmt(r)

    def f_ok(r):
        out(f"  + {r.name}{_tag(ev, r)}: {_songs(r)}")
        how = {"pin": "pinned by you", "instagram": r.reason, "websearch": r.reason}.get(r.by)
        p = r.picks[0] if r.picks else {}
        out(f"      {how or _where(p)}" + (f"  {p.get('spotify_link')}" if p.get("spotify_link") else ""))

    def f_unsure(r):
        out(f"  ? {r.name}{_tag(ev, r)}: {_songs(r)}")
        out(f"      {'; '.join(w.replace('unsure: ', '') for w in r.warnings if w.startswith('unsure'))}")
        if r.reason:
            out(f"      why: {r.reason[:300]}")
        for p in r.picks:
            if p.get("spotify_link"):
                out(f"      check: {p['spotify_link']}")

    def f_multi(r):
        out(f"  * {r.name}{_tag(ev, r)}: songs from {len(r.picks)} same-name artists")
        for p in r.picks:
            songs = [s.title for s in r.songs if s.artist_label == p["label"]]
            out(f"      - {p['label']} ({_where(p)}): {'; '.join(songs) or 'no songs'}  {p.get('spotify_link', '')}")
        if r.reason:
            out(f"      why: {r.reason[:300]}")

    def f_missing(r):
        out(f"  - {r.name}{_tag(ev, r)}: {'; '.join(r.warnings) or 'no songs found'}")
        for c in r.candidates[:3]:
            out(f"      same name: {c['name']} ({_where(c)}) {c.get('url', '')}")
        h = ev.handle_map.get(normalize_name(r.name))
        if h:
            out(f"      tip: if instagram.com/{h['handle']} links to Spotify, pin that link")

    section("Matched", ok, f_ok)
    section("Matched, but maybe another band with the same name", unsure, f_unsure)
    section("Several same-name artists included", multi, f_multi)
    section("No songs added", missing, f_missing)
    section("Unreadable logos", ev.unreadable, lambda u: out(f"  ~ {u}"))
    out(f"\nPlaylist: {playlist}")
