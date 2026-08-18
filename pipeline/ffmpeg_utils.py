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
    # flags=lanczos: this scale is a geometric no-op for the common case
    # (input already at exactly `resolution`, from pipeline.textcard's
    # zoompan output), but the default scaler (bilinear) still re-resamples
    # every pixel on a no-op scale -- the same shake-causing mechanism
    # already diagnosed and fixed in _zoompan_clip, just never applied to
    # this second scale pass. Matters for real (non-pre-sized) b-roll input
    # too, where the resize is a genuine resize, not just a no-op.
    scale_crop = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},fps={FPS},setsar=1[v]"
    )
    run_ffmpeg([
        "-stream_loop", "-1", "-i", str(broll_path),
        "-i", str(audio_path),
        "-filter_complex", f"[0:v]{scale_crop}",
        "-map", "[v]", "-map", "1:a",
        "-t", f"{duration:.3f}",
        # Matches pipeline.textcard._zoompan_clip's quality bar (slow/crf16,
        # not veryfast/crf20) -- this re-encode runs on every scene clip, so
        # using the old low-quality settings here was quietly re-compressing
        # (and visibly degrading) the zoompan stage's fixed output right
        # back down before it ever reached concat/final mux.
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
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


ACCENT_RGB = (255, 195, 80)  # warm gold, matches pipeline/thumbnail.py's ACCENT_COLOR
EMPHASIS_KEYWORDS = {"ai", "chatgpt", "gpt", "pdf", "cta", "youtube"}


def _rgb_to_ass_style_color(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"  # style fields: &HAABBGGRR


def _rgb_to_ass_inline_color(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"&H{b:02X}{g:02X}{r:02X}&"  # inline \c override: &HBBGGRR&
ACCENT_INLINE_ASS = _rgb_to_ass_inline_color(ACCENT_RGB)
WHITE_INLINE_ASS = "&HFFFFFF&"


def _is_emphasis_word(word: str) -> bool:
    bare = word.strip("\"'.,!?:;()")
    if not bare:
        return False
    if any(ch.isdigit() for ch in bare):
        return True
    if bare.isupper() and len(bare) >= 2:
        return True
    return bare.lower() in EMPHASIS_KEYWORDS


def group_words_into_captions(
    word_tuples: list[tuple[str, float, float, bool]],
    max_words: int = 2,
) -> list[tuple[str, float, float, bool]]:
    """Groups (word, start, end, ends_sentence) tuples into short caption
    chunks (default 1-2 words, for a big word-by-word "pop" caption style),
    breaking early at a sentence end so a chunk never straddles two sentences.

    Returns (text, start, end, emphasize) — emphasize is True if any word in
    the chunk looks like a number, acronym, or tool-name keyword, so the
    caller can render it in the accent color.

    ends_sentence must come from the original narration text, not the word
    itself — edge-tts's WordBoundary events strip punctuation entirely, so
    checking e.g. word.endswith(".") here would silently never fire.
    """
    chunks = []
    current: list[tuple[str, float, float, bool]] = []
    for word, start, end, ends_sentence in word_tuples:
        current.append((word, start, end, ends_sentence))
        if len(current) >= max_words or ends_sentence:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)

    return [
        (
            " ".join(w for w, _, _, _ in chunk),
            chunk[0][1],
            chunk[-1][2],
            any(_is_emphasis_word(w) for w, _, _, _ in chunk),
        )
        for chunk in chunks
    ]


def _pop_in_tags() -> str:
    """A quick scale-bounce + fade-in, applied at the start of each caption's
    on-screen window — the classic word-by-word 'pop' of short-form captions."""
    return r"\fad(60,0)\fscx55\fscy55\t(0,150,\fscx108\fscy108)\t(150,220,\fscx100\fscy100)"


def build_ass_captions(
    caption_lines: list[tuple[str, float, float, bool]],
    out_path: Path,
    resolution: tuple[int, int],
    font_size: int = 100,
    font_name: str = "Arial",
) -> Path:
    """caption_lines are big, lower-third, word-by-word "pop" captions —
    1-2 words at a time with a scale-bounce entrance, white by default and
    accent-colored when flagged as emphasis (numbers/acronyms/tool names).

    font_name defaults to Arial (Latin script); a non-Latin-script track
    (e.g. Hindi/Devanagari) needs a font that actually has those glyphs —
    e.g. "Kohinoor Devanagari" on macOS, "Noto Sans Devanagari" on Linux/CI
    (install via the `fonts-noto` apt package) — otherwise libass silently
    renders missing-glyph boxes.
    """
    width, height = resolution
    # Bottom-center (Alignment=2), lifted clear of the frame edge and of
    # platform UI that overlays the lower portion of vertical video
    # (Shorts/Reels caption+like/share icons) — captions used to sit dead
    # center (Alignment=5) and covered the middle of every illustration.
    caption_margin_v = int(height * 0.14)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{font_size},&H00FFFFFF,&H00000000,&H00000000,1,0,1,5,0,2,60,60,{caption_margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""
    lines = [header]

    def _add(text: str, start: float, end: float, style: str, override: str = "") -> None:
        if end <= start:
            return
        escaped = text.replace("\\", "").replace("{", "").replace("}", "")
        prefix = f"{{{override}}}" if override else ""
        lines.append(
            f"Dialogue: 0,{seconds_to_ass_timestamp(start)},{seconds_to_ass_timestamp(end)},{style},{prefix}{escaped}\n"
        )

    pop_tags = _pop_in_tags()
    for text, start, end, emphasize in caption_lines:
        color = ACCENT_INLINE_ASS if emphasize else WHITE_INLINE_ASS
        _add(text, start, end, "Caption", override=f"{pop_tags}\\c{color}")

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
        # See build_scene_clip's comment -- this is the final output encode,
        # so re-compressing at veryfast/crf20 here undid the zoompan fix's
        # quality all over again on every single delivered video.
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path.resolve()),
    ]
    # Run with cwd = captions dir so the subtitles filter can reference it by
    # bare filename, sidestepping ffmpeg filter-string escaping of the full path.
    cwd = captions_ass_path.parent if captions_ass_path is not None else None
    run_ffmpeg(args, cwd=cwd)
    return out_path
