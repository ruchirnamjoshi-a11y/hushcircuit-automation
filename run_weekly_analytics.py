#!/usr/bin/env python3
"""Weekly orchestrator: pulls the last 7 days of channel/video metrics and
writes analytics/reports/{date}.json + .md. No LLM call itself — the
weekly-analytics.yml workflow chains run_weekly_story_refill.py (which does
call Gemini) right after this, so the report currently isn't fed back into
story generation yet. Feeding performance data into the story prompts is a
natural next step if that becomes worth doing.
"""

from __future__ import annotations

from pipeline.analytics import build_report, write_report


def main() -> None:
    report = build_report(days=7)
    json_path, md_path = write_report(report)
    print(f"Wrote {json_path} and {md_path}")


if __name__ == "__main__":
    main()
