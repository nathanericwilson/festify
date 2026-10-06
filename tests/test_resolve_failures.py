"""Judge/web failures must never be cached as final answers."""
import pytest

from festify.cache import Cache
from festify.config import Config
from festify.extract import Event
from festify.llm import BillingError
from festify.models import Candidate
from festify.resolve import Resolver


class FakeDeezer:
    def exact_artists(self, name):
        return [{"id": 1, "name": name, "nb_fan": 5}, {"id": 2, "name": name, "nb_fan": 900}]

    def albums(self, *a, **k): return []
    def top_tracks(self, *a, **k): return []
    def track(self, *a, **k): return {}


class FakeLastFm:
    available = False


class FakeSpotify:
    def search_artists(self, *a, **k): return []
    def track_by_isrc(self, *a): return []
    def search_tracks(self, *a, **k): return []


class FakeLocator:
    def annotate(self, name, cands, event): pass


class FailingJudge:
    def pick(self, *a, **k):
        return {"judge_failed": True, "pick": None, "reason": "boom"}


class BillingJudge:
    def pick(self, *a, **k):
        raise BillingError("no credit")


def make(tmp_path, judge):
    cache = Cache(tmp_path / "c.json")
    r = Resolver(Config(), cache, FakeDeezer(), FakeLastFm(), FakeSpotify(), FakeLocator(), judge,
                 input_fn=lambda _: "")
    return r, cache


def test_judge_failure_with_several_candidates_is_not_cached(tmp_path):
    r, cache = make(tmp_path, FailingJudge())
    res = r.resolve("Same Name", Event(name="E"))
    assert res.outcome == "not_found" and "not cached" in res.reason
    assert cache.get("artists", "same name") is None


def test_billing_error_propagates(tmp_path):
    r, cache = make(tmp_path, BillingJudge())
    with pytest.raises(BillingError):
        r.resolve("Same Name", Event(name="E"))
    assert cache.get("artists", "same name") is None


def test_event_name_falls_back_to_venue():
    assert Event.from_dict({"name": "", "venue": "The Cellar"}).name == "The Cellar"
    assert Event.from_dict({}).name == "Show"


def test_web_search_only_for_ambiguous_and_capped(tmp_path):
    from festify.location import Locator

    class Llm:
        calls = 0

        def ask_json_web(self, *a, **k):
            Llm.calls += 1
            return {"results": []}

    class Mb:
        def lookup(self, name): return []

    loc = Locator(Config(), Cache(tmp_path / "c.json"), Llm(), mb=Mb(), max_web=1, log=lambda *_: None)
    one = [Candidate("deezer:1", "deezer", "A")]
    two = [Candidate("deezer:2", "deezer", "B"), Candidate("deezer:3", "deezer", "B")]
    two2 = [Candidate("deezer:4", "deezer", "C"), Candidate("deezer:5", "deezer", "C")]
    ev = Event(name="E")
    loc.annotate("A", one, ev)      # single match: no web search
    assert Llm.calls == 0
    loc.annotate("B", two, ev)      # ambiguous: one search
    assert Llm.calls == 1
    loc.annotate("C", two2, ev)     # cap of 1 reached: skipped
    assert Llm.calls == 1


def test_pinned_artist_uses_spotify_only(monkeypatch):
    from festify.songs import pick_songs

    class Sp:
        def search_artists(self, *a, **k): return []
        def track_by_isrc(self, *a): raise AssertionError("pinned artists must not use ISRC/Deezer")
        def search_tracks(self, q, offset=0):
            t = {"id": "t", "uri": "spotify:track:t", "name": "Real Song", "album": "",
                 "artists": [{"id": "RIGHT", "name": "Skrude"}]}
            w = {"id": "w", "uri": "spotify:track:w", "name": "Wrong", "album": "",
                 "artists": [{"id": "WRONG", "name": "Skrude"}]}
            return [t, w] if offset == 0 else []
        def artist_albums(self, *a, **k): return []

    class Dz:
        def top_tracks(self, *a, **k): raise AssertionError("pinned artists must not use Deezer")

    class Lf:
        available = True
        def top_tracks(self, *a, **k): raise AssertionError("pinned artists must not use Last.fm")

    choice = {"cid": "spotify:RIGHT", "source": "spotify", "spotify_id": "RIGHT", "pinned": True}
    res = pick_songs(Sp(), Dz(), Lf(), "Skrude", choice, n=3)
    assert [s.title for s in res.songs] == ["Real Song"]
