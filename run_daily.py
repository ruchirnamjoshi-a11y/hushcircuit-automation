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

from pipeline.assemble import assemble_long_form, pick_music_track
from pipeline.broll import fetch_broll
from pipeline.config import OUTPUT_DIR
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

    print("[2/5] Fetching b-roll...")
    broll_cache: dict = {}
    broll_clips = [
        fetch_broll(scene.visual_keyword, min_duration=audio.duration, out_dir=run_dir / "broll", cache=broll_cache)
        for scene, audio in zip(script.scenes, scene_audios)
    ]

    music_path = pick_music_track()

    print("[3/5] Assembling long-form video...")
    long_form_path = assemble_long_form(
        scene_audios, broll_clips,
        work_dir=run_dir / "work_long", out_path=run_dir / "long.mp4",
        music_path=music_path,
    )

    print("[3/5] Assembling short...")
    short_path = assemble_short(
        script, scene_audios, broll_clips,
        work_dir=run_dir / "work_short", out_path=run_dir / "short.mp4",
        music_path=music_path,
    )

    print("[4/5] Generating thumbnail...")
    thumbnail_path = generate_thumbnail(broll_clips[0].path, script.title, run_dir / "thumbnail.jpg")

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
