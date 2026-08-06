"""Stage 5: Pillow-generated thumbnail — a frame from the strongest b-roll
clip with bold overlay text. Canva's API isn't scriptable on a free plan, so
this is the default; README documents Canva as an optional manual override.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from pipeline.config import THUMBNAIL_RESOLUTION
from pipeline.ffmpeg_utils import run_ffmpeg


def extract_frame(video_path: Path, out_path: Path, timestamp: float = 1.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", str(timestamp), "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(out_path)])
    return out_path


def _wrap_text(text: str, max_chars_per_line: int = 16, max_lines: int = 3) -> list[str]:
    return textwrap.wrap(text, width=max_chars_per_line, break_long_words=False)[:max_lines]


def generate_thumbnail(
    source_video: Path,
    text: str,
    out_path: Path,
    resolution: tuple[int, int] = THUMBNAIL_RESOLUTION,
    frame_timestamp: float = 1.0,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path = out_path.parent / f"_{out_path.stem}_frame.jpg"
    extract_frame(source_video, frame_path, timestamp=frame_timestamp)

    base = ImageOps.fit(Image.open(frame_path).convert("RGB"), resolution, Image.LANCZOS).convert("RGBA")

    width, height = resolution
    overlay = Image.new("RGBA", resolution, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle([0, int(height * 0.55), width, height], fill=(0, 0, 0, 160))
    composed = Image.alpha_composite(base, overlay).convert("RGB")

    draw = ImageDraw.Draw(composed)
    font_size = int(height * 0.11)
    font = ImageFont.load_default(size=font_size)
    lines = _wrap_text(text.upper())
    line_height = int(font_size * 1.15)
    y = height - int(height * 0.06) - line_height * len(lines)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=6)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=6, stroke_fill=(0, 0, 0))
        y += line_height

    composed.save(out_path, "JPEG", quality=92)
    frame_path.unlink(missing_ok=True)
    return out_path
