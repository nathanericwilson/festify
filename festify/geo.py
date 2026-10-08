"""Distance from the user's relevant cities, via OpenStreetMap Nominatim (geocoding) + haversine.

Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/): max 1 request/second,
an identifying User-Agent, cache results, and be able to switch it off. Every lookup here is cached forever
in the festify cache, so each place name is only ever geocoded once. Data (c) OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import math

from .http import Http
from .names import normalize_name

NOMINATIM = "https://nominatim.openstreetmap.org/search"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class Geo:
    def __init__(self, cache, contact: str = "festify", enabled: bool = True, http: Http | None = None, log=print):
        self.cache, self.enabled, self.log = cache, enabled, log
        self.http = http or Http(min_interval=1.1, headers={"User-Agent": f"festify/0.2 (local playlist tool; {contact})"})
        self._failed = False

    def locate(self, place: str) -> tuple[float, float] | None:
        key = normalize_name(place)
        if not key:
            return None
        hit = self.cache.get("geo", key)
        if hit is not None:
            return tuple(hit) if hit else None
        if not self.enabled or self._failed:
            return None
        data = self.http.get_json(NOMINATIM, {"q": place, "format": "json", "limit": 1})
        if data is None:  # network/429: stop for this run, don't cache the miss
            self._failed = True
            self.log("  ! geocoding unavailable right now; distances skipped for the rest of this run.")
            return None
        coords = [float(data[0]["lat"]), float(data[0]["lon"])] if data else []
        self.cache.set("geo", key, coords)
        return tuple(coords) if coords else None

    def nearest(self, place: str, cities: list[str]) -> tuple[float, str] | None:
        """(km, city) from `place` to the closest of `cities`, or None if anything can't be located."""
        p = self.locate(place)
        if not p:
            return None
        best = None
        for c in cities:
            q = self.locate(c)
            if q:
                km = haversine_km(p, q)
                if best is None or km < best[0]:
                    best = (km, c)
        return best
