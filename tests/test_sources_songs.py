"""Mocked-API tests: Deezer exact matching, Last.fm parsing, and ISRC/title song picking."""
from festify.sources import Deezer, LastFm
from festify.songs import pick_songs


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get_json(self, url, params=None, retries=3):
        self.calls.append((url, params))
        for key, val in self.routes.items():
            if key in url or (params and key == params.get("method")):
                return val
        return None


def test_deezer_exact_artists_filters_and_sorts_by_fans():
    http = FakeHttp({"/search/artist": {"data": [
        {"id": 1, "name": "P.S. Destroy This", "nb_fan": 9},
        {"id": 2, "name": "PS Destroy This Tribute", "nb_fan": 999},
        {"id": 3, "name": "PS Destroy This", "nb_fan": 400},
    ]}})
    out = Deezer(http).exact_artists("PS Destroy This")
    assert [a["id"] for a in out] == [3, 1]


def test_deezer_error_payload_is_empty():
    assert Deezer(FakeHttp({"/artist/": {"error": {"type": "DataException"}}})).top_tracks(5) == []


def test_lastfm_info_and_name_check():
    info = {"artist": {"name": "Neon Hollow", "url": "u", "mbid": "", "stats": {"listeners": "120", "playcount": "900"},
                       "tags": {"tag": [{"name": "punk"}]}}}
    lf = LastFm("key", FakeHttp({"artist.getInfo": info}))
    assert lf.artist_info("neon hollow")["listeners"] == 120
    assert lf.artist_info("Totally Different") is None
    assert LastFm("", FakeHttp({})).artist_info("x") is None


class FakeDeezer:
    def __init__(self, tops, isrcs):
        self.tops, self.isrcs = tops, isrcs

    def top_tracks(self, aid, limit=25):
        return self.tops

    def track(self, tid):
        return {"isrc": self.isrcs.get(tid)}


class FakeSpotify:
    def __init__(self, by_isrc=None, by_query=None, artists=None):
        self.by_isrc, self.by_query, self.artists = by_isrc or {}, by_query or {}, artists or []
        self.queries = []

    def track_by_isrc(self, isrc):
        return self.by_isrc.get(isrc, [])

    def search_tracks(self, q, offset=0):
        self.queries.append(q)
        return [t for k, v in self.by_query.items() if k in q for t in v] if offset == 0 else []

    def search_artists(self, name, pages=2):
        return self.artists

    def artist_albums(self, aid, limit=10):
        return []

    def album_tracks(self, *a, **k):
        return []


def trk(name, artist="Neon Hollow", aid="SP1", uri=None):
    return {"id": name, "uri": uri or f"spotify:track:{name}", "name": name,
            "artists": [{"id": aid, "name": artist}], "album": ""}


CHOICE = {"cid": "deezer:10", "source": "deezer", "name": "Neon Hollow"}


def test_isrc_match_then_title_fallback_and_version_dedupe():
    deezer = FakeDeezer(
        tops=[{"id": 1, "title": "Alpha"}, {"id": 2, "title": "Alpha (Live)"}, {"id": 3, "title": "Beta"},
              {"id": 4, "title": "Gamma"}, {"id": 5, "title": "Delta"}],
        isrcs={1: "ISRC1", 2: "ISRC2", 3: None, 4: "ISRC4", 5: "ISRC5"},
    )
    sp = FakeSpotify(by_isrc={"ISRC1": [trk("Alpha")], "ISRC4": [trk("Gamma")], "ISRC5": [trk("Delta")]},
                     by_query={'track:"Beta"': [trk("Beta")]})
    res = pick_songs(sp, deezer, None, "Neon Hollow", CHOICE, n=3)
    assert [s.title for s in res.songs] == ["Alpha", "Beta", "Gamma"]
    assert [s.via for s in res.songs] == ["isrc", "title-search", "isrc"]
    assert res.spotify_artist_id == "SP1"
    assert "Deezer (ISRC)" in res.sources


def test_title_search_rejects_other_artists():
    deezer = FakeDeezer(tops=[{"id": 1, "title": "Alpha"}], isrcs={1: None})
    sp = FakeSpotify(by_query={'track:"Alpha"': [trk("Alpha", artist="Some Other Band", aid="X")]})
    res = pick_songs(sp, deezer, None, "Neon Hollow", CHOICE, n=3)
    assert res.songs == []


def test_title_search_accepts_normalized_artist_name():
    deezer = FakeDeezer(tops=[{"id": 1, "title": "Alpha"}], isrcs={1: None})
    sp = FakeSpotify(by_query={'track:"Alpha"': [trk("Alpha", artist="P.S. Neon Hollow", aid="X")],
                               })
    assert pick_songs(sp, deezer, None, "PS Neon Hollow", CHOICE, n=3).songs[0].title == "Alpha"


def test_spotify_only_fallback_filters_by_artist_id_and_dedupes():
    sp = FakeSpotify(
        artists=[{"id": "SP1", "name": "Neon Hollow"}],
        by_query={'artist:"Neon Hollow"': [trk("One"), trk("One (Remastered)", uri="spotify:track:One2"),
                                           trk("Two"), trk("Other", aid="ZZ")]},
    )
    res = pick_songs(sp, FakeDeezer([], {}), None, "Neon Hollow", {"cid": "deezer:10", "source": "deezer"}, n=3)
    assert [s.title for s in res.songs] == ["One", "Two"]
    assert all(s.source == "Spotify" for s in res.songs)


def test_ambiguous_spotify_artists_not_guessed():
    sp = FakeSpotify(artists=[{"id": "A", "name": "Neon Hollow"}, {"id": "B", "name": "Neon Hollow"}])
    res = pick_songs(sp, FakeDeezer([], {}), None, "Neon Hollow", {"cid": "deezer:10", "source": "deezer"}, n=3)
    assert res.songs == [] and res.notes
