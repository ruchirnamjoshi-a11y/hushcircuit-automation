"""Stage 3b (math_explainers track only): renders a hand-authored canvas
animation "piece" (math_pieces/<piece_id>/piece.html) into a real video with
synced narration. No AI image or video generation involved anywhere in this
path — the visuals are code, rendered deterministically by a real browser —
so this stage has zero external API cost, unlike pipeline.ai_image.

Two-part contract with a piece's HTML/JS:
  1. Each narration line is synthesized separately (pipeline.tts, same
     per-line pattern as story scenes) so the piece can be timed against
     REAL speech durations instead of hand-guessed offsets.
  2. Those real per-line start/end times are injected into the page as
     window.__LINE_TIMES__ before the piece's own script runs. Every piece
     built on pipeline/canvas_lib/helpers.js reads that to build its own
     internal timeline (see math_pieces/gauss_trick/piece.html for the
     pattern) — the piece's visual/timing LOGIC stays hand-authored, only
     the real-world seconds come from this module.

Frame capture is deterministic (step window.__seek(t) frame-by-frame, then
screenshot), not real-time recording — slower (~200ms/frame; a ~50s piece
takes several minutes to render) but glitch-free regardless of how fast the
render machine is, which matters more for unattended CI than render speed.
Swapping in Playwright's real-time record_video_dir would be ~6-7x faster
if verified to stay in sync with __seek-driven pieces — not done here since
it hasn't been validated against this render pattern.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from pipeline.config import MUSIC_VOLUME_DB
from pipeline.ffmpeg_utils import mix_final, run_ffmpeg
from pipeline.tts import SceneAudio, synthesize_scene

FPS = 30
TAIL_BUFFER_SECONDS = 1.0  # a beat of settle time after the last line ends


@dataclass
class LineTime:
    start: float
    end: float


def synthesize_narration_lines(
    lines: list[str], out_dir: Path, voice: str,
) -> tuple[Path, list[LineTime]]:
    """Synthesizes each narration line separately via edge-tts (real
    per-line duration, same pattern as story scenes — see pipeline.tts),
    concatenates them into one narration track, and returns each line's
    real cumulative start/end offset for the piece to sync against."""
    out_dir.mkdir(parents=True, exist_ok=True)
    audios: list[SceneAudio] = [
        synthesize_scene(text, out_dir, f"line_{i:02d}", voice=voice)
        for i, text in enumerate(lines)
    ]

    concat_list = out_dir / "concat_list.txt"
    concat_list.write_text("".join(f"file '{a.audio_path.name}'\n" for a in audios))
    narration_path = out_dir / "narration.mp3"
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", "concat_list.txt", "-c", "copy", narration_path.name],
        cwd=out_dir,
    )

    line_times: list[LineTime] = []
    cumulative = 0.0
    for a in audios:
        line_times.append(LineTime(start=cumulative, end=cumulative + a.duration))
        cumulative += a.duration
    return narration_path, line_times


def render_piece_video(
    piece_html: Path,
    line_times: list[LineTime],
    narration_path: Path,
    out_path: Path,
    resolution: tuple[int, int] = (1080, 1920),
    fps: int = FPS,
    music_path: Optional[Path] = None,
) -> Path:
    """Drives `piece_html?render=1` deterministically via window.__seek(t),
    screenshotting each frame, then muxes the PNG sequence with the
    narration audio, then layers `music_path` under that at
    MUSIC_VOLUME_DB via pipeline.ffmpeg_utils.mix_final (same mixing step
    every story track's video goes through — see pipeline.assemble.
    pick_music_track). No captions_ass_path: a piece's on-screen captions
    are drawn straight onto the canvas (see pipeline/canvas_lib/helpers.js's
    createCaptionSync), so they're already baked into every screenshotted
    frame — nothing to burn in separately here. `line_times` (see
    synthesize_narration_lines) is injected as window.__LINE_TIMES__ before
    the piece's own script runs, so its internal timeline is built from
    real speech durations."""
    total_duration = line_times[-1].end + TAIL_BUFFER_SECONDS
    total_frames = int(total_duration * fps)

    frames_dir = out_path.parent / f"_{out_path.stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for p in frames_dir.glob("*.png"):
        p.unlink()

    line_times_json = json.dumps([{"start": lt.start, "end": lt.end} for lt in line_times])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": resolution[0], "height": resolution[1]})
        page.add_init_script(f"window.__LINE_TIMES__ = {line_times_json};")
        page.goto(f"file://{piece_html.resolve()}?render=1")
        page.wait_for_timeout(200)  # let fonts/layout settle before the first capture

        for i in range(total_frames):
            t = i / fps
            page.evaluate(f"window.__seek({t})")
            page.screenshot(path=str(frames_dir / f"frame_{i:05d}.png"))

        browser.close()

    narrated_path = out_path.parent / f"_{out_path.stem}_narrated.mp4"
    run_ffmpeg([
        "-framerate", str(fps), "-i", "frame_%05d.png",
        "-i", str(narration_path.resolve()),
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(narrated_path.resolve()),
    ], cwd=frames_dir)
    shutil.rmtree(frames_dir)

    mix_final(
        narrated_path, out_path,
        music_path=music_path, captions_ass_path=None, music_volume_db=MUSIC_VOLUME_DB,
    )
    narrated_path.unlink()

    return out_path
