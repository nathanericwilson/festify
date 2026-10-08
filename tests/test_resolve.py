"""Decision rules and resolver behaviour (all APIs faked)."""
import pytest

from festify.cache import Cache
from festify.config import Config
from festify.extract import Event
from festify.llm import BillingError
from festify.models import Candidate
from festify.report import playlist_description
from festify.resolve import Resolver, decide, pin_entry


def cand(cid, km=None, nearest="Provo, UT"):
    return Candidate(cid, "deezer", "Band", distance_km=km, nearest=nearest if km is not None else "")


# ---- decide(): pure rules -----------------------------------------------------------
def test_unique_match_is_kept_but_flagged_when_far():
    ch, warn, kind, _ = decide("Band", [cand("deezer:1", 8000)], {}, 3)
    assert kind == "single" and ch[0][1] == 3 and warn == ["unsure: ~8,000 km from Provo"]
    assert decide("Band", [cand("deezer:1", 20)], {}, 3)[1] == []


def test_single_local_candidate_wins_even_if_judge_prefers_other():
    cs = [cand("deezer:1", 9000), cand("deezer:2", 30), cand("deezer:3")]
    ch, warn, kind, confident = decide("Band", cs, {"pick": "deezer:1", "confidence": "high"}, 3)
    assert [c.cid for c, _ in ch] == ["deezer:2"] and confident and warn == []


def test_multi_is_capped_at_three_nearby_and_never_far():
    cs = [cand(f"deezer:{i}", km) for i, km in enumerate([50, 60, 200, 300, 5000])]
    verdict = {"pick": "deezer:0", "confidence": "low", "plausible": [c.cid for c in cs]}
    ch, warn, kind, _ = decide("Band", cs, verdict, 3)
    assert kind == "multi" and len(ch) == 3 and [k for _, k in ch] == [3, 2, 1]
    assert all(c.distance_km < 400 for c, _ in ch) and warn == ["multi: 3 possible matches"]


def test_only_plausible_nearby_candidates_are_included():
    cs = [cand("deezer:1", 200), cand("deezer:2", 250), cand("deezer:3", 300)]
    ch, _, kind, _ = decide("Band", cs, {"confidence": "low", "plausible": ["deezer:2"]}, 3)
    assert kind == "single" and [c.cid for c, _ in ch] == ["deezer:2"]


def test_all_far_means_not_found_nearby():
    cs = [cand("deezer:1", 1500), cand("deezer:2", 3000)]
    ch, warn, kind, _ = decide("Band", cs, {"pick": None, "confidence": "low", "plausible": []}, 3)
    assert ch == [] and kind == "none" and warn == ["not found nearby"]


def test_unknown_distance_candidates_capped_at_two():
    cs = [cand("deezer:1"), cand("deezer:2"), cand("deezer:3")]
    ch, _, kind, _ = decide("Band", cs, {"confidence": "low", "plausible": ["deezer:1", "deezer:2", "deezer:3"]}, 3)
    assert kind == "multi" and len(ch) == 2


# ---- resolver with fakes ----------------------------------------------------------
class Dz:
    def __init__(self, artists): self.artists = artists
    def exact_artists(self, name): return self.artists
    def albums(self, *a, **k): return []
    def top_tracks(self, *a, **k): return []
    def track(self, *a): return {}


class LF:
    available = False


class Sp:
    def __init__(self, artists=None): self.artists = artists or {}
    def search_artists(self, *a, **k): return []
    def artist_albums(self, *a, **k): return []
    def get_artist(self, sid): return {"id": sid, "name": self.artists.get(sid, "")} if sid in self.artists else None
    def track_by_isrc(self, *a): return []
    def search_tracks(self, q, offset=0):
        if offset:
            return []
        return [{"id": sid, "uri": f"spotify:track:{sid}", "name": f"Song by {n}", "album": "",
                 "artists": [{"id": sid, "name": n}]} for sid, n in self.artists.items()]


class Loc:
    def __init__(self, kms): self.kms = kms
    def annotate(self, name, cands, event, handle=None):
        for c in cands:
            c.distance_km = self.kms.get(c.cid)
            c.nearest = "Provo, UT" if c.distance_km is not None else ""
    def _region_words(self, event): return ["Provo", "Utah"]


class Judge:
    def __init__(self, verdict=None, exc=None): self.verdict, self.exc, self.calls = verdict or {}, exc, 0
    def pick(self, *a, **k):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.verdict


class Web:
    on = True
    def __init__(self, hits): self.hits = hits
    def spotify_links(self, *a, **k): return self.hits
    def find_handle(self, *a, **k): return None


def make(tmp_path, artists, kms, judge, web=None, sp=None):
    return Resolver(Config(), Cache(tmp_path / "c.json"), Dz(artists), LF(), sp or Sp(), Loc(kms), judge,
                    web=web, log=lambda *_: None)


TWO = [{"id": 1, "name": "Band", "nb_fan": 5}, {"id": 2, "name": "Band", "nb_fan": 900}]


def test_judge_failure_is_not_cached(tmp_path):
    r = make(tmp_path, TWO, {}, Judge({"failed": True, "pick": None, "confidence": "low", "plausible": [], "reason": "x"}))
    res = r.resolve("Band", Event(name="E"), songs=False)
    assert res.outcome == "none" and r.cache.get("artists", "band") is None


def test_billing_error_propagates(tmp_path):
    r = make(tmp_path, TWO, {}, Judge(exc=BillingError("no credit")))
    with pytest.raises(BillingError):
        r.resolve("Band", Event(name="E"))


def test_pin_beats_everything(tmp_path):
    r = make(tmp_path, TWO, {"deezer:1": 10}, Judge(), sp=Sp({"PINNED": "Band"}))
    r.cache.set("artists", "band", pin_entry("Band", "PINNED"))
    res = r.resolve("Band", Event(name="E"))
    assert res.by == "pin" and res.songs[0].uri == "spotify:track:PINNED"


def test_web_identity_used_when_rules_are_unsure(tmp_path):
    web = Web([{"spotify_id": "REAL", "via": "web search (@band page: linktr.ee)", "url": "u"}])
    r = make(tmp_path, TWO, {"deezer:1": 3000, "deezer:2": 5000}, Judge({"pick": None, "confidence": "low", "plausible": []}),
             web=web, sp=Sp({"REAL": "Band"}))
    res = r.resolve("Band", Event(name="E"))
    assert res.by == "websearch" and res.has_songs and r.cache.get("artists", "band")["by"] == "websearch"


def test_web_link_to_differently_named_artist_is_ignored(tmp_path):
    web = Web([{"spotify_id": "OTHER", "via": "x", "url": "u"}])
    r = make(tmp_path, TWO, {"deezer:1": 3000, "deezer:2": 5000}, Judge({"pick": None, "confidence": "low", "plausible": []}),
             web=web, sp=Sp({"OTHER": "Someone Else"}))
    assert r.resolve("Band", Event(name="E"), songs=False).outcome == "none"


def test_confident_local_pick_skips_web_search(tmp_path):
    web = Web([{"spotify_id": "REAL", "via": "x", "url": "u"}])
    j = Judge({"pick": "deezer:1", "confidence": "low", "plausible": ["deezer:1"]})
    r = make(tmp_path, TWO, {"deezer:1": 20, "deezer:2": 5000}, j, web=web, sp=Sp({"REAL": "Band"}))
    res = r.resolve("Band", Event(name="E"), songs=False)
    assert res.by == "rules" and res.picks[0]["url"] == "" and res.picks[0]["tier"] == "local"


def test_single_match_without_conflict_doesnt_call_judge(tmp_path):
    j = Judge()
    r = make(tmp_path, TWO[:1], {"deezer:1": 15}, j)
    r.resolve("Band", Event(name="E"), songs=False)
    assert j.calls == 0


# ---- playlist description -------------------------------------------------------------
def _res(name, outcome, warnings=(), songs=True, picks=1):
    from festify.models import ArtistResult, SongPick
    r = ArtistResult(name, outcome, warnings=list(warnings), picks=[{}] * picks)
    if songs:
        r.songs = [SongPick("s", f"spotify:track:{name}", "isrc", "Deezer")]
    return r


def test_description_lists_warnings_by_priority():
    ev = Event(name="E", unreadable=["logo"])
    rs = [_res("Good", "single"), _res("Lavon", "single", ["unsure: ~8,000 km from Provo"]),
          _res("Daisy", "multi", ["multi: 2 possible matches"], picks=2),
          _res("World Record", "none", ["not found nearby"], songs=False),
          _res("Gene Freak", "none", ["not on Spotify"], songs=False)]
    d = playlist_description(ev, rs)
    assert d.startswith("festify:") and "Lavon (~8,000 km from Provo)" in d and "Daisy (2)" in d
    assert "Not found nearby: World Record" in d and "Not on Spotify: Gene Freak" in d and "unreadable" in d
    assert d.index("Lavon") < d.index("Daisy") < d.index("World Record") < d.index("Gene Freak")


def test_description_fits_spotify_limit():
    ev = Event(name="E")
    rs = [_res(f"Missing Band Number {i}", "none", ["not on Spotify"], songs=False) for i in range(40)]
    rs += [_res(f"Far Band {i}", "single", ["unsure: ~9,000 km from Provo"]) for i in range(10)]
    d = playlist_description(ev, rs)
    assert len(d) <= 300 and "Far Band 0" in d


def test_description_all_good():
    assert playlist_description(Event(name="E"), [_res("A", "single")]) == "festify: all 1 artists matched."
