#!/usr/bin/env python3
"""Weekly orchestrator: pulls the last 7 days of channel/video metrics for
each track's YouTube channel and writes analytics/reports/<track>/{date}.json
+ .md. No LLM call itself — the weekly-analytics.yml workflow chains
run_weekly_story_refill.py (which does call Gemini) right after this, so
the report currently isn't fed back into story generation yet. Feeding
performance data into the story prompts is a natural next step if that
becomes worth doing.

A missing OAuth token for a track (channel not set up yet) is logged and
skipped, not a failure — the other tracks' reports still get pulled.
"""

from __future__ import annotations

import sys
import traceback

from pipeline.analytics import LOW_QUEUE_THRESHOLD, build_report, count_pending_scripts, write_report
from pipeline.config import TRACKS, youtube_token_path


def _warn_if_queue_low(track) -> None:
    # Story-type tracks get auto-refilled by run_weekly_story_refill.py
    # right after this script in the same workflow, so a low count here is
    # expected and self-correcting -- not worth an alert. Canvas/song
    # tracks (e.g. math_explainers) are deliberately excluded from that
    # refill (hand-authored by design), so a low queue there silently
    # stops production with no automatic recovery -- this is exactly the
    # math_explainers gap (empty 2026-09-11 to 2026-11-04, unnoticed for
    # ~2 months because "Queue empty, skipping" only ever showed up in CI
    # logs nobody reads regularly). `::warning::` is GitHub Actions'
    # annotation syntax -- it surfaces in the run summary UI, not just the
    # raw log, so it doesn't require anyone to go looking for it.
    if track.content_type == "story":
        return
    pending = count_pending_scripts(track.key)
    if pending <= LOW_QUEUE_THRESHOLD:
        print(f"::warning::[{track.key}] Only {pending} script(s) left in the queue and this "
              f"track isn't on the automated refill — write more scripts manually or it will "
              f"silently stop producing videos.")


def run() -> int:
    any_failed = False
    for track in TRACKS.values():
        _warn_if_queue_low(track)
        if track.shares_youtube_channel_with:
            # Confirmed live (2026-08-27, via YouTube's public oEmbed
            # endpoint): this track's videos upload to the SAME channel as
            # shares_youtube_channel_with, so channel==MINE would just
            # return that other track's report again under a different
            # label -- byte-identical and misleading, not a second real
            # data point. See that track's report instead.
            print(f"[{track.key}] Shares a YouTube channel with "
                  f"'{track.shares_youtube_channel_with}' — see that track's report instead of "
                  f"pulling a duplicate one here.")
            continue
        token_path = youtube_token_path(track.key)
        if not token_path.exists():
            print(f"[{track.key}] No YouTube OAuth token at {token_path} — skipping.")
            continue
        try:
            report = build_report(token_path, days=7, track_key=track.key)
            json_path, md_path = write_report(report, track.key, track.label)
            print(f"[{track.key}] Wrote {json_path} and {md_path}")
        except Exception:
            any_failed = True
            print(f"[{track.key}] FAILED:")
            traceback.print_exc()

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(run())
