#!/usr/bin/env python3
"""Daily orchestrator: consumes the next queued script and produces + uploads
a long-form video and a Short. On success, moves the consumed script from
scripts_queue/pending/ to scripts_queue/used/.

If a stage raises, the script is left in pending/ so the next run retries it,
and this process exits non-zero (GitHub Actions surfaces that as a failed run).
"""

from __future__ import annotations

import argparse
import sys

from pipeline.ai_image import fit_scene_image, generate_scene_image_raw
from pipeline.assemble import assemble_long_form, pick_music_track
from pipeline.config import LONG_FORM_RESOLUTION, OUTPUT_DIR, SHORT_RESOLUTION
from pipeline.scripts import load_next_pending, mark_used
from pipeline.shorts import assemble_short
from pipeline.thumbnail import generate_thumbnail
from pipeline.tts import synthesize_script
from pipeline.upload import upload_daily_video


def run(dry_run: bool = False, privacy_status: str = "private") -> int:
    result = load_next_pending()
    if result is None:
        print("Queue empty — refill needed. Nothing to do today.")
        return 0

    script_path, script = result
    print(f"Producing: {script.title} ({script.id})")
    run_dir = OUTPUT_DIR / script.id
    run_dir.mkdir(parents=True, exist_ok=True)

    print("[1/5] Synthesizing voiceover...")
    scene_audios = synthesize_script(script, run_dir / "audio")

    print("[2/5] Generating scene illustrations...")
    # One AI image generation per scene (raw, uncropped), reused for both
    # long-form and Short via a cheap local crop per format — keeps API
    # cost to exactly len(scenes) calls regardless of how many formats use it.
    raw_results = [
        generate_scene_image_raw(scene, run_dir / "images_raw" / f"scene_{i:02d}.png")
        for i, scene in enumerate(script.scenes)
    ]
    raw_images = [path for path, _ in raw_results]
    scene_used_fallback = [used_fallback for _, used_fallback in raw_results]
    long_form_images = [
        fit_scene_image(raw, run_dir / "images_long" / f"scene_{i:02d}.png", LONG_FORM_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]
    short_images = [
        fit_scene_image(raw, run_dir / "images_short" / f"scene_{i:02d}.png", SHORT_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]

    music_path = pick_music_track()

    print("[3/5] Assembling long-form video...")
    long_form_path = assemble_long_form(
        script, scene_audios, long_form_images,
        work_dir=run_dir / "work_long", out_path=run_dir / "long.mp4",
        music_path=music_path, scene_used_fallback=scene_used_fallback,
    )

    print("[3/5] Assembling short...")
    short_path = assemble_short(
        script, scene_audios, short_images,
        work_dir=run_dir / "work_short", out_path=run_dir / "short.mp4",
        music_path=music_path, scene_used_fallback=scene_used_fallback,
    )

    print("[4/5] Generating thumbnail...")
    thumbnail_path = generate_thumbnail(script.title, run_dir / "thumbnail.jpg")

    print(f"[5/5] Uploading (dry_run={dry_run})...")
    upload_result = upload_daily_video(
        script, long_form_path, short_path, thumbnail_path,
        privacy_status=privacy_status, dry_run=dry_run,
    )
    print(upload_result)

    mark_used(script_path)
    print(f"Done. Moved {script_path.name} to scripts_queue/used/.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip the real YouTube upload")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, privacy_status=args.privacy))


if __name__ == "__main__":
    main()
