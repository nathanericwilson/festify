"""How good is automatic matching? `python -m festify.eval`

Re-decides every band in tests/golden.json (the known right answers) using the events in events.json,
IGNORING your pins and earlier decisions, and reports right / wrong / missing. Evidence caches (candidates,
MusicBrainz, web search, geocodes, judge verdicts) are reused, so re-running after a rule change is free;
decisions themselves are kept in memory only, so your real cache and pins are never changed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import Cache
from .cli import build_resolver, load_events, make_llm
from .config import ROOT, load_config
from .names import names_match, normalize_name
from .spotify import Spotify


class EvalCache(Cache):
    """Shares evidence with the real cache; keeps decisions (artists) in memory."""

    MEMORY = ("artists",)

    def __init__(self, path):
        super().__init__(path)
        self.mem: dict[str, dict] = {s: {} for s in self.MEMORY}

    def get(self, section, key):
        return self.mem[section].get(key) if section in self.MEMORY else super().get(section, key)

    def set(self, section, key, value):
        if section in self.MEMORY:
            self.mem[section][key] = value
        else:
            super().set(section, key, value)


def spotify_ids_for(resolver, cache, choice: dict, name: str) -> set[str]:
    ids = {choice["cid"]}
    if choice.get("spotify_id"):
        ids.add(choice["spotify_id"])
    if choice["cid"].startswith("deezer:"):
        key = choice["cid"]
        sid = cache.get("d2s", key)
        if sid is None:  # map a Deezer artist to Spotify via an ISRC of one of its songs
            sid = ""
            for t in resolver.deezer.top_tracks(key.split(":")[1], 3):
                isrc = resolver.deezer.track(t["id"]).get("isrc")
                for tr in resolver.sp.track_by_isrc(isrc) if isrc else []:
                    a = next((a for a in tr["artists"] if names_match(a["name"], name)), None)
                    if a:
                        sid = a["id"]
                        break
                if sid:
                    break
            cache.set("d2s", key, sid)
        if sid:
            ids.add(sid)
    return ids


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="festify.eval")
    p.add_argument("--events", default=str(ROOT / "events.json"))
    p.add_argument("--golden", default=str(ROOT / "tests" / "golden.json"))
    p.add_argument("--only", help="evaluate just this band name")
    p.add_argument("--no-search", action="store_true")
    a = p.parse_args(argv)
    cfg = load_config()
    cache = EvalCache(cfg.cache_path)
    llm = make_llm(cfg, use_claude=False)
    args = argparse.Namespace(no_search=a.no_search, no_geocode=False, no_instagram=False, max_searches=40, tracks=3)
    resolver = build_resolver(cfg, cache, llm, Spotify(cfg, write=False), args, log=lambda m: print(m))
    resolver.ignore_pins = True
    events = load_events(a.events)
    golden = json.loads(Path(a.golden).read_text(encoding="utf-8"))
    right = 0
    rows = []
    for g in golden:
        if a.only and not names_match(a.only, g["name"]):
            continue
        ev = next((e for e in events if any(names_match(x, g["name"]) for x in e.artists)), None)
        if not ev:
            rows.append((g["name"], "SKIP", "not in any saved event", ""))
            continue
        poster_name = next(x for x in ev.artists if names_match(x, g["name"]))
        expect = set(g["expect"])
        # bands expected to be left out need the song step: a match with no Spotify songs is dropped there
        res = resolver.resolve(poster_name, ev, songs=not expect, fresh=True)
        entry = cache.get("artists", normalize_name(poster_name)) or {}
        chosen = [] if res.outcome == "none" else [spotify_ids_for(resolver, cache, s["choice"], poster_name)
                                                   for s in entry.get("choices", [])]
        if not expect:
            ok = res.outcome == "none"
        else:
            ok = any(ids & expect or any(e.endswith(":") and any(i.startswith(e) for i in ids) for e in expect)
                     for ids in chosen)
        right += ok
        def show(ids):  # prefer the Spotify ID over the Deezer one
            return min(ids, key=lambda i: (i.startswith(("deezer:", "lastfm:", "spotify:")), i))
        got = " | ".join(show(ids) for ids in chosen) or "none"
        rows.append((g["name"], "OK" if ok else "WRONG", f"{res.outcome} via {res.by}: {got}",
                     "; ".join(res.warnings) + (f" | {res.reason[:120]}" if res.reason else "")))
    print()
    for name, status, got, why in rows:
        print(f"{status:5} {name:28} {got}")
        if status != "OK" and why:
            print(f"      {why}")
    scored = [r for r in rows if r[1] != "SKIP"]
    print(f"\n{right}/{len(scored)} correct   ({llm.usage_line()})")


if __name__ == "__main__":
    main()
