"""Tiny per-track state: how many videos a track has already produced (and
uploaded) today, against that track's own daily limit (Track.videos_per_day
-- 1 for every existing track, 2 for math_explainers). The daily workflow
runs multiple times a day, so a run that aborts early (Cloudflare image
quota exhausted) gets retried later the same day, and a track that already
hit its daily limit earlier isn't asked to produce again until the count
resets tomorrow."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.config import ROOT_DIR

STATE_DIR = ROOT_DIR / "scripts_queue" / "state"


def _state_path(track_key: str, state_dir: Path = STATE_DIR) -> Path:
    return state_dir / f"{track_key}.json"


def produced_count_today(track_key: str, state_dir: Path = STATE_DIR) -> int:
    """How many videos this track has already produced today (0 if none,
    or if the stored state is from an earlier day)."""
    path = _state_path(track_key, state_dir)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if data.get("last_produced_date") != date.today().isoformat():
        return 0
    return int(data.get("count", 0))


def already_produced_today(track_key: str, limit: int = 1, state_dir: Path = STATE_DIR) -> bool:
    """True once this track has hit `limit` videos today (Track.videos_per_
    day — 1 for every existing track, 2 for math_explainers)."""
    return produced_count_today(track_key, state_dir) >= limit


def mark_produced_today(track_key: str, state_dir: Path = STATE_DIR) -> None:
    """Increments today's count (starting fresh at 1 if the stored state is
    from an earlier day, or this is the first video produced today)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    count = produced_count_today(track_key, state_dir) + 1
    _state_path(track_key, state_dir).write_text(
        json.dumps({"last_produced_date": date.today().isoformat(), "count": count})
    )
