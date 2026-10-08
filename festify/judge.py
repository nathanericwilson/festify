"""The LLM looks at same-name candidates (with distances) and says which could be the band on this bill."""
from __future__ import annotations

import hashlib
import json

from .llm import BillingError
from .models import Candidate

VERSION = 2  # bump when the prompt changes so cached verdicts are refreshed

SYSTEM = """You help match band names on small local concert posters to the right artist when several artists share
a name. The correct band is usually small and from near the show. Return STRICT JSON only:
{"pick": <candidate id or null>, "confidence": "high"|"medium"|"low",
 "plausible": [candidate ids, most likely first], "reason": str}
Rules:
- Each candidate has "distance_km" from the show's area (null = unknown) and a "tier": local (<=80 km),
  regional (<=400 km), far, or unknown. Distance comes from verified evidence; trust it.
- "plausible" lists every candidate that could reasonably be the band on THIS bill: local and regional ones,
  and unknown ones whose style/size fits. Never list a far candidate unless the poster says it's a touring act.
- Big fan counts on a tiny local bill are a warning sign, not a plus.
- "high" confidence only with clear evidence (e.g. it is the only local candidate, or the Instagram handle,
  releases or genre clearly match). Otherwise "medium" or "low".
- pick null if nothing fits. Keep "reason" to one or two short sentences."""


def candidate_view(c: Candidate) -> dict:
    return {"id": c.cid, "name": c.name, "distance_km": c.distance_km, "tier": c.tier, "nearest_city": c.nearest,
            "deezer_fans": c.fans, "lastfm_listeners": c.listeners, "genres": c.genres[:5],
            "releases": c.releases[:6], "songs": c.songs[:6], "location_evidence": c.location[:4]}


class Judge:
    def __init__(self, llm, cfg, cache=None):
        self.llm, self.cfg, self.cache = llm, cfg, cache

    def pick(self, name: str, cands: list[Candidate], lastfm_ctx: dict | None, event, handle: str | None) -> dict:
        ctx = {
            "artist_name_on_poster": name, "instagram_handle_on_poster": handle,
            "event": {"name": event.name, "city": event.city, "venue": event.venue, "genre_hints": event.genre_hints},
            "candidates": [candidate_view(c) for c in cands],
            "lastfm_page_for_this_name (may belong to any candidate)": lastfm_ctx,
        }
        body = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
        key = f"v{VERSION}:{hashlib.sha1(body.encode()).hexdigest()[:16]}"
        if self.cache is not None:
            hit = self.cache.get("verdicts", key)
            if hit:
                return hit
        try:
            out = self.llm.ask_json(SYSTEM, body, max_tokens=600, fast=True)
        except BillingError:
            raise
        except Exception as e:
            return {"failed": True, "pick": None, "confidence": "low", "plausible": [], "reason": f"judge failed: {e}"}
        valid = {c.cid for c in cands}
        out["pick"] = out.get("pick") if out.get("pick") in valid else None
        out["plausible"] = [x for x in out.get("plausible") or [] if x in valid]
        if self.cache is not None:
            self.cache.set("verdicts", key, out)
        return out
