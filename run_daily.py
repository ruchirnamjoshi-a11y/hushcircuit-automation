#!/usr/bin/env python3
"""Daily orchestrator: for each story track (kids/teens/adults/women/
hindi_mythology/hero_saga), consumes that track's next queued script and
produces + uploads its video(s). Tracks with Track.produce_long_form=True
(currently kids + hindi_mythology) get TWO uploads per day — the full
long-form story and a trimmed Shorts highlight cut — built from the SAME
generated scene images/audio, at no extra image-generation or TTS cost.

A track with Track.shares_images_with set (hindi_mythology -> "kids") is a
language variant of another track's stories: instead of generating its own
scene illustrations, it reuses the primary track's already-generated raw
images from THIS SAME run (matched via Script.source_script_id), so a
second language costs zero extra Cloudflare image-generation quota — only
its own narration/captions/upload are per-language work. If the primary
track hasn't produced its paired script yet this run (e.g. it already
produced today's video in an earlier scheduled run, or its queue is out of
sync), the variant track falls back to generating its own images
independently rather than stalling.

On success per track, moves the consumed script from
scripts_queue/pending/<track>/ to scripts_queue/used/<track>/.

This is meant to run several times a day (see .github/workflows/
daily-video.yml), not just once:

- Before doing any other real work for a track that generates its own
  images (i.e. not reusing a sibling's this run), a dedicated reference
  portrait is generated as a real-work "probe" (see
  ai_image.REFERENCE_PORTRAIT_SCENE). If Cloudflare's free image quota is
  exhausted, that raises CloudflareQuotaExhausted instead of silently
  falling back to the gradient — we'd rather abort and retry later than
  publish a video with no matching artwork. The whole run stops there
  (quota is account-wide, not per-track, so every other track would hit the
  same wall) and every track's script stays in pending/ for the next
  scheduled run to retry.
- pipeline/state.py tracks which tracks already produced a video today, so
  a later same-day run — after quota resets — doesn't produce a *second*
  video for a track that already succeeded earlier.

An empty queue for a given track is not an error — that track is skipped and
the others still run. If a stage raises for a track for any other reason,
that script is left in pending/ so the next run retries it, that track's
error is logged, and the other tracks still get a chance to run; the process
exits non-zero at the end if any track had a real failure (GitHub Actions
surfaces that as a failed run) so a silent per-track failure doesn't go
unnoticed. Quota-exhaustion aborts are expected/handled, not failures, and
don't affect the exit code.
"""

from __future__ import annotations

import argparse
import sys
import traceback
import zlib
from pathlib import Path
from typing import Optional

from pipeline.ai_image import (
    REFERENCE_PORTRAIT_SCENE,
    CloudflareQuotaExhausted,
    fit_scene_image,
    generate_scene_image_raw,
)
from pipeline.assemble import pick_music_track
from pipeline.canvas_video import render_piece_video, synthesize_narration_lines
from pipeline.config import (
    MATH_PIECES_DIR,
    OUTPUT_DIR,
    QUEUE_PENDING_DIR,
    QUEUE_USED_DIR,
    SHORT_RESOLUTION,
    TRACKS,
    Track,
    youtube_token_path,
)
from pipeline.math_scripts import load_next_pending as load_next_pending_math
from pipeline.scripts import Script, load_next_pending, mark_used
from pipeline.shorts import assemble_short
from pipeline.state import already_produced_today, mark_produced_today
from pipeline.thumbnail import generate_thumbnail
from pipeline.tts import synthesize_script_for_track
from pipeline.upload import upload_daily_video

# Populated by a track's own (non-reused) image generation, consulted by any
# later-processed track whose Track.shares_images_with points at it —
# key: (track_key, script_id) -> (images_raw_dir, scene_used_fallback).
# Scoped to a single run() call: an ephemeral CI runner's output/ dir
# doesn't persist between separately scheduled runs, so reuse only works
# when both the primary and variant scripts are due in the same run.
ProducedImages = dict[tuple[str, str], tuple[Path, list[bool]]]


def _story_seed(seed_key: str) -> int:
    """A stable (not Python's randomized-per-process hash()) seed derived
    from seed_key, reused for every scene's Cloudflare call within a story.
    Combined with a fixed reference-portrait image (see
    ai_image.REFERENCE_PORTRAIT_SCENE), this keeps a character visually
    recognizable across independently generated scenes."""
    return zlib.crc32(seed_key.encode())


def _generate_story_images(
    script: Script, track: Track, seed: int, images_raw_dir: Path,
) -> tuple[list[Path], list[bool]]:
    """Generates a fixed reference portrait, then every scene's illustration
    conditioned on it. The portrait generation is the real-work quota probe
    (raises CloudflareQuotaExhausted if the daily free allocation is
    exhausted) — done before any other work so nothing's wasted if it
    fails."""
    portrait_path, portrait_fallback = generate_scene_image_raw(
        REFERENCE_PORTRAIT_SCENE, images_raw_dir / "reference_portrait.png", track,
        character_reference=script.character_reference, seed=seed, raise_on_quota_exhausted=True,
    )
    reference_image_bytes = None if portrait_fallback else portrait_path.read_bytes()

    raw_results = [
        generate_scene_image_raw(
            scene, images_raw_dir / f"scene_{i:02d}.png", track,
            character_reference=script.character_reference, seed=seed,
            reference_image_bytes=reference_image_bytes,
        )
        for i, scene in enumerate(script.scenes)
    ]
    raw_images = [path for path, _ in raw_results]
    scene_used_fallback = [used_fallback for _, used_fallback in raw_results]
    return raw_images, scene_used_fallback


def _reused_story_images(
    script: Script, track: Track, produced_images: ProducedImages,
) -> Optional[tuple[list[Path], list[bool]]]:
    """Looks up a sibling track's already-generated images for this script's
    source_script_id, produced earlier in this SAME run() call. Returns None
    if unavailable — the caller falls back to independent generation."""
    entry = produced_images.get((track.shares_images_with, script.source_script_id or script.id))
    if entry is None:
        return None
    source_images_dir, scene_used_fallback = entry
    if len(scene_used_fallback) != len(script.scenes):
        # Paired scripts must have matching scene counts (visuals must line
        # up 1:1) — a mismatch means these aren't really a matched pair.
        return None
    raw_images = [source_images_dir / f"scene_{i:02d}.png" for i in range(len(script.scenes))]
    return raw_images, scene_used_fallback


def run_track(
    track: Track,
    dry_run: bool = False,
    privacy_status: str = "private",
    produced_images: Optional[ProducedImages] = None,
) -> bool:
    """Returns True if a video was produced (or there was legitimately
    nothing to do — empty queue, already produced today). Raises
    CloudflareQuotaExhausted if the image quota probe fails (caller decides
    whether to skip remaining tracks), or any other exception on a real
    failure. `produced_images` is shared across every track within one
    run() call — see ProducedImages."""
    if produced_images is None:
        produced_images = {}

    if not dry_run and already_produced_today(track.key, limit=track.videos_per_day):
        print(f"[{track.key}] Already produced a video today — skipping until tomorrow.")
        return True

    if not dry_run and not youtube_token_path(track.key).exists():
        # Each track has its own YouTube channel/OAuth token (see README's
        # multi-channel setup). A channel that hasn't been set up yet is a
        # clean skip, not a failure — and we check this BEFORE doing any
        # TTS/image work, since it'd all be wasted on a track that can't
        # upload anyway.
        print(f"[{track.key}] No YouTube OAuth token yet for this channel — skipping until it's set up.")
        return True

    pending_dir = QUEUE_PENDING_DIR / track.key
    used_dir = QUEUE_USED_DIR / track.key

    result = load_next_pending(pending_dir)
    if result is None:
        print(f"[{track.key}] Queue empty — refill needed. Skipping today.")
        return True

    script_path, script = result
    print(f"[{track.key}] Producing: {script.title} ({script.id})")
    run_dir = OUTPUT_DIR / track.key / script.id
    run_dir.mkdir(parents=True, exist_ok=True)
    images_raw_dir = run_dir / "images_raw"

    # Serialized tracks (see Track.serialized) reuse ONE seed across the
    # whole series, not just within one episode, so the protagonist stays
    # visually consistent from episode 1 through episode N.
    seed = _story_seed(track.key if track.serialized else script.id)

    reused = _reused_story_images(script, track, produced_images) if track.shares_images_with else None
    if reused is not None:
        print(f"[{track.key}] [1/4] Reusing images generated for "
              f"'{script.source_script_id or script.id}' earlier this run — no image-generation cost.")
        raw_images, scene_used_fallback = reused
    else:
        if track.shares_images_with:
            print(f"[{track.key}] [1/4] No matching images from '{track.shares_images_with}' "
                  f"this run yet — generating independently instead.")
        else:
            print(f"[{track.key}] [1/4] Probing image generation availability...")
        raw_images, scene_used_fallback = _generate_story_images(script, track, seed, images_raw_dir)
        produced_images[(track.key, script.id)] = (images_raw_dir, scene_used_fallback)

    voice_desc = f"{track.tts_provider}: {'/'.join(track.tts_voices) or track.voice}"
    print(f"[{track.key}] [2/4] Synthesizing voiceover ({voice_desc})...")
    scene_audios = synthesize_script_for_track(script, run_dir / "audio", track)

    print(f"[{track.key}] [3/4] Fitting images for output...")
    fit_images = [
        fit_scene_image(raw, run_dir / "images_fit" / f"scene_{i:02d}.png", SHORT_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]

    music_path = pick_music_track()
    thumbnail_path = generate_thumbnail(
        script.title, run_dir / "thumbnail.jpg", draw_text=track.burn_captions,
    )

    print(f"[{track.key}] [4/4] Assembling and uploading Short (dry_run={dry_run})...")
    short_path = assemble_short(
        script, scene_audios, fit_images,
        work_dir=run_dir / "work_short", out_path=run_dir / "video_short.mp4",
        music_path=music_path, scene_used_fallback=scene_used_fallback,
        burn_captions=track.burn_captions,
    )
    short_upload = upload_daily_video(
        script, short_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run, is_short=True,
    )
    print(short_upload)

    if track.produce_long_form:
        print(f"[{track.key}] Assembling and uploading long-form (dry_run={dry_run})...")
        long_path = assemble_short(
            script, scene_audios, fit_images,
            work_dir=run_dir / "work_long", out_path=run_dir / "video_long.mp4",
            music_path=music_path, max_seconds=None, scene_used_fallback=scene_used_fallback,
            burn_captions=track.burn_captions,
        )
        long_upload = upload_daily_video(
            script, long_path, thumbnail_path, track,
            privacy_status=privacy_status, dry_run=dry_run, is_short=False,
        )
        print(long_upload)

    mark_used(script_path, used_dir)
    if not dry_run:
        mark_produced_today(track.key)
    print(f"[{track.key}] Done. Moved {script_path.name} to scripts_queue/used/{track.key}/.")
    return True


def run_math_track(track: Track, dry_run: bool = False, privacy_status: str = "private") -> bool:
    """Produces + uploads one math_explainers video: no AI images, no
    Cloudflare quota involved at all — synthesizes each narration line
    (pipeline.tts), renders the paired hand-authored canvas piece against
    those real durations (pipeline.canvas_video), then uploads as a single
    Short-style video. Same queue-consumption/state contract as run_track."""
    if not dry_run and already_produced_today(track.key, limit=track.videos_per_day):
        print(f"[{track.key}] Already produced a video today — skipping until tomorrow.")
        return True

    if not dry_run and not youtube_token_path(track.key).exists():
        print(f"[{track.key}] No YouTube OAuth token yet for this channel — skipping until it's set up.")
        return True

    pending_dir = QUEUE_PENDING_DIR / track.key
    used_dir = QUEUE_USED_DIR / track.key

    result = load_next_pending_math(pending_dir)
    if result is None:
        print(f"[{track.key}] Queue empty — refill needed. Skipping today.")
        return True

    script_path, script = result
    print(f"[{track.key}] Producing: {script.title} ({script.id})")
    run_dir = OUTPUT_DIR / track.key / script.id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{track.key}] [1/3] Synthesizing narration lines (edge: {track.voice})...")
    narration_path, line_times = synthesize_narration_lines(script.narration_lines, run_dir / "audio", track.voice)

    print(f"[{track.key}] [2/3] Rendering '{script.piece}' (Playwright + ffmpeg)...")
    piece_html = MATH_PIECES_DIR / script.piece / "piece.html"
    video_path = render_piece_video(
        piece_html, line_times, narration_path, run_dir / "video.mp4",
        music_path=pick_music_track(),
    )

    thumbnail_path = generate_thumbnail(script.title, run_dir / "thumbnail.jpg")

    print(f"[{track.key}] [3/3] Uploading (dry_run={dry_run})...")
    upload_result = upload_daily_video(
        script, video_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run, is_short=True,
    )
    print(upload_result)

    mark_used(script_path, used_dir)
    if not dry_run:
        mark_produced_today(track.key)
    print(f"[{track.key}] Done. Moved {script_path.name} to scripts_queue/used/{track.key}/.")
    return True


def run(dry_run: bool = False, privacy_status: str = "private", track_key: Optional[str] = None) -> int:
    tracks = [TRACKS[track_key]] if track_key else list(TRACKS.values())

    any_failed = False
    quota_exhausted = False
    produced_images: ProducedImages = {}
    for track in tracks:
        if quota_exhausted:
            print(f"[{track.key}] Skipping — Cloudflare image quota already confirmed exhausted "
                  f"this run. Will retry on the next scheduled run.")
            continue
        try:
            if track.content_type == "canvas":
                run_math_track(track, dry_run=dry_run, privacy_status=privacy_status)
            else:
                run_track(track, dry_run=dry_run, privacy_status=privacy_status, produced_images=produced_images)
        except CloudflareQuotaExhausted as e:
            # Account-wide, not per-track — every other track would hit the
            # same wall, so stop here rather than burning time confirming
            # that 3 more times. Not a failure: this is the pipeline
            # working as designed, and the next scheduled run retries it.
            quota_exhausted = True
            print(f"[{track.key}] Cloudflare image quota exhausted: {e}")
            print(f"[{track.key}] Aborting this track and skipping remaining tracks — "
                  f"script stays queued for the next scheduled run.")
        except Exception:
            any_failed = True
            print(f"[{track.key}] FAILED:")
            traceback.print_exc()

    return 1 if any_failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip the real YouTube upload")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--track", default=None, choices=list(TRACKS.keys()), help="Run only this track (default: all tracks)")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, privacy_status=args.privacy, track_key=args.track))


if __name__ == "__main__":
    main()
