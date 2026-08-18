"""Stage 3: branded animated backgrounds — Ken Burns zoom over either an
AI-generated scene illustration (pipeline.ai_image) or, as a fallback/plain
mode, a generated navy-to-violet gradient with drifting glow orbs. Both share
the same zoompan animation engine below. pipeline/broll.py is kept intact but
unused by the default pipeline in case a hybrid look is wanted later.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pipeline.ffmpeg_utils import FPS, run_ffmpeg

GRADIENT_TOP = (12, 20, 48)      # deep navy
GRADIENT_BOTTOM = (35, 16, 64)   # deep violet
OVERSCAN = 4.0  # supersample factor feeding zoompan -- see _zoompan_clip's comment;
# this is not about "room to move," it's about giving zoompan's internal
# integer-pixel crop-position rounding enough sub-pixel headroom that a
# 1px snap in the supersampled source is well under 1px in the final
# output.
ZOOM_PER_FRAME = 0.0006
MAX_ZOOM = 1.12

ORB_COLOR_GOLD = (255, 195, 80)   # matches the accent/headline color
ORB_COLOR_VIOLET = (110, 80, 210)
ORB_SOURCE_SIZE = 240  # small on purpose — orbs are meant to look soft/out-of-focus


def make_gradient_image(path: Path, resolution: tuple[int, int], overscan: float = 1.0) -> Path:
    """Renders the brand gradient at `resolution` (or larger, via `overscan`,
    to leave room for zoompan motion). Also used directly by thumbnail.py so
    the thumbnail matches the video's background exactly, and as the
    fallback image in pipeline.ai_image when the AI API call fails."""
    width, height = resolution
    render_w, render_h = int(width * overscan), int(height * overscan)

    # Build a thin vertical gradient strip, then stretch it — avoids a
    # pure-Python double loop over every pixel (slow at ~2000x2000+).
    strip = Image.new("RGB", (1, render_h))
    for y in range(render_h):
        t = y / max(1, render_h - 1)
        strip.putpixel((0, y), tuple(
            int(GRADIENT_TOP[c] + (GRADIENT_BOTTOM[c] - GRADIENT_TOP[c]) * t) for c in range(3)
        ))

    path.parent.mkdir(parents=True, exist_ok=True)
    strip.resize((render_w, render_h)).save(path)
    return path


def _make_orb_image(path: Path, color: tuple[int, int, int], max_alpha: int, size: int = ORB_SOURCE_SIZE) -> Path:
    """A small soft-edged translucent circle, later blurred + upscaled by
    ffmpeg so it reads as an out-of-focus glow rather than a hard disc."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    cx = cy = size / 2
    max_r = size / 2
    for y in range(size):
        dy = y - cy
        for x in range(size):
            dx = x - cx
            d = (dx * dx + dy * dy) ** 0.5
            if d <= max_r:
                t = d / max_r
                alpha = int(max_alpha * (1 - t) ** 2)
                if alpha > 0:
                    px[x, y] = (*color, alpha)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _zoompan_clip(
    image_path: Path,
    resolution: tuple[int, int],
    duration: float,
    out_path: Path,
    with_orbs: bool,
) -> Path:
    """Shared animation engine: a slow center-zoom over `image_path`, with
    two drifting glow orbs layered on top if `with_orbs` (used for the plain
    gradient background; skipped for AI images, which already have their own
    visual interest and would look cluttered with orbs on top)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = resolution
    frames = max(1, int(round(duration * FPS)))
    zoom_expr = f"min(zoom+{ZOOM_PER_FRAME},{MAX_ZOOM})"

    inputs = ["-loop", "1", "-i", str(image_path)]
    # The real shake mechanism (confirmed via frame-by-frame ORB motion
    # analysis, not just eyeballing): ffmpeg's zoompan filter computes its
    # crop x/y from floating-point expressions but the actual crop position
    # is an integer pixel offset, re-derived independently every frame. At
    # low supersampling, tiny per-frame zoom deltas (ZOOM_PER_FRAME) round
    # to the *same* integer offset most frames, then jump 1px whenever the
    # fractional part crosses a boundary — in x and y independently, so it
    # reads as random 1-2px trembling, not smooth motion. Measured on real
    # output: ~1px mean / ~2.4px peak frame-to-frame jitter at OVERSCAN=1.15
    # (a static, non-zoomed control video measured exactly 0px with the same
    # method, ruling out encoder/measurement noise) vs. ~0.01px mean / 0.17px
    # peak at OVERSCAN=4.0 — i.e. supersampling amount, not scaler choice,
    # is what actually governs this. flags=lanczos below is still correct
    # (it fixes a separate, real resampling-quality issue) but does nothing
    # for this rounding jitter on its own.
    overscan_w, overscan_h = int(width * OVERSCAN), int(height * OVERSCAN)
    stages = [
        f"[0:v]scale={overscan_w}:{overscan_h}:flags=lanczos[src]",
        f"[src]zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={FPS}[bg]"
    ]
    video_out = "[bg]"
    cleanup_paths: list[Path] = []

    if with_orbs:
        orb_gold_path = out_path.parent / f"_{out_path.stem}_orb_gold.png"
        orb_violet_path = out_path.parent / f"_{out_path.stem}_orb_violet.png"
        _make_orb_image(orb_gold_path, ORB_COLOR_GOLD, max_alpha=150)
        _make_orb_image(orb_violet_path, ORB_COLOR_VIOLET, max_alpha=180)
        cleanup_paths += [orb_gold_path, orb_violet_path]

        orb_gold_size = int(width * 0.32)
        orb_violet_size = int(width * 0.28)
        gold_cx, gold_cy = width * 0.26, height * 0.28
        violet_cx, violet_cy = width * 0.74, height * 0.70

        inputs += ["-loop", "1", "-i", str(orb_gold_path), "-loop", "1", "-i", str(orb_violet_path)]
        stages += [
            f"[1:v]scale={orb_gold_size}:{orb_gold_size},gblur=sigma=14,fps={FPS}[orbg]",
            f"[2:v]scale={orb_violet_size}:{orb_violet_size},gblur=sigma=14,fps={FPS}[orbv]",
            f"[bg][orbg]overlay=format=auto:"
            f"x='{gold_cx - orb_gold_size / 2}+{width * 0.06}*sin(2*PI*t/9)':"
            f"y='{gold_cy - orb_gold_size / 2}+{height * 0.06}*cos(2*PI*t/11)'[bg1]",
            f"[bg1][orbv]overlay=format=auto:"
            f"x='{violet_cx - orb_violet_size / 2}+{width * 0.06}*cos(2*PI*t/13)':"
            f"y='{violet_cy - orb_violet_size / 2}+{height * 0.06}*sin(2*PI*t/8)'[bg2]",
        ]
        video_out = "[bg2]"

    stages.append(f"{video_out}noise=alls=2:allf=t+u[outv]")

    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(stages),
        "-map", "[outv]",
        "-t", f"{duration:.3f}",
        # -preset slow -crf 16 (not veryfast/20): the low-quality encode was
        # compounding the pre-lanczos shake by re-quantizing already-jittery
        # sub-pixel motion every frame — the higher-quality encode alone
        # doesn't fix shake, but combined with lanczos scaling above it was
        # the confirmed-working combination (see comment above).
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
        str(out_path),
    ])

    for p in cleanup_paths:
        p.unlink(missing_ok=True)
    return out_path


def generate_background_clip(
    resolution: tuple[int, int],
    duration: float,
    out_path: Path,
) -> Path:
    """The plain abstract background: gradient + two drifting glow orbs. Used
    as pipeline.ai_image's fallback image source when the AI API is
    unavailable, and directly by anything that wants the non-AI look."""
    gradient_path = out_path.parent / f"_{out_path.stem}_gradient.png"
    make_gradient_image(gradient_path, resolution, overscan=OVERSCAN)
    result = _zoompan_clip(gradient_path, resolution, duration, out_path, with_orbs=True)
    gradient_path.unlink(missing_ok=True)
    return result


def generate_image_background_clip(
    image_path: Path,
    resolution: tuple[int, int],
    duration: float,
    out_path: Path,
) -> Path:
    """Ken Burns zoom over a pre-supplied image (an AI-generated scene
    illustration from pipeline.ai_image, or the gradient it falls back to) —
    a drop-in replacement for a b-roll clip path anywhere build_scene_clip()
    is used. No orbs: the source image already carries the visual interest."""
    return _zoompan_clip(image_path, resolution, duration, out_path, with_orbs=False)
