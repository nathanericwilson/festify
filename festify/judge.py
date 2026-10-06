"""Claude picks the most plausible candidate (or null) and flags possibly non-local artists."""
from __future__ import annotations

import json

from .llm import BillingError
from .models import Candidate
from .names import normalize_name

SYSTEM = """You disambiguate bands that share names. The user attends small local shows, so the correct band is
usually small and from/near the event's city. Return STRICT JSON only:
{"pick": <candidate id or null>, "confidence": "high"|"medium"|"low", "local_evidence": "yes"|"no"|"unknown",
 "nonlocal_suspected": bool, "reason": str}
Rules:
- Strongly prefer candidates with evidence of being from or playing near the target location.
- Large fan/listener counts for a name on a small local bill are a warning sign, not a plus.
- "local_evidence" refers to the PICKED candidate: "yes" only if the evidence actually places it near the target.
- If no candidate has local evidence, say so (local_evidence "no" or "unknown") and keep confidence low; do not pretend.
- With a single candidate, "nonlocal_suspected" is true if the evidence suggests it's a different, non-local band.
- pick null if no candidate plausibly fits."""


def candidate_view(c: Candidate) -> dict:
    return {"id": c.cid, "name": c.name, "deezer_fans": c.fans, "lastfm_listeners": c.listeners,
            "genres": c.genres, "releases": c.releases[:6], "songs": c.songs[:6], "location_evidence": c.location}


class Judge:
    def __init__(self, llm, cfg):
        self.llm, self.cfg = llm, cfg

    def pick(self, name: str, cands: list[Candidate], lastfm_ctx: dict | None, event) -> dict:
        ctx = {
            "artist_name_on_poster": name,
            "event": {"name": event.name, "city": event.city, "venue": event.venue,
                      "genre_hints": event.genre_hints, "date": event.date},
            "target_location": event.city or self.cfg.home_region,
            "instagram_handle_tagged_on_poster (the real band is the one this account belongs to; handle often hints at location/genre, e.g. slc/provo)": getattr(event, "handle_map", {}).get(normalize_name(name), {}).get("handle"),
            "candidates": [candidate_view(c) for c in cands],
            "lastfm_page_for_this_exact_name (cannot be tied to a specific candidate unless titles overlap)": lastfm_ctx,
        }
        try:
            out = self.llm.ask_json(SYSTEM, json.dumps(ctx, ensure_ascii=False, indent=1), max_tokens=800)
        except BillingError:
            raise
        except Exception as e:
            return {"judge_failed": True, "pick": None, "confidence": "low", "local_evidence": "unknown",
                    "nonlocal_suspected": False, "reason": f"judge failed: {e}"}
        valid = {c.cid for c in cands}
        if out.get("pick") not in valid:
            out["pick"] = None
        return out
