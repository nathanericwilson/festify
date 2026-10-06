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
