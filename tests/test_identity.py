from festify.cache import Cache
from festify.config import Config
from festify.extract import Event
from festify.identity import HandleLinker
from festify.resolve import Resolver

SP_ID = "1sLV65Y5JPvcLSADaDE5uK"


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class Sess:
    def __init__(self, pages):
        self.pages, self.headers, self.urls = pages, {}, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.pages.get(url, Resp(404))


def test_spotify_link_directly_in_bio(tmp_path):
    c = Cache(tmp_path / "c.json")
    hl = HandleLinker(c, session=Sess({}), ig_fetch=lambda h: [f"utah punk https://open.spotify.com/artist/{SP_ID}?si=x"])
    assert hl.find("@World_Record_Band") == {"spotify_id": SP_ID, "via": "Instagram bio"}
    assert c.get("handles", "world_record_band")["spotify_id"] == SP_ID


def test_follows_linktree_in_bio(tmp_path):
    s = Sess({"https://linktr.ee/wrb": Resp(200, f'<a href="https://open.spotify.com/artist/{SP_ID}">')})
    hl = HandleLinker(Cache(tmp_path / "c.json"), session=s, ig_fetch=lambda h: ["bio", "linktr.ee/wrb"])
    assert hl.find("world_record_band")["via"] == "link in Instagram bio (linktr.ee)"


def test_linktree_guess_needs_backlink(tmp_path):
    page = f'open.spotify.com/artist/{SP_ID}'
    s1 = Sess({"https://linktr.ee/band": Resp(200, page)})  # no link back to instagram.com/band
    assert HandleLinker(Cache(tmp_path / "a.json"), session=s1, ig_fetch=lambda h: None).find("band") is None
    s2 = Sess({"https://linktr.ee/band": Resp(200, page + ' instagram.com/band"')})
    assert HandleLinker(Cache(tmp_path / "b.json"), session=s2, ig_fetch=lambda h: None).find("band")["spotify_id"] == SP_ID


def test_miss_not_cached_when_instagram_blocked(tmp_path):
    c = Cache(tmp_path / "c.json")
    HandleLinker(c, session=Sess({}), ig_fetch=lambda h: None).find("band")
    assert c.get("handles", "band") is None  # retry next time (e.g. after logging in)
    HandleLinker(c, session=Sess({}), ig_fetch=lambda h: ["no links here"]).find("band")
    assert c.get("handles", "band") == {}


class _Linker:
    def __init__(self, hit): self.hit = hit
    def find(self, handle): return self.hit


class _Sp:
    def __init__(self, name): self.name = name
    def get_artist(self, aid): return {"id": aid, "name": self.name}
    def search_tracks(self, q, offset=0):
        return [] if offset else [{"id": "t", "uri": "spotify:track:t", "name": "Real Song", "album": "",
                                   "artists": [{"id": SP_ID, "name": self.name}]}]
    def artist_albums(self, *a, **k): return []
    def search_artists(self, *a, **k): return []


def _resolver(tmp_path, linker, sp):
    return Resolver(Config(), Cache(tmp_path / "c.json"), None, None, sp, None, None, linker=linker, log=lambda *_: None)


def test_handle_link_beats_cached_guess(tmp_path):
    r = _resolver(tmp_path, _Linker({"spotify_id": SP_ID, "via": "Instagram bio"}), _Sp("World Record"))
    r.cache.set("artists", "world record", {"choice": None, "by": "claude", "reason": "no plausible candidate"})
    ev = Event(name="Show", handle_map={"world record": {"handle": "world_record_band", "inferred": False}})
    res = r.resolve("world record", ev)
    assert res.has_songs and res.songs[0].title == "Real Song"
    assert r.cache.get("artists", "world record")["by"] == "instagram"


def test_handle_link_to_differently_named_artist_is_ignored(tmp_path):
    r = _resolver(tmp_path, _Linker({"spotify_id": SP_ID, "via": "Instagram bio"}), _Sp("Singer's Solo Project"))
    r.cache.set("artists", "world record", {"choice": None, "by": "claude", "reason": "none"})
    ev = Event(name="Show", handle_map={"world record": {"handle": "world_record_band", "inferred": False}})
    assert r.resolve("world record", ev).outcome == "not_found"


def test_manual_pin_wins_over_handle(tmp_path):
    r = _resolver(tmp_path, _Linker({"spotify_id": "OTHER", "via": "Instagram bio"}), _Sp("World Record"))
    r.cache.set("artists", "world record", {"choice": {"cid": f"spotify:{SP_ID}", "source": "spotify", "name": "World Record",
                                                       "spotify_id": SP_ID, "pinned": True}, "by": "manual"})
    ev = Event(name="Show", handle_map={"world record": {"handle": "world_record_band", "inferred": False}})
    r.resolve("world record", ev)
    assert r.cache.get("artists", "world record")["by"] == "manual"
