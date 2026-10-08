from festify.gui import build_args


def test_new_post_args():
    args = build_args({"links": ["https://www.instagram.com/p/X/", " "], "dry_run": True, "tracks": "4"}, ["C:/tmp/upload"])
    assert args == ["https://www.instagram.com/p/X/", "C:/tmp/upload", "--save-events", "events.json",
                    "--dry-run", "--tracks", "4"]


def test_update_all_with_pin():
    args = build_args({"resume": True, "links": ["ignored"], "pins": ["Skrude=https://open.spotify.com/artist/ID", ""]}, [])
    assert args == ["--pin", "Skrude=https://open.spotify.com/artist/ID", "--events-file", "events.json"]


def test_find_playlist_tolerates_renamed_event(tmp_path):
    from festify.cache import Cache
    from festify.cli import find_playlist
    from festify.extract import Event

    c = Cache(tmp_path / "c.json")
    c.set("playlists", "provo 2026 10 17 the jungle", {"id": "P", "url": "u1"})
    c.set("playlists", "dennys grand slam fest 2026 10 17", {"id": "D", "url": "u2"})
    c.set("playlists", "all saints music festival guru s thrift rx", {"id": "A", "url": "u3"})
    assert find_playlist(c, Event(name="Provo", date="2026-10-17", venue="The Jungle"))[1]["id"] == "P"
    assert find_playlist(c, Event(name="Denny's Grand Slam Fest", date="2026-10-17"))[1]["id"] == "D"
    assert find_playlist(c, Event(name="All Saints Music Festival", venue="Guru's"))[1]["id"] == "A"
    assert find_playlist(c, Event(name="Some Other Show", date="2026-10-17"))[1] is None
