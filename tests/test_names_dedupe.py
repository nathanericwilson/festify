import pytest

from festify.names import base_title, dedupe_tracks, is_alt_version, names_match, normalize_name


@pytest.mark.parametrize("a,b", [
    ("PS Destroy This", "P.S. Destroy This"),
    ("Brooks & Dunn", "Brooks and Dunn"),
    ("The Aces", "Aces"),
    ("the AMERICAN Dream", "American Dream"),
    ("Beyoncé", "Beyonce"),
    ("Mom's Spaghetti", "Moms Spaghetti"),
    ("Rock-n-Roll Kids", "Rock n Roll Kids"),
])
def test_names_match(a, b):
    assert names_match(a, b)


@pytest.mark.parametrize("a,b", [("Aces", "Ace"), ("Neon Trees", "Neon Tree Frogs"), ("The Used", "Used Cars")])
def test_names_differ(a, b):
    assert not names_match(a, b)


def test_normalize_symbol_only_name_falls_back():
    assert normalize_name("!!!") == "!!!"


def test_base_title_strips_versions():
    assert base_title("Song (Remastered 2011)") == base_title("Song")
    assert base_title("Song - Live at Kilby Court") == base_title("Song")
    assert base_title("Song (feat. Someone)") == base_title("Song")
    assert base_title("Song - 2009 Remaster") == base_title("Song")
    assert base_title("Song (Acoustic)") == base_title("Song")


def test_base_title_keeps_real_parentheticals():
    assert base_title("Song (Part II)") != base_title("Song")
    assert base_title("Hello - Part 2") != base_title("Hello")


def test_is_alt_version():
    assert is_alt_version("Song (Live)")
    assert is_alt_version("Song - Remastered")
    assert not is_alt_version("Song")
    assert not is_alt_version("Song (Part II)")


def test_dedupe_prefers_original_even_when_alt_comes_first():
    items = [{"t": "Song (Live)"}, {"t": "Other"}, {"t": "Song"}, {"t": "Song - Remastered"}]
    out = dedupe_tracks(items, key=lambda x: x["t"])
    assert [x["t"] for x in out] == ["Song", "Other"]


def test_dedupe_keeps_order_of_first_seen():
    out = dedupe_tracks(["B", "A", "b (Acoustic)", "C"])
    assert out == ["B", "A", "C"]
