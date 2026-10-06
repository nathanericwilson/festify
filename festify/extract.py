"""Step 1: read posters with Claude vision, then merge duplicate events."""
from __future__ import annotations

import base64
import datetime as dt
import difflib
import io
from dataclasses import dataclass, field
from pathlib import Path

from .instagram import PostInput
from .names import normalize_name

SYSTEM = """You read concert/festival posters and return STRICT JSON only (no prose, no code fences).

Schema: {"events": [{"name": str, "date": str, "venue": str, "city": str, "genre_hints": [str],
"artists": [str], "unreadable": [str], "handle_map": [{"artist": str, "handle": str, "inferred": bool}]}]}

Rules:
- A multi-venue festival spread across several slides is ONE event with all artists combined.
- Copy artist names EXACTLY as printed (spelling, punctuation, capitalization).
- Do NOT guess stylized or illegible logos/lettering from the artwork alone.
- TAGGED @HANDLES: Instagram tags usually include every band playing. You get a list of tagged/mentioned handles, and tag bubbles may also be visible on the images (read those too). For each handle decide: band/performer, or venue/promoter/sponsor/designer (never include those as performers; venue tags often match a venue name printed on the poster).
  * If a band handle matches a printed name, add it to "handle_map" with inferred=false (it also confirms spelling).
  * If a band handle has NO printed counterpart (stylized/illegible lettering, or a lineup slot you cannot read), add the band to "artists" using the most natural reading of the handle as the name (e.g. 'hit.confirm' -> 'Hit Confirm'; drop suffixes like 'band', 'official', 'slc', 'music' only when clearly decorative) and put it in "handle_map" with inferred=true. Remove the matching entry from "unreadable".
  * Anything still unidentifiable goes under "unreadable" (describe where it is on the poster).
  * Handles of unclear type (could be a band or a venue): include only if the poster layout suggests a performer.
- "date": ISO YYYY-MM-DD if the year is printed or can be sensibly inferred from today's date; otherwise exactly as printed. Use "" if absent.
- "city": city of the show; "" if absent. "genre_hints": genres printed or clearly implied; [] if none.
- If the images contain several distinct events, return several events."""


@dataclass
class Event:
    name: str
    date: str = ""
    venue: str = ""
    city: str = ""
    genre_hints: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    cover_image: str = ""  # path of the post image, used as the playlist cover
    handle_map: dict = field(default_factory=dict)  # normalized artist -> {"handle", "inferred"}

    @staticmethod
    def from_dict(d: dict) -> "Event":
        un = d.get("unreadable") or []
        if isinstance(un, str):
            un = [un] if un.strip() else []
        return Event(
            name=str(d.get("name") or "").strip(),
            date=str(d.get("date") or "").strip(),
            venue=str(d.get("venue") or "").strip(),
            city=str(d.get("city") or "").strip(),
            genre_hints=[str(x) for x in d.get("genre_hints") or []],
            artists=[str(x).strip() for x in d.get("artists") or [] if str(x).strip()],
            unreadable=[str(x) for x in un],
            handle_map=_handle_map(d.get("handle_map")),
        )


def _handle_map(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    out = {}
    for it in raw or []:
        if isinstance(it, dict) and it.get("artist") and it.get("handle"):
            out[normalize_name(str(it["artist"]))] = {"handle": str(it["handle"]).lstrip("@"),
                                                       "inferred": bool(it.get("inferred"))}
    return out


def _union(a: list[str], b: list[str]) -> list[str]:
    out, seen = list(a), {normalize_name(x) for x in a}
    for x in b:
        if normalize_name(x) not in seen:
            seen.add(normalize_name(x))
            out.append(x)
    return out


def _same_event(a: Event, b: Event) -> bool:
    na, nb = normalize_name(a.name), normalize_name(b.name)
    if not na or not nb:
        return False
    similar = na == nb or na in nb or nb in na or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8
    if a.date and b.date and normalize_name(a.date) != normalize_name(b.date):
        return False
    if a.date and b.date:
        return similar or bool(set(map(normalize_name, a.artists)) & set(map(normalize_name, b.artists)))
    return na == nb or similar


def merge_events(events: list[Event]) -> list[Event]:
    merged: list[Event] = []
    for ev in events:
        for m in merged:
            if _same_event(m, ev):
                m.artists = _union(m.artists, ev.artists)
                m.genre_hints = _union(m.genre_hints, ev.genre_hints)
                m.unreadable = _union(m.unreadable, ev.unreadable)
                m.handle_map = {**ev.handle_map, **m.handle_map}
                for f in ("date", "venue", "city", "cover_image"):
                    if not getattr(m, f):
                        setattr(m, f, getattr(ev, f))
                if len(ev.name) > len(m.name):
                    m.name = ev.name
                break
        else:
            merged.append(Event(**{**ev.__dict__}))
    return merged


def _image_block(path: Path) -> dict:
    from PIL import Image

    raw = path.read_bytes()
    img = Image.open(io.BytesIO(raw))
    if len(raw) > 4_500_000 or max(img.size) > 3000 or img.format not in ("PNG", "JPEG", "WEBP", "GIF"):
        img = img.convert("RGB")
        img.thumbnail((3000, 3000))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        raw, media = buf.getvalue(), "image/jpeg"
    else:
        media = Image.MIME.get(img.format, "image/jpeg")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(raw).decode()}}


def extract_events(llm, post: PostInput) -> list[Event]:
    content = []
    for i, p in enumerate(post.images, 1):
        content.append({"type": "text", "text": f"Image {i} of {len(post.images)}:"})
        content.append(_image_block(p))
    note = f"Today's date: {dt.date.today().isoformat()}.\n"
    note += f"Instagram caption:\n{post.caption}\n" if post.caption else "No caption available.\n"
    if post.handles:
        note += "Tagged/mentioned handles: " + ", ".join("@" + h for h in post.handles) + "\n"
    else:
        note += "No handle list available (read any tag bubbles visible on the images).\n"
    content.append({"type": "text", "text": note + "Return the JSON now."})
    data = llm.ask_json(SYSTEM, content)
    items = data.get("events", data) if isinstance(data, dict) else data
    events = [Event.from_dict(d) for d in items if isinstance(d, dict)]
    for e in events:
        e.cover_image = str(post.images[0]) if post.images else ""
    return events
