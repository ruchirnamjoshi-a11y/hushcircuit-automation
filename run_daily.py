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

An empty queue for a given track is not an error — that track is skipped and
the others still run. If a stage raises for a track, that script is left in
pending/ so the next run retries it, that track's error is logged, and the
other tracks still get a chance to run; the process exits non-zero at the end
if any track failed (GitHub Actions surfaces that as a failed run) so a
silent per-track failure doesn't go unnoticed.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from pipeline.ai_image import fit_scene_image, generate_scene_image_raw
from pipeline.assemble import pick_music_track
from pipeline.config import (
    OUTPUT_DIR,
    QUEUE_PENDING_DIR,
    QUEUE_USED_DIR,
    SHORT_RESOLUTION,
    TRACKS,
    Track,
)
from pipeline.scripts import load_next_pending, mark_used
from pipeline.shorts import assemble_short
from pipeline.thumbnail import generate_thumbnail
from pipeline.tts import synthesize_script
from pipeline.upload import upload_daily_video


def run_track(track: Track, dry_run: bool = False, privacy_status: str = "private") -> bool:
    """Returns True if a video was produced (or the queue was legitimately
    empty), False if the track hit a real error."""
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

    print(f"[{track.key}] [1/4] Synthesizing voiceover ({track.voice})...")
    scene_audios = synthesize_script(script, run_dir / "audio", voice=track.voice)

    print(f"[{track.key}] [2/4] Generating scene illustrations...")
    raw_results = [
        generate_scene_image_raw(scene, run_dir / "images_raw" / f"scene_{i:02d}.png", track)
        for i, scene in enumerate(script.scenes)
    ]
    raw_images = [path for path, _ in raw_results]
    scene_used_fallback = [used_fallback for _, used_fallback in raw_results]
    short_images = [
        fit_scene_image(raw, run_dir / "images_short" / f"scene_{i:02d}.png", SHORT_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]

    music_path = pick_music_track()

    print(f"[{track.key}] [3/4] Assembling video...")
    video_path = assemble_short(
        script, scene_audios, short_images,
        work_dir=run_dir / "work", out_path=run_dir / "video.mp4",
        music_path=music_path, scene_used_fallback=scene_used_fallback,
    )

    print(f"[{track.key}] [4/4] Generating thumbnail...")
    thumbnail_path = generate_thumbnail(script.title, run_dir / "thumbnail.jpg")

    print(f"[{track.key}] Uploading (dry_run={dry_run})...")
    upload_result = upload_daily_video(
        script, video_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run,
    )
    print(upload_result)

    mark_used(script_path, used_dir)
    print(f"[{track.key}] Done. Moved {script_path.name} to scripts_queue/used/{track.key}/.")
    return True


def run(dry_run: bool = False, privacy_status: str = "private", track_key: str | None = None) -> int:
    tracks = [TRACKS[track_key]] if track_key else list(TRACKS.values())

    any_failed = False
    for track in tracks:
        try:
            run_track(track, dry_run=dry_run, privacy_status=privacy_status)
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
