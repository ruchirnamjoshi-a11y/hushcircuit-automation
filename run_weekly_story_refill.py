#!/usr/bin/env python3
"""Weekly orchestrator: generates a week's worth of stories per track via
the Gemini API (pipeline.story_writer) and writes them into
scripts_queue/pending/<track>/. Replaces the manual "write ~28 scripts by
hand every week" step — this is the only place in the pipeline that calls
an LLM; run_daily.py still only ever *consumes* the queue.

If GEMINI_API_KEY isn't set, this exits cleanly (not an error) so a missing
key doesn't fail CI — the queue just doesn't get refilled until it's added.
One track's generation failure doesn't block the others.
"""

from __future__ import annotations

import sys
import traceback

from pipeline.config import GEMINI_API_KEY, TRACKS
from pipeline.story_writer import generate_next_episode, generate_stories, write_stories

STORIES_PER_TRACK = 7  # one week of daily buffer per track


def _refill_serialized(track) -> None:
    # Each call continues from the last-saved series state, so episodes
    # must be generated one at a time, in order — not as a single batch
    # call like the standalone tracks.
    for _ in range(STORIES_PER_TRACK):
        episode = generate_next_episode(track)
        written = write_stories(track, [episode])
        for path in written:
            print(f"[{track.key}] Wrote {path} (episode {episode.episode_number})")


def run() -> int:
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY not set — skipping story refill.")
        return 0

    any_failed = False
    for track in TRACKS.values():
        if track.content_type != "story":
            # generate_stories/write_stories only know the Script schema
            # (id/title/description/tags/scenes[...]) -- content_type=
            # "canvas" (math_explainers) needs MathScript's schema instead
            # (piece/narration_lines/params, no AI-generatable "piece"
            # picker built yet) and "song" (manifestation) never reads a
            # pre-written queue at all (see run_daily.run_manifestation_
            # track -- lyrics/scenes are generated live every run). Calling
            # the story generator for either just wrote Script-shaped JSON
            # into their queues, which run_math_track's MathScript.validate
            # rejected outright -- confirmed in production: every scheduled
            # run from 2026-09-05 onward failed on exactly this, and
            # manifestation accumulated 14 dead files it never even reads.
            print(f"[{track.key}] content_type={track.content_type!r} doesn't use the story-schema "
                  f"generator -- skipping (see math_explainers/manifestation refill separately).")
            continue
        try:
            if track.serialized:
                print(f"[{track.key}] Generating {STORIES_PER_TRACK} more episodes...")
                _refill_serialized(track)
            else:
                print(f"[{track.key}] Generating {STORIES_PER_TRACK} stories...")
                scripts = generate_stories(track, count=STORIES_PER_TRACK)
                written = write_stories(track, scripts)
                for path in written:
                    print(f"[{track.key}] Wrote {path}")
        except Exception:
            any_failed = True
            print(f"[{track.key}] FAILED:")
            traceback.print_exc()

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(run())
