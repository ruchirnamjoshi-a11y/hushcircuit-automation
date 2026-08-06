"""Shared low-level ffmpeg building blocks used by assemble.py and shorts.py."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

FPS = 30
AUDIO_RATE = 44100


def run_ffmpeg(args: list[str], cwd: Optional[Path] = None) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n{result.stderr}")


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def build_scene_clip(
    broll_path: Path,
    audio_path: Path,
    out_path: Path,
    resolution: tuple[int, int],
) -> Path:
    """Scale/crop broll to fill `resolution`, loop/trim it to the voiceover's
    length, and mux the voiceover in as the audio track.

    Uses an explicit `-t <duration>` hard trim rather than `-shortest`:
    `-shortest` does not reliably terminate at the audio's end when the video
    input is infinitely looped through a filter graph (observed ~1s overshoot).
    """
    width, height = resolution
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(audio_path)
    scale_crop = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={FPS},setsar=1[v]"
    )
    run_ffmpeg([
        "-stream_loop", "-1", "-i", str(broll_path),
        "-i", str(audio_path),
        "-filter_complex", f"[0:v]{scale_crop}",
        "-map", "[v]", "-map", "1:a",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE), "-ac", "2",
        str(out_path),
    ])
    return out_path


def concat_clips(clip_paths: list[Path], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / f"{out_path.stem}_concat_list.txt"
    list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy",
        str(out_path),
    ])
    return out_path


def seconds_to_ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def group_words_into_captions(
    word_tuples: list[tuple[str, float, float]],
    max_words: int = 4,
) -> list[tuple[str, float, float]]:
    """Groups (word, start, end) tuples into short caption lines, breaking
    early at sentence-ending punctuation so a line never straddles two
    sentences even if that means fewer than max_words in it."""
    chunks = []
    current: list[tuple[str, float, float]] = []
    for word, start, end in word_tuples:
        current.append((word, start, end))
        ends_sentence = word.rstrip("\"')").endswith((".", "!", "?"))
        if len(current) >= max_words or ends_sentence:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)

    return [
        (" ".join(w for w, _, _ in chunk), chunk[0][1], chunk[-1][2])
        for chunk in chunks
    ]


def build_ass_captions(
    caption_lines: list[tuple[str, float, float]],
    out_path: Path,
    resolution: tuple[int, int],
    font_size: int = 64,
    margin_v: int = 100,
) -> Path:
    width, height = resolution
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H00000000,&H00000000,1,0,1,4,0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""
    lines = [header]
    for text, start, end in caption_lines:
        if end <= start:
            continue
        escaped = text.replace("\\", "").replace("{", "").replace("}", "")
        lines.append(
            f"Dialogue: 0,{seconds_to_ass_timestamp(start)},{seconds_to_ass_timestamp(end)},Default,{escaped}\n"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines))
    return out_path


def mix_final(
    video_path: Path,
    out_path: Path,
    music_path: Optional[Path] = None,
    captions_ass_path: Optional[Path] = None,
    music_volume_db: int = -22,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = ["-i", str(video_path)]
    filters = []
    video_out = "0:v"
    audio_out = "0:a"

    if captions_ass_path is not None:
        filters.append(f"[0:v]subtitles={captions_ass_path.name}[v]")
        video_out = "[v]"

    if music_path is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        filters.append(f"[1:a]volume={music_volume_db}dB[music]")
        filters.append(f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        audio_out = "[aout]"

    args = [*inputs]
    if filters:
        args += ["-filter_complex", ";".join(filters)]
    args += [
        "-map", video_out, "-map", audio_out,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path.resolve()),
    ]
    # Run with cwd = captions dir so the subtitles filter can reference it by
    # bare filename, sidestepping ffmpeg filter-string escaping of the full path.
    cwd = captions_ass_path.parent if captions_ass_path is not None else None
    run_ffmpeg(args, cwd=cwd)
    return out_path
