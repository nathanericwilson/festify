from festify.cache import Cache
from festify.geo import Geo, haversine_km
from festify.models import tier_for
from festify.websearch import WebSearch

PROVO, SLC, PERTH = (40.2338, -111.6585), (40.7608, -111.8910), (-31.95, 115.86)


class FakeHttp:
    def __init__(self, table): self.table, self.calls = table, 0
    def get_json(self, url, params=None, retries=3):
        self.calls += 1
        q = params["q"]
        return [{"lat": str(self.table[q][0]), "lon": str(self.table[q][1])}] if q in self.table else []


def test_haversine_and_tiers():
    assert 55 < haversine_km(PROVO, SLC) < 70
    assert haversine_km(PROVO, PERTH) > 14000
    assert [tier_for(x) for x in (10, 200, 900, None)] == ["local", "regional", "far", "unknown"]


def test_geo_nearest_and_cache(tmp_path):
    http = FakeHttp({"Provo, UT": PROVO, "Salt Lake City, UT": SLC, "Springville, Utah": (40.165, -111.61), "Perth": PERTH})
    g = Geo(Cache(tmp_path / "c.json"), http=http, log=lambda *_: None)
    km, city = g.nearest("Springville, Utah", ["Provo, UT", "Salt Lake City, UT"])
    assert city == "Provo, UT" and km < 15
    assert g.nearest("Perth", ["Provo, UT"])[0] > 14000
    n = http.calls
    g.nearest("Springville, Utah", ["Provo, UT", "Salt Lake City, UT"])
    assert http.calls == n  # all cached
    assert g.nearest("Nowhere Town", ["Provo, UT"]) is None


class Resp:
    def __init__(self, status, payload=None): self.status_code, self._p = status, payload or {}
    def json(self): return self._p


class Sess:
    def __init__(self, resps): self.resps, self.bodies = list(resps), []
    def post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append(json)
        return self.resps.pop(0)


SID = "1sLV65Y5JPvcLSADaDE5uK"


def ws(tmp_path, resps, **k):
    return WebSearch("tvly-x", Cache(tmp_path / "c.json"), session=Sess(resps), log=lambda *_: None, **k)


def test_handle_page_gives_spotify_link(tmp_path):
    page = {"results": [{"url": "https://linktr.ee/worldrecordband", "title": "World Record | Linktree",
                         "content": "instagram.com/world_record_band", "raw_content": f"open.spotify.com/artist/{SID}"}]}
    w = ws(tmp_path, [Resp(200, page)])
    found = w.spotify_links("World Record", "world_record_band", ["Provo", "Utah"])
    assert found[0]["spotify_id"] == SID and "@world_record_band" in found[0]["via"]


def test_untied_page_is_ignored(tmp_path):
    page = {"results": [{"url": "https://some.blog/post", "title": "Best of World Record (LA)",
                         "content": "Los Angeles band World Record", "raw_content": f"open.spotify.com/artist/{SID}"}]}
    w = ws(tmp_path, [Resp(200, page), Resp(200, page)])
    assert w.spotify_links("World Record", "world_record_band", ["Provo", "Utah"]) == []


def test_quota_error_turns_search_off_and_is_not_cached(tmp_path):
    w = ws(tmp_path, [Resp(432)])
    assert w.search("q") == [] and not w.on
    assert w.cache.get("search", "q||0") is None


def test_cap_and_cache(tmp_path):
    w = ws(tmp_path, [Resp(200, {"results": []})], max_searches=1)
    w.search("a"); w.search("a")  # second is cached
    assert w.used == 1
    assert w.search("b") == [] and not w.on  # cap reached


def test_no_key_does_nothing(tmp_path):
    w = WebSearch("", Cache(tmp_path / "c.json"), log=lambda *_: None)
    assert w.search("x") == [] and w.spotify_links("A", None, ["Provo"]) == []
