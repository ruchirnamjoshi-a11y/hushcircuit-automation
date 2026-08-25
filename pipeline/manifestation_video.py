"""Audio + video generation for the manifestation/affirmation song track.
Lyrics and per-line scene descriptions come from pipeline.manifestation_lyrics
(Gemini); this module turns those into a finished video:

1. synthesize_song — ACE-Step (HF Space, ZeroGPU) turns the lyrics into a
   real sung track.
2. transcribe_song — faster-whisper (local, free) gets REAL per-word timing
   from the actual audio; ACE-Step gives no alignment info itself.
3. match_words_to_lines — fuzzy-matches the transcription back onto the
   KNOWN authored lyric lines (word overlap, not exact string match — ASR
   output doesn't reliably match the original punctuation/spelling, which
   silently broke an earlier exact-match version of this).
4. generate_character_reference / generate_scene_image — Cloudflare
   (free), one fixed reference portrait + every scene image conditioned on
   it (image-to-image) for real character consistency across the video.
5. build_line_clips / assemble_video — Ken Burns motion per line held for
   its real sung duration (pipeline.textcard), concatenated, muxed with the
   song audio, karaoke captions burned on top
   (pipeline.ffmpeg_utils.build_ass_karaoke_captions).

Built and hand-verified against one specific song earlier in the same
session as a proof of concept (13 hand-picked "buckets", a hand-typed
line->image dict) — this module replaces both of those hand-authored
pieces with Gemini (step 3 of pipeline.manifestation_lyrics) and fuzzy
matching (step 3 here) so it generalizes to any new song.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from pipeline.ai_image import CloudflareQuotaExhausted, _call_cloudflare
from pipeline.config import GEMINI_API_KEY, HF_TOKEN
from pipeline.ffmpeg_utils import build_ass_karaoke_captions, concat_clips, probe_duration, run_ffmpeg
from pipeline.textcard import generate_image_background_clip

RESOLUTION = (1280, 720)
SONG_SEED = 4242  # fixed per-run seed for the reference portrait + every
                   # scene image -- same lever pipeline.ai_image uses for
                   # cross-scene consistency on the story tracks.
DEFAULT_CHARACTER = (
    "the same young woman throughout, early 20s, wavy shoulder-length "
    "brown hair, warm brown eyes, athletic build, wearing a simple fitted "
    "olive-green long-sleeve top and dark leggings"
)
STYLE_PREFIX = "Cinematic photo-realistic photo, no text, no letters, no words anywhere. "
STYLE_SUFFIX = (
    ", front-facing or three-quarter view with her face clearly lit, "
    "sharply in focus, and fully visible -- natural skin tones, detailed "
    "facial features, absolutely NOT a silhouette, NOT backlit into "
    "darkness. Rich, detailed, realistic background environment. Warm "
    "cinematic lighting, wide 16:9 composition, high quality, photorealistic"
)


class ZeroGPUQuotaExhausted(RuntimeError):
    """Hugging Face's free ZeroGPU daily allocation is used up for this
    account (ACE-Step's song generation runs on it) — confirmed via the
    real observed AppError text, not a guess. Distinct from
    CloudflareQuotaExhausted: different provider, different daily reset,
    and typically a much longer (~23h) wait since a single generation call
    can consume most/all of the tiny free daily budget by itself."""


def synthesize_song(lyrics_text: str, style_tags: str, duration_seconds: float, out_path: Path) -> Path:
    from gradio_client import Client  # deferred: only needed for this one call

    client = Client("ACE-Step/ACE-Step", token=HF_TOKEN or None)
    try:
        result = client.predict(
            audio_duration=duration_seconds,
            prompt=style_tags,
            lyrics=lyrics_text,
            infer_step=60,
            api_name="/__call__",
        )
    except Exception as e:
        # Two distinct observed error texts share the same "retry later,
        # don't fail the whole job" handling in run_daily.py: hard daily
        # quota exhaustion ("ZeroGPU quota...") and live pool congestion
        # ("No GPU was available after 60s..."). Only matching the first
        # string let a real congestion error fall through as a raw
        # AppError and hard-fail the job (confirmed in production: run
        # 32814268463 failed the whole run on exactly this un-caught text).
        msg = str(e)
        if "ZeroGPU quota" in msg or "No GPU was available" in msg:
            raise ZeroGPUQuotaExhausted(msg) from e
        raise
    audio_path = result[0] if isinstance(result, (list, tuple)) else result
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(audio_path), "-c:a", "copy", str(out_path)])
    return out_path


def transcribe_song(audio_path: Path) -> list[tuple[str, float, float, bool]]:
    """Returns (word, start, end, ends_sentence) tuples — same shape
    pipeline.ffmpeg_utils's caption groupers already expect."""
    from faster_whisper import WhisperModel  # deferred: heavy import

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True, language="en")
    word_tuples = []
    for seg in segments:
        for w in seg.words:
            text = w.word.strip()
            if not text:
                continue
            ends_sentence = text.endswith((",", ".", "!", "?"))
            word_tuples.append((text, w.start, w.end, ends_sentence))
    return word_tuples


def _normalize_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def match_words_to_lines(
    caption_lines: list[tuple[str, float, float, bool]], known_lines: list[str],
) -> list[tuple[str, float, float]]:
    """For each ASR-transcribed caption chunk (text, start, end, emphasize),
    finds the closest KNOWN authored lyric line by word overlap and returns
    (known_line, start, end) — real timing from the transcription, but the
    ORIGINAL clean line text for scene lookup, not whatever ASR happened to
    hear. Falls back to the raw ASR text if nothing matches well enough
    (better than crashing on an unexpected line).

    Scored as "what fraction of the ASR fragment's words does this known
    line contain" (recall against the ASR words), not the reverse — an ASR
    chunk is very often a short PARTIAL capture of a line (e.g. just
    "unbreakable," out of "I'm unbreakable, unbreakable, yeah"), so scoring
    by how much of the *known line* the fragment covers systematically
    under-scored short fragments and sent ~36% of lines to the fallback
    path in testing. Ties (a short/generic word matching several known
    lines) go to the shortest candidate line, the tightest containing match.
    """
    known_word_sets = [(line, _normalize_words(line)) for line in known_lines]
    matched = []
    for text, start, end, _ in caption_lines:
        asr_words = _normalize_words(text)
        best_line, best_score, best_len = text, 0.0, None
        for line, line_words in known_word_sets:
            if not line_words or not asr_words:
                continue
            recall = len(asr_words & line_words) / len(asr_words)
            if recall > best_score or (recall == best_score and (best_len is None or len(line_words) < best_len)):
                best_score, best_line, best_len = recall, line, len(line_words)
        matched.append((best_line if best_score >= 0.5 else text, start, end))
    return matched


def generate_character_reference(character_description: str, out_path: Path) -> Path:
    prompt = (
        f"{STYLE_PREFIX}A confident portrait of {character_description}, "
        f"standing outdoors in soft natural daylight, calm self-assured "
        f"expression, looking directly at the camera{STYLE_SUFFIX}"
    )
    img = _call_cloudflare(prompt, seed=SONG_SEED, resolution=RESOLUTION)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def generate_scene_image(scene_description: str, reference_bytes: bytes, out_path: Path, seed: int = SONG_SEED) -> Path:
    prompt = f"{STYLE_PREFIX}{scene_description}{STYLE_SUFFIX}"
    img = _call_cloudflare(prompt, seed=seed, reference_image_bytes=reference_bytes, resolution=RESOLUTION)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def build_line_clips(
    matched_lines: list[tuple[str, float, float]],
    line_image_paths: dict[str, list[Path]],
    song_duration: float,
    clips_dir: Path,
) -> list[Path]:
    """One zoompan clip per matched line, held from that line's start to
    the NEXT line's start (not its own end) so inter-line pauses are
    covered too — an earlier version that used only (end - start) per line
    silently dropped ~45s of pause time across a 165s song. Cycles through
    that line's pre-generated variant images in occurrence order, so a
    repeated chorus doesn't show the identical photo every time."""
    clips_dir.mkdir(parents=True, exist_ok=True)
    variant_cursor: dict[str, int] = {}
    clip_paths = []

    for i, (line, start, _end) in enumerate(matched_lines):
        next_start = matched_lines[i + 1][1] if i + 1 < len(matched_lines) else song_duration
        duration = max(0.3, next_start - start)
        images = line_image_paths.get(line) or next(iter(line_image_paths.values()))
        idx = variant_cursor.get(line, 0) % len(images)
        variant_cursor[line] = idx + 1
        out_path = clips_dir / f"clip_{i:03d}.mp4"
        generate_image_background_clip(images[idx], RESOLUTION, duration, out_path)
        clip_paths.append(out_path)

    lead_in = matched_lines[0][1]
    if lead_in > 0.3:
        lead_path = clips_dir / "clip_lead.mp4"
        first_line_images = next(iter(line_image_paths.values()))
        generate_image_background_clip(first_line_images[0], RESOLUTION, lead_in, lead_path)
        clip_paths.insert(0, lead_path)

    return clip_paths


def assemble_video(
    clip_paths: list[Path],
    song_path: Path,
    matched_words: list[tuple[str, float, float, bool]],
    out_path: Path,
    work_dir: Path,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    silent_path = work_dir / "silent.mp4"
    concat_clips(clip_paths, silent_path)

    ass_path = build_ass_karaoke_captions(
        matched_words, work_dir / "captions.ass", resolution=RESOLUTION,
        font_size=48, font_name="Avenir Next Heavy",
    )

    with_audio = work_dir / "with_audio.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(silent_path), "-i", str(song_path),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(with_audio)],
        check=True,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(with_audio), "-vf", f"ass={ass_path.name}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "copy", str(out_path)],
        check=True, cwd=str(work_dir),
    )
    return out_path
