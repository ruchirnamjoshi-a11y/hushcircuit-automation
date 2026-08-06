"""Stage 3: b-roll fetch. Pexels primary, Pixabay fallback. Both free tiers.

Videos are always fetched in landscape orientation; shorts.py reframes the same
clips to vertical later rather than fetching a second copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from pipeline.config import PEXELS_API_KEY, PIXABAY_API_KEY

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60


class BrollNotFoundError(RuntimeError):
    pass


@dataclass
class BrollClip:
    path: Path
    duration: float
    source: str


def _download(url: str, out_dir: Path, filename: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    if path.exists() and path.stat().st_size > 0:
        return path
    resp = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    with open(path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return path


def _pick_pexels_file(video_files: list[dict]) -> Optional[dict]:
    mp4_files = [f for f in video_files if f.get("file_type") == "video/mp4" and f.get("width")]
    if not mp4_files:
        return None
    return min(mp4_files, key=lambda f: abs(f["width"] - 1920))


def _fetch_from_pexels(keyword: str, min_duration: float, out_dir: Path) -> Optional[BrollClip]:
    if not PEXELS_API_KEY:
        return None
    try:
        resp = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": keyword, "orientation": "landscape", "per_page": 15},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
    except requests.RequestException:
        return None

    candidates = sorted(videos, key=lambda v: (v["duration"] < min_duration, abs(v["duration"] - min_duration)))
    for v in candidates:
        file = _pick_pexels_file(v.get("video_files", []))
        if file is None:
            continue
        try:
            path = _download(file["link"], out_dir, f"pexels_{v['id']}.mp4")
        except requests.RequestException:
            continue
        return BrollClip(path=path, duration=v["duration"], source="pexels")
    return None


def _fetch_from_pixabay(keyword: str, min_duration: float, out_dir: Path) -> Optional[BrollClip]:
    if not PIXABAY_API_KEY:
        return None
    try:
        resp = requests.get(
            PIXABAY_SEARCH_URL,
            params={"key": PIXABAY_API_KEY, "q": keyword, "video_type": "film", "per_page": 15},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except requests.RequestException:
        return None

    candidates = sorted(hits, key=lambda h: (h["duration"] < min_duration, abs(h["duration"] - min_duration)))
    for h in candidates:
        videos = h.get("videos", {})
        file = videos.get("large") or videos.get("medium") or videos.get("small")
        if not file:
            continue
        try:
            path = _download(file["url"], out_dir, f"pixabay_{h['id']}.mp4")
        except requests.RequestException:
            continue
        return BrollClip(path=path, duration=h["duration"], source="pixabay")
    return None


def fetch_broll(
    keyword: str,
    min_duration: float,
    out_dir: Path,
    cache: Optional[dict[str, BrollClip]] = None,
) -> BrollClip:
    if cache is not None and keyword in cache:
        return cache[keyword]

    clip = _fetch_from_pexels(keyword, min_duration, out_dir) or _fetch_from_pixabay(keyword, min_duration, out_dir)
    if clip is None:
        raise BrollNotFoundError(f"no b-roll found for keyword '{keyword}' on Pexels or Pixabay")

    if cache is not None:
        cache[keyword] = clip
    return clip
