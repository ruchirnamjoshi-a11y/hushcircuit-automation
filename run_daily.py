#!/usr/bin/env python3
"""Daily orchestrator: for each story track (kids/teens/adults/women),
consumes that track's next queued script and produces + uploads a single
vertical Short carrying the full story. On success per track, moves the
consumed script from scripts_queue/pending/<track>/ to
scripts_queue/used/<track>/.

Shorts-only by design: our ~8-scene stories run ~70-90s, so a separate
long-form (16:9) video added little, and uploading both would cost ~13,000
YouTube Data API quota units/day across 4 tracks — over the default 10,000/day
free cap. One upload per track keeps it to ~6,600/day.

This is meant to run several times a day (see .github/workflows/
daily-video.yml), not just once:

- Before doing any real work for a track, its first scene's image is
  generated as a real-work "probe". If Cloudflare's free image quota is
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

from pipeline.ai_image import CloudflareQuotaExhausted, fit_scene_image, generate_scene_image_raw
from pipeline.assemble import pick_music_track
from pipeline.config import (
    OUTPUT_DIR,
    QUEUE_PENDING_DIR,
    QUEUE_USED_DIR,
    SHORT_RESOLUTION,
    TRACKS,
    Track,
    youtube_token_path,
)
from pipeline.scripts import load_next_pending, mark_used
from pipeline.shorts import assemble_short
from pipeline.state import already_produced_today, mark_produced_today
from pipeline.thumbnail import generate_thumbnail
from pipeline.tts import synthesize_script_for_track
from pipeline.upload import upload_daily_video


def _story_seed(seed_key: str) -> int:
    """A stable (not Python's randomized-per-process hash()) seed derived
    from seed_key, reused for every scene's Cloudflare call within a story.
    Cloudflare's free flux-1-schnell has no image-to-image/reference input,
    so a shared seed + Script.character_reference (see
    pipeline.ai_image.build_prompt) is the strongest available lever for
    keeping a character visually recognizable across independently
    generated scenes."""
    return zlib.crc32(seed_key.encode())


def run_track(track: Track, dry_run: bool = False, privacy_status: str = "private") -> bool:
    """Returns True if a video was produced (or there was legitimately
    nothing to do — empty queue, already produced today). Raises
    CloudflareQuotaExhausted if the image quota probe fails (caller decides
    whether to skip remaining tracks), or any other exception on a real
    failure."""
    if not dry_run and already_produced_today(track.key):
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

    # Serialized tracks (see Track.serialized) reuse ONE seed across the
    # whole series, not just within one episode, so the protagonist stays
    # visually consistent from episode 1 through episode N.
    seed = _story_seed(track.key if track.serialized else script.id)

    print(f"[{track.key}] [1/4] Probing image generation availability...")
    images_raw_dir = run_dir / "images_raw"
    first_raw_path, first_used_fallback = generate_scene_image_raw(
        script.scenes[0], images_raw_dir / "scene_00.png", track,
        character_reference=script.character_reference, seed=seed, raise_on_quota_exhausted=True,
    )
    # The first scene's own generated image becomes the reference for every
    # later scene — real image-to-image conditioning (see ai_image.py),
    # not just a shared text description. Skipped if scene 1 itself fell
    # back to the gradient (nothing meaningful to reference).
    reference_image_bytes = None if first_used_fallback else first_raw_path.read_bytes()

    voice_desc = f"{track.tts_provider}: {'/'.join(track.tts_voices) or track.voice}"
    print(f"[{track.key}] [2/4] Synthesizing voiceover ({voice_desc})...")
    scene_audios = synthesize_script_for_track(script, run_dir / "audio", track)

    print(f"[{track.key}] [3/4] Generating remaining scene illustrations...")
    raw_results = [(first_raw_path, first_used_fallback)] + [
        generate_scene_image_raw(
            scene, images_raw_dir / f"scene_{i:02d}.png", track,
            character_reference=script.character_reference, seed=seed,
            reference_image_bytes=reference_image_bytes,
        )
        for i, scene in enumerate(script.scenes[1:], start=1)
    ]
    raw_images = [path for path, _ in raw_results]
    scene_used_fallback = [used_fallback for _, used_fallback in raw_results]
    short_images = [
        fit_scene_image(raw, run_dir / "images_short" / f"scene_{i:02d}.png", SHORT_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]

    music_path = pick_music_track()

    print(f"[{track.key}] [4/4] Assembling video...")
    video_path = assemble_short(
        script, scene_audios, short_images,
        work_dir=run_dir / "work", out_path=run_dir / "video.mp4",
        music_path=music_path, scene_used_fallback=scene_used_fallback,
        burn_captions=track.burn_captions,
    )

    thumbnail_path = generate_thumbnail(
        script.title, run_dir / "thumbnail.jpg", draw_text=track.burn_captions,
    )

    print(f"[{track.key}] Uploading (dry_run={dry_run})...")
    upload_result = upload_daily_video(
        script, video_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run,
    )
    print(upload_result)

    mark_used(script_path, used_dir)
    if not dry_run:
        mark_produced_today(track.key)
    print(f"[{track.key}] Done. Moved {script_path.name} to scripts_queue/used/{track.key}/.")
    return True


def run(dry_run: bool = False, privacy_status: str = "private", track_key: str | None = None) -> int:
    tracks = [TRACKS[track_key]] if track_key else list(TRACKS.values())

    any_failed = False
    quota_exhausted = False
    for track in tracks:
        if quota_exhausted:
            print(f"[{track.key}] Skipping — Cloudflare image quota already confirmed exhausted "
                  f"this run. Will retry on the next scheduled run.")
            continue
        try:
            run_track(track, dry_run=dry_run, privacy_status=privacy_status)
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
