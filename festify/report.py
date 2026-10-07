from __future__ import annotations

from .extract import Event
from .models import ArtistResult
from .names import normalize_name


def playlist_name(ev: Event) -> str:
    return f"{ev.name} – {ev.date}" if ev.date else ev.name


def playlist_description(ev: Event, results: list[ArtistResult]) -> str:
    missing = [r.name for r in results if not r.has_songs]
    d = f"{ev.venue + ', ' if ev.venue else ''}{ev.city}".strip(", ")
    s = f"Built by festify. {d}." if d else "Built by festify."
    if missing:
        s += " Not on Spotify / no songs found: " + ", ".join(missing) + "."
    return s


def _tag(ev: Event, r: ArtistResult) -> str:
    from .names import normalize_name
    h = ev.handle_map.get(normalize_name(r.name))
    return (f" (name inferred from @{h['handle']})" if h and h.get('inferred') else f" (@{h['handle']})" if h else "")


def _songs(r: ArtistResult) -> str:
    return "; ".join(s.title for s in r.songs) + f"  <- {', '.join(r.song_sources)}"


def print_report(ev: Event, results: list[ArtistResult], playlist: str, dry_run: bool, out=print):
    out("\n" + "=" * 72)
    out(f"{playlist_name(ev)}   [{ev.venue or 'venue ?'}, {ev.city or 'city ?'}]")
    out("=" * 72)
    matched = [r for r in results if r.outcome == "matched" and r.has_songs and not r.nonlocal_flag]
    picked = [r for r in results if r.outcome in ("picked", "manual") and r.has_songs]
    flagged = [r for r in results if r.outcome == "matched" and r.has_songs and r.nonlocal_flag]
    review = [r for r in results if r.outcome == "needs_review"]
    missing = [r for r in results if not r.has_songs and r.outcome != "needs_review"]

    def section(title, rows, fmt):
        out(f"\n{title} ({len(rows)})")
        for r in rows:
            fmt(r)

    def f_match(r):
        out(f"  + {r.name}{_tag(ev, r)}: {_songs(r)}")
        if r.reason.startswith("Spotify link from @"):
            out(f"      identity: {r.reason}")
        elif r.local_evidence != "yes":
            out(f"      (no local evidence found)")

    def f_pick(r):
        how = "you chose" if r.outcome == "manual" else f"picked from {r.n_candidates}"
        out(f"  * {r.name}{_tag(ev, r)}: {how} -> {r.chosen}")
        out(f"      songs: {_songs(r)}")
        out(f"      why: {r.reason}")
        for e in r.evidence[:3]:
            out(f"      evidence: {e}")
        if r.local_evidence != "yes":
            out("      ! no candidate had solid local evidence; low-confidence pick")
        if r.spotify_link:
            out(f"      check: {r.spotify_link}")

    def f_flag(r):
        out(f"  ? {r.name}{_tag(ev, r)}: {_songs(r)}")
        out(f"      why flagged: {r.reason}")
        for e in r.evidence[:3]:
            out(f"      evidence: {e}")
        if r.spotify_link:
            out(f"      check: {r.spotify_link}")

    def f_miss(r):
        out(f"  - {r.name}{_tag(ev, r)}: {r.reason}")
        h = ev.handle_map.get(normalize_name(r.name))
        if h:
            out(f"      tip: check instagram.com/{h['handle']} for a Spotify link, then pin it")

    def f_review(r):
        out(f"  ! {r.name}{_tag(ev, r)}: several artists share this name and I'm not sure which. No songs added.")
        out(f"      best guess: {r.chosen}")
        out(f"      why unsure: {r.reason[:400]}")
        for c in r.candidates:
            fans = f" ({c['fans']} Deezer fans)" if c.get("fans") is not None else ""
            out(f"      - {c['name']}{fans} {c['url']}")
        out("      fix: re-run with --ask, or --pin \"Name=<spotify artist link>\"")

    section("NEEDS YOUR PICK (same-name artists, not confident)", review, f_review)
    section("Matched", matched, f_match)
    section("Picked from several same-name artists", picked, f_pick)
    section("Single match, possibly NOT local", flagged, f_flag)
    section("Not on Spotify / no songs found", missing, f_miss)
    out(f"\nIllegible logos ({len(ev.unreadable)})")
    for u in ev.unreadable:
        out(f"  ~ {u}")
    out(f"\nPlaylist: {playlist}")
