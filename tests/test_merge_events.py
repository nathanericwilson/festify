from festify.extract import Event, merge_events
from festify.llm import extract_json


def ev(name, date="", artists=(), city="", unreadable=()):
    return Event(name=name, date=date, city=city, artists=list(artists), unreadable=list(unreadable))


def test_merges_same_event_across_slides_and_unions_artists():
    a = ev("Velour Fest", "2026-11-07", ["Band A", "The Band B"], "Provo")
    b = ev("VELOUR FEST", "2026-11-07", ["band b", "Band C"], "", ["logo, slide 2"])
    out = merge_events([a, b])
    assert len(out) == 1
    assert out[0].artists == ["Band A", "The Band B", "Band C"]
    assert out[0].city == "Provo"
    assert out[0].unreadable == ["logo, slide 2"]


def test_different_dates_stay_separate():
    out = merge_events([ev("Velour Night", "2026-11-07"), ev("Velour Night", "2026-11-14")])
    assert len(out) == 2


def test_missing_date_merges_on_similar_name():
    out = merge_events([ev("Kilby Court Showcase", "", ["A"]), ev("Kilby Court Showcase Night", "2026-12-01", ["B"])])
    assert len(out) == 1 and out[0].date == "2026-12-01" and out[0].artists == ["A", "B"]


def test_unrelated_events_not_merged():
    out = merge_events([ev("Punk Night", "2026-11-07", ["A"]), ev("Folk Night", "2026-11-07", ["B"])])
    assert len(out) == 2


def test_merge_does_not_mutate_inputs():
    a = ev("X Fest", "2026-01-01", ["A"])
    merge_events([a, ev("X Fest", "2026-01-01", ["B"])])
    assert a.artists == ["A"]


def test_from_dict_handles_string_unreadable_and_missing_fields():
    e = Event.from_dict({"name": "N", "unreadable": "blurry logo", "artists": ["A", " "], "genre_hints": None})
    assert e.unreadable == ["blurry logo"] and e.artists == ["A"] and e.genre_hints == []


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"results": [{"x": 1}]} Hope that helps') == {"results": [{"x": 1}]}


def test_handle_map_parsed_and_merged():
    a = Event.from_dict({"name": "Show", "date": "2026-10-17", "artists": ["Hit Confirm"],
                         "handle_map": [{"artist": "Hit Confirm", "handle": "@hit.confirm", "inferred": True}]})
    b = Event.from_dict({"name": "Show", "date": "2026-10-17", "artists": ["Chain High"],
                         "handle_map": [{"artist": "Chain High", "handle": "chain_high", "inferred": False}]})
    out = merge_events([a, b])[0]
    assert out.handle_map["hit confirm"] == {"handle": "hit.confirm", "inferred": True}
    assert out.handle_map["chain high"]["handle"] == "chain_high"
