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


def test_profile_read_through_page_reader(tmp_path):
    page = ("* [442 followers](#)\n\nDisillusioned\n\nSLC Shoegaze\n\n[linktr.ee/disillusioned]"
            "(https://l.instagram.com/?u=https%3A%2F%2Flinktr.ee%2Fdisillusioned&e=AUB)")

    class Reader:
        def extract(self, url):
            assert url == "https://www.instagram.com/disillusioned_band/"
            return page

    s = Sess({"https://linktr.ee/disillusioned": Resp(200, f'"https://open.spotify.com/artist/{SP_ID}"')})
    hl = HandleLinker(Cache(tmp_path / "c.json"), session=s, web=Reader())
    assert hl.find("disillusioned_band") == {"spotify_id": SP_ID, "via": "link in Instagram bio (linktr.ee)"}


def test_album_link_in_bio_resolves_to_its_artist(tmp_path):
    album = "1yMOsgDs5OHA3fiDOVNGfX"
    hl = HandleLinker(Cache(tmp_path / "c.json"), session=Sess({}),
                      ig_fetch=lambda h: [f"utah emotional music open.spotify.com/album/{album}?si=1"])

    class Sp:
        def release_artists(self, kind, rid):
            assert (kind, rid) == ("album", album)
            return [{"id": "OTHERxxxxxxxxxxxxxxxxx", "name": "Someone"}, {"id": SP_ID, "name": "World Record"}]

        def get_artist(self, sid):
            return {"id": sid, "name": "World Record"}

    r = Resolver(Config(), Cache(tmp_path / "r.json"), None, None, Sp(), None, None, linker=hl, log=lambda *_: None)
    assert r._linked("World Record", "world_record_band")["choices"][0]["choice"]["spotify_id"] == SP_ID


def test_find_handle_from_search_results(tmp_path):
    from festify.websearch import WebSearch
    w = WebSearch("", Cache(tmp_path / "c.json"))
    w.search = lambda *a, **k: [{"url": "https://www.instagram.com/p/X", "title": "show",
                                 "content": "163 likes - disillusioned_band on September 18: disillusioned rules"}]
    assert w.find_handle("Disillusioned", ["Utah"]) == "disillusioned_band"
    assert w.find_handle("Helper", ["Utah"]) is None
