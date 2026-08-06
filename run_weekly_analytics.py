#!/usr/bin/env python3
"""Weekly orchestrator: pulls the last 7 days of channel/video metrics and
writes analytics/reports/{date}.json + .md. No LLM call — bring the .md
report back to a Claude Code session to turn it into next week's scripts.
"""

from __future__ import annotations

from pipeline.analytics import build_report, write_report


def main() -> None:
    report = build_report(days=7)
    json_path, md_path = write_report(report)
    print(f"Wrote {json_path} and {md_path}")


if __name__ == "__main__":
    main()
