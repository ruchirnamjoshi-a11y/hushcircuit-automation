"""Stage 5: Pillow-generated thumbnail — the same brand gradient used for
video backgrounds, with bold overlay text. No video-frame extraction needed,
which keeps the thumbnail visually consistent with the video itself. Canva's
API isn't scriptable on a free plan, so this is the default; README documents
Canva as an optional manual override.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from pipeline.config import THUMBNAIL_RESOLUTION
from pipeline.textcard import make_gradient_image

ACCENT_COLOR = (255, 195, 80)  # matches HEADLINE_COLOR_ASS in ffmpeg_utils.py


def _wrap_text(text: str, max_chars_per_line: int = 16, max_lines: int = 3) -> list[str]:
    return textwrap.wrap(text, width=max_chars_per_line, break_long_words=False)[:max_lines]


def generate_thumbnail(
    text: str,
    out_path: Path,
    resolution: tuple[int, int] = THUMBNAIL_RESOLUTION,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gradient_path = out_path.parent / f"_{out_path.stem}_gradient.png"
    make_gradient_image(gradient_path, resolution)

    base = ImageOps.fit(Image.open(gradient_path).convert("RGB"), resolution, Image.LANCZOS).convert("RGBA")

    width, height = resolution
    overlay = Image.new("RGBA", resolution, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle([0, int(height * 0.55), width, height], fill=(0, 0, 0, 130))
    composed = Image.alpha_composite(base, overlay).convert("RGB")

    draw = ImageDraw.Draw(composed)
    font_size = int(height * 0.13)
    font = ImageFont.load_default(size=font_size)
    lines = _wrap_text(text.upper())
    line_height = int(font_size * 1.15)
    y = height - int(height * 0.06) - line_height * len(lines)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=6)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text((x, y), line, font=font, fill=ACCENT_COLOR, stroke_width=6, stroke_fill=(0, 0, 0))
        y += line_height

    composed.save(out_path, "JPEG", quality=92)
    gradient_path.unlink(missing_ok=True)
    return out_path
