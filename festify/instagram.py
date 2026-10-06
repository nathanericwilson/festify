"""Collect poster images + captions from Instagram links, image files and folders."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IG_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)", re.I)


_warned_session = False


class IGFetchError(Exception):
    pass


@dataclass
class PostInput:
    label: str
    images: list[Path] = field(default_factory=list)
    caption: str = ""
    handles: list[str] = field(default_factory=list)  # tagged / mentioned @handles


def fetch_instagram(url: str, ig_username: str = "") -> PostInput:
    m = IG_RE.search(url)
    if not m:
        raise IGFetchError(f"Not a recognizable Instagram post/reel URL: {url}")
    shortcode = m.group(1)
    hint = "Instagram often blocks this. Take screenshots of the post slides instead and pass the image files (or a folder)."
    try:
        import instaloader

        loader = instaloader.Instaloader(download_videos=False, save_metadata=False, quiet=True,
                                       max_connection_attempts=1)
        if ig_username:
            try:
                loader.load_session_from_file(ig_username)
            except FileNotFoundError:
                global _warned_session
                if not _warned_session:
                    _warned_session = True
                    print(f"Note: no saved Instagram session for '{ig_username}' (run `instaloader -l {ig_username}` "
                          f"to create one). Trying anonymously.")
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        urls = [n.display_url for n in post.get_sidecar_nodes()] if post.typename == "GraphSidecar" else [post.url]
        tmp = Path(tempfile.mkdtemp(prefix="festify_ig_"))
        paths = []
        for i, u in enumerate(urls):
            r = requests.get(u, timeout=30)
            r.raise_for_status()
            p = tmp / f"{shortcode}_{i}.jpg"
            p.write_bytes(r.content)
            paths.append(p)
        handles = list(dict.fromkeys([*post.tagged_users, *post.caption_mentions]))
        return PostInput(label=url, images=paths, caption=post.caption or "", handles=handles)
    except IGFetchError:
        raise
    except Exception as e:  # instaloader raises many exception types for blocks/login walls
        raise IGFetchError(f"Could not fetch {url} ({type(e).__name__}: {e}). {hint}")


def images_in(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in IMG_EXT)
    return [path] if path.suffix.lower() in IMG_EXT else []


def collect_inputs(inputs: list[str], ig_username: str = "", warn=print) -> list[PostInput]:
    """URLs -> one post each; folders -> one post each; loose image files -> one shared post."""
    posts: list[PostInput] = []
    loose: list[Path] = []
    for item in inputs:
        if item.lower().startswith(("http://", "https://")) or "instagram.com" in item.lower():
            try:
                posts.append(fetch_instagram(item, ig_username))
            except IGFetchError as e:
                warn(f"! {e}")
            continue
        p = Path(item)
        if not p.exists():
            warn(f"! Not found: {item}")
        elif p.is_dir():
            imgs = images_in(p)
            if imgs:
                posts.append(PostInput(label=str(p), images=imgs))
            else:
                warn(f"! No images in folder: {item}")
        else:
            imgs = images_in(p)
            if imgs:
                loose.extend(imgs)
            else:
                warn(f"! Not an image: {item}")
    if loose:
        posts.append(PostInput(label="screenshots", images=loose))
    return posts
