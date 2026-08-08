"""Tiny per-track state: which track already produced (and uploaded) a
video today. The daily workflow runs multiple times a day so a run that
aborts early (Cloudflare image quota exhausted) gets retried later the same
day — this file is what stops that retry from producing a *second* video
for a track that already succeeded earlier in the day."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.config import ROOT_DIR

STATE_DIR = ROOT_DIR / "scripts_queue" / "state"


def _state_path(track_key: str, state_dir: Path = STATE_DIR) -> Path:
    return state_dir / f"{track_key}.json"


def already_produced_today(track_key: str, state_dir: Path = STATE_DIR) -> bool:
    path = _state_path(track_key, state_dir)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return data.get("last_produced_date") == date.today().isoformat()


def mark_produced_today(track_key: str, state_dir: Path = STATE_DIR) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_path(track_key, state_dir).write_text(
        json.dumps({"last_produced_date": date.today().isoformat()})
    )
