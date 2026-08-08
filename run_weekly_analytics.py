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

from pipeline.analytics import build_report, write_report
from pipeline.config import TRACKS, youtube_token_path


def run() -> int:
    any_failed = False
    for track in TRACKS.values():
        token_path = youtube_token_path(track.key)
        if not token_path.exists():
            print(f"[{track.key}] No YouTube OAuth token at {token_path} — skipping.")
            continue
        try:
            report = build_report(token_path, days=7)
            json_path, md_path = write_report(report, track.key, track.label)
            print(f"[{track.key}] Wrote {json_path} and {md_path}")
        except Exception:
            any_failed = True
            print(f"[{track.key}] FAILED:")
            traceback.print_exc()

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(run())
