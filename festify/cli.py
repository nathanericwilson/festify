"""festify command line. The app (festify.gui) runs this same code."""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from .cache import Cache
from .config import load_config
from .extract import Event, extract_events, merge_events
from .geo import Geo
from .identity import HandleLinker
from .instagram import collect_inputs
from .judge import Judge
from .llm import BillingError
from .location import Locator
from .names import normalize_name
from .report import playlist_description, playlist_name, print_report
from .resolve import Resolver, pin_entry
from .sources import Deezer, LastFm
from .spotify import Spotify
from .websearch import WebSearch


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="festify", description="Turn concert posters into Spotify playlists.")
    p.add_argument("inputs", nargs="*", help="Instagram post/reel URLs, screenshot images, or folders")
    p.add_argument("--dry-run", action="store_true", help="show everything, create no playlists")
    p.add_argument("--tracks", type=int, default=3, help="songs per artist (default 3)")
    p.add_argument("--pin", action="append", default=[], metavar="NAME=SPOTIFY_LINK",
                   help='lock a band to an exact Spotify artist, e.g. --pin "Skrude=https://open.spotify.com/artist/ID"')
    p.add_argument("--events-file", help="use saved events (skips reading posters)")
    p.add_argument("--save-events", help="add the events read from posters to this JSON file")
    p.add_argument("--events-only", action="store_true", help="just read posters and save events")
    p.add_argument("--claude", action="store_true", help="use the paid Claude API instead of free Gemini")
    p.add_argument("--no-instagram", action="store_true", help="skip bands' Instagram bio / Linktree lookups")
    p.add_argument("--no-search", action="store_true", help="skip Tavily web search")
    p.add_argument("--no-geocode", action="store_true", help="skip OpenStreetMap distance lookups")
    p.add_argument("--max-searches", type=int, default=30, help="web searches per run (default 30)")
    p.add_argument("--cache", help="cache file (default .festify_cache.json)")
    return p.parse_args(argv)


def make_llm(cfg, use_claude: bool):
    if use_claude:
        if not cfg.anthropic_key:
            raise SystemExit("--claude needs ANTHROPIC_API_KEY in .env.")
        from .llm import LLM

        return LLM(cfg.anthropic_key, cfg.model, cfg.fast_model)
    if not cfg.gemini_key:
        raise SystemExit("Needs GEMINI_API_KEY in .env (free key: https://aistudio.google.com/apikey).")
    from .gemini import GeminiLLM

    return GeminiLLM(cfg.gemini_key, cfg.gemini_model)


def load_events(path: str) -> list[Event]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Event.from_dict(d) for d in (raw if isinstance(raw, list) else raw.get("events", []))]


def save_events(path: str, events: list[Event]):
    p = Path(path)
    old = load_events(path) if p.exists() else []  # accumulate so older playlists can still be refreshed
    p.write_text(json.dumps([e.__dict__ for e in merge_events(old + events)], indent=2), encoding="utf-8")


def find_playlist(cache: Cache, ev: Event) -> tuple[str, dict | None]:
    """The playlist already built for this event, tolerating small naming differences between poster reads."""
    pkey = normalize_name(f"{playlist_name(ev)} {ev.venue}")
    hit = cache.get("playlists", pkey)
    if hit:
        return pkey, hit
    name, date = normalize_name(ev.name), normalize_name(ev.date)
    for k, v in cache.data.get("playlists", {}).items():
        if date and date not in k:
            continue
        if (name and name in k) or difflib.SequenceMatcher(None, k, pkey).ratio() >= 0.7:
            return k, v
    return pkey, None


def pin_artists(cache: Cache, pins: list[str]):
    for item in pins:
        name, _, link = item.partition("=")
        m = re.search(r"artist/(\w+)", link) or re.fullmatch(r"\s*(\w{22})\s*", link)
        if not name.strip() or not m:
            raise SystemExit(f'Bad --pin {item!r}. Use: --pin "Artist Name=https://open.spotify.com/artist/<id>"')
        cache.set("artists", normalize_name(name), pin_entry(name.strip(), m.group(1)))
        print(f"Pinned {name.strip()} -> spotify:artist:{m.group(1)}")


def build_resolver(cfg, cache, llm, sp, args, log=print) -> Resolver:
    web = None if args.no_search or not cfg.tavily_key else WebSearch(cfg.tavily_key, cache, args.max_searches, log=log)
    geo = Geo(cache, cfg.mb_contact, enabled=not args.no_geocode, log=log)
    locator = Locator(cfg, cache, llm, geo, web, log=log)
    return Resolver(cfg, cache, Deezer(), LastFm(cfg.lastfm_key), sp, locator, Judge(llm, cfg, cache),
                    n_tracks=args.tracks, log=log, web=web,
                    linker=None if args.no_instagram else HandleLinker(cache, cfg.ig_username, log=log, web=web))


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = parse_args(argv)
    cfg = load_config()
    cache = Cache(Path(args.cache) if args.cache else cfg.cache_path)
    if args.pin:
        pin_artists(cache, args.pin)
        if not args.inputs and not args.events_file:
            return
    if not args.inputs and not args.events_file:
        raise SystemExit("Give me an Instagram link, screenshot or folder (or --events-file).")
    llm = make_llm(cfg, args.claude)
    print(f"Using {'Claude (paid)' if args.claude else 'Gemini free tier'}; local cities: {cfg.home_region}")

    # 1. posters -> events
    if args.events_file:
        events = load_events(args.events_file)
    else:
        events = []
        posts = collect_inputs(args.inputs, cfg.ig_username)
        if not posts:
            raise SystemExit("Nothing readable. Screenshots always work - pass image files or a folder.")
        for post in posts:
            print(f"Reading {post.label} ({len(post.images)} image(s))...")
            events += extract_events(llm, post)
    events = merge_events(events)
    if args.save_events and not args.events_file:
        save_events(args.save_events, events)
    if not events:
        raise SystemExit("No events found in those images.")
    print(f"Found {len(events)} event(s): " + "; ".join(f"{e.name} ({len(e.artists)} artists)" for e in events))
    if args.events_only:
        return

    # 2. artists -> songs -> playlists
    sp = Spotify(cfg, write=not args.dry_run)
    resolver = build_resolver(cfg, cache, llm, sp, args)
    if not cfg.tavily_key and not args.no_search:
        print("Tip: add a free TAVILY_API_KEY to .env to find more tiny bands.")
    for ev in events:
        print(f"\nResolving {len(ev.artists)} artists for {ev.name}...")
        results = []
        for a in ev.artists:
            print(f"  {a} ...", flush=True)
            results.append(resolver.resolve(a, ev))
        link = "(dry run - no playlist created)"
        if not args.dry_run:
            pkey, done = find_playlist(cache, ev)
            uris = list(dict.fromkeys(s.uri for r in results for s in r.songs))
            desc = playlist_description(ev, results)
            if done and uris:  # same event again: update its playlist instead of making a duplicate
                sp.replace_items(done["id"], uris)
                sp.update_description(done["id"], desc)
                link = f"{done['url']} (updated)"
            elif uris:
                pl = sp.create_playlist(playlist_name(ev), desc)
                sp.add_items(pl["id"], uris)
                if ev.cover_image:
                    sp.set_cover(pl["id"], ev.cover_image)
                link = pl.get("external_urls", {}).get("spotify", pl["id"])
                cache.set("playlists", pkey, {"id": pl["id"], "url": link})
            else:
                link = "(no songs found; playlist not created)"
        print_report(ev, results, link)
        print(f"Description: {playlist_description(ev, results)}")
    print("\n" + llm.usage_line())


def run():
    try:
        main()
    except BillingError as e:
        raise SystemExit(f"\nSTOPPED: {e}")


if __name__ == "__main__":
    run()
