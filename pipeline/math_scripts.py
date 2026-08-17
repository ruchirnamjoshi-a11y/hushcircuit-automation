"""Manifest schema and filesystem queue for the math_explainers track.

Mirrors pipeline.scripts's pattern (same queue directory shape, same
pending -> used move on success) but for a different content shape: no
Scene/character_reference/AI-illustration fields at all, since each piece's
visuals are the hand-authored canvas animation at
math_pieces/<piece>/piece.html (see pipeline.canvas_video), not anything
generated per-script. A manifest only supplies the narration and which
piece to pair it with.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pipeline.config import MATH_PIECES_DIR, QUEUE_PENDING_DIR

REQUIRED_FIELDS = {"id", "title", "description", "piece", "narration_lines"}


@dataclass
class MathScript:
    id: str
    title: str
    description: str
    piece: str  # math_pieces/<piece>/piece.html must exist
    narration_lines: list[str]
    tags: list[str] = field(default_factory=list)


class MathScriptValidationError(ValueError):
    pass


def validate(data: dict) -> MathScript:
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        raise MathScriptValidationError(f"math script missing required fields: {sorted(missing)}")

    narration_lines = data["narration_lines"]
    if not isinstance(narration_lines, list) or len(narration_lines) < 1:
        raise MathScriptValidationError("math script needs at least 1 narration line")

    piece = data["piece"]
    piece_html = MATH_PIECES_DIR / piece / "piece.html"
    if not piece_html.exists():
        raise MathScriptValidationError(f"no piece found at {piece_html} (referenced by piece={piece!r})")

    return MathScript(
        id=data["id"],
        title=data["title"],
        description=data["description"],
        tags=list(data.get("tags", [])),
        piece=piece,
        narration_lines=list(narration_lines),
    )


def load_next_pending(queue_dir: Path = QUEUE_PENDING_DIR) -> Optional[tuple[Path, MathScript]]:
    """Returns (path, MathScript) for the oldest queued file, or None if the queue is empty."""
    pending = sorted(p for p in queue_dir.glob("*.json"))
    if not pending:
        return None
    path = pending[0]
    data = json.loads(path.read_text())
    return path, validate(data)
