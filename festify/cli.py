from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import Cache
from .config import load_config
from .extract import Event, extract_events, merge_events
from .instagram import collect_inputs
from .judge import Judge
from .llm import LLM, BillingError
from .names import normalize_name
from .location import Locator
from .report import playlist_description, playlist_name, print_report
from .resolve import Resolver
from .sources import Deezer, LastFm
from .spotify import Spotify


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="festify", description="Turn concert posters into Spotify playlists.")
    p.add_argument("inputs", nargs="*", help="Instagram post/reel URLs, screenshot images, or folders")
    p.add_argument("--ask", action="store_true", help="pick ambiguous artists yourself")
    p.add_argument("--dry-run", action="store_true", help="show everything, create no playlists")
    p.add_argument("--tracks", type=int, default=3, help="songs per artist (default 3)")
    p.add_argument("--pin", action="append", default=[], metavar="NAME=SPOTIFY_LINK",
                   help='lock an artist to an exact Spotify artist, e.g. --pin "Skrude=https://open.spotify.com/artist/ID"')
    p.add_argument("--refresh", action="store_true", help="re-fill playlists that were already created (after fixing picks)")
    p.add_argument("--cache", help="cache file path (default .festify_cache.json)")
    p.add_argument("--free", action="store_true", help="use Google Gemini free tier instead of Claude (no paid API)")
    p.add_argument("--no-web", action="store_true", help="never use web search for location (cheapest)")
    p.add_argument("--deep", action="store_true", help="also web-search artists with a single match (costly)")
    p.add_argument("--max-searches", type=int, default=25, help="cap on web-search lookups per run (default 25)")
    p.add_argument("--events-file", help="JSON of events (skips poster reading)")
    p.add_argument("--save-events", help="write the extracted events to this JSON file")
    return p.parse_args(argv)


def pin_artists(cache: Cache, pins: list[str]):
    import re

    for item in pins:
        name, _, link = item.partition("=")
        m = re.search(r"artist/(\w+)", link) or re.fullmatch(r"\s*(\w{22})\s*", link)
        if not name.strip() or not m:
            raise SystemExit(f'Bad --pin {item!r}. Use: --pin "Artist Name=https://open.spotify.com/artist/<id>"')
        sid = m.group(1)
        cache.set("artists", normalize_name(name), {
            "choice": {"cid": f"spotify:{sid}", "source": "spotify", "name": name.strip(),
                       "url": f"https://open.spotify.com/artist/{sid}", "spotify_id": sid, "pinned": True},
            "by": "manual", "ambiguous": False, "reason": "pinned by you", "confidence": "high",
            "local_evidence": "yes", "nonlocal_suspected": False, "evidence": [], "n_candidates": 1})
        print(f"Pinned {name.strip()} -> spotify:artist:{sid}")


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = parse_args(argv)
    cfg = load_config()
    if args.pin:
        pin_artists(Cache(Path(args.cache) if args.cache else cfg.cache_path), args.pin)
        if not args.inputs and not args.events_file:
            return
    if not args.inputs and not args.events_file:
        raise SystemExit("Give me at least one Instagram link, screenshot, or folder (or --events-file).")
    if args.free or cfg.provider == "gemini":
        if not cfg.gemini_key:
            raise SystemExit("Free mode needs GEMINI_API_KEY in .env (free key: https://aistudio.google.com/apikey).")
        from .gemini import GeminiLLM

        llm = GeminiLLM(cfg.gemini_key, cfg.gemini_model)
        print(f"Using Gemini free tier ({cfg.gemini_model}); no paid API calls.")
    else:
        if not cfg.anthropic_key:
            raise SystemExit("Missing ANTHROPIC_API_KEY in .env (see README), or use --free with GEMINI_API_KEY.")
        llm = LLM(cfg.anthropic_key, cfg.model, cfg.fast_model)

    # Step 1: posters -> events
    if args.events_file:
        raw = json.loads(Path(args.events_file).read_text(encoding="utf-8"))
        events = [Event.from_dict(d) for d in (raw if isinstance(raw, list) else raw.get("events", []))]
    else:
        events = []
        posts = collect_inputs(args.inputs, cfg.ig_username)
        if not posts:
            raise SystemExit("Nothing readable to work with. Screenshots always work - pass image files or a folder.")
        for post in posts:
            print(f"Reading {post.label} ({len(post.images)} image(s))...")
            events += extract_events(llm, post)
    events = merge_events(events)
    if args.save_events:
        Path(args.save_events).write_text(json.dumps([e.__dict__ for e in events], indent=2), encoding="utf-8")
    if not events:
        raise SystemExit("No events found in those images.")
    print(f"Found {len(events)} event(s): " + "; ".join(f"{e.name} ({len(e.artists)} artists)" for e in events))

    # Steps 2-5
    cache = Cache(Path(args.cache) if args.cache else cfg.cache_path)
    lastfm = LastFm(cfg.lastfm_key)
    if not lastfm.available:
        print("Note: LASTFM_API_KEY not set - using Deezer + Spotify only.")
    sp = Spotify(cfg, write=not args.dry_run)
    resolver = Resolver(cfg, cache, Deezer(), lastfm, sp, Locator(cfg, cache, llm, use_web=not args.no_web,
                                                max_web=args.max_searches, deep=args.deep),
                        Judge(llm, cfg), n_tracks=args.tracks, ask=args.ask)

    # Step 6
    for ev in events:
        print(f"\nResolving {len(ev.artists)} artists for {ev.name}...")
        results = []
        for a in ev.artists:
            print(f"  {a} ...", flush=True)
            results.append(resolver.resolve(a, ev))
        link = "(dry run - no playlist created)"
        if not args.dry_run:
            pkey = normalize_name(f"{playlist_name(ev)} {ev.venue}")
            done = cache.get("playlists", pkey)
            uris = list(dict.fromkeys(s.uri for r in results for s in r.songs))
            if done and args.refresh and uris:
                sp.replace_items(done["id"], uris)
                sp.update_description(done["id"], playlist_description(ev, results))
                link = f"{done['url']} (refreshed)"
            elif done:  # already built on an earlier run: never create duplicates
                link = f"{done['url']} (already created earlier; not duplicated)"
            elif uris:
                pl = sp.create_playlist(playlist_name(ev), playlist_description(ev, results))
                sp.add_items(pl["id"], uris)
                if ev.cover_image:
                    sp.set_cover(pl["id"], ev.cover_image)
                link = pl.get("external_urls", {}).get("spotify", pl["id"])
                cache.set("playlists", pkey, {"id": pl["id"], "url": link})
            else:
                link = "(no songs found; playlist not created)"
        print_report(ev, results, link, args.dry_run)
    print("\n" + llm.usage_line())


def run():
    try:
        main()
    except BillingError as e:
        raise SystemExit(f"\nSTOPPED: {e}")


if __name__ == "__main__":
    run()
