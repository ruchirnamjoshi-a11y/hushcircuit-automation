"""Stage 8: pull last-N-day performance metrics from the YouTube Analytics API.

Multi-channel: each track has its own YouTube channel, so metrics are
pulled and reported per track — see pipeline.config.youtube_token_path.

No LLM call here — this only pulls and writes the raw report. Turning it
into "what's working, what to script next" happens interactively: bring
the .md report back to a Claude Code session in this repo.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from googleapiclient.discovery import build

from pipeline.config import ANALYTICS_REPORTS_DIR
from pipeline.upload import load_credentials

ANALYTICS_API_SERVICE_NAME = "youtubeAnalytics"
ANALYTICS_API_VERSION = "v2"

CHANNEL_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,subscribersGained,subscribersLost"
)
VIDEO_METRICS = "views,averageViewDuration,averageViewPercentage"


def get_analytics_service(token_path: Path):
    return build(ANALYTICS_API_SERVICE_NAME, ANALYTICS_API_VERSION, credentials=load_credentials(token_path))


def _rows_to_dicts(response: dict) -> list[dict]:
    headers = [h["name"] for h in response.get("columnHeaders", [])]
    return [dict(zip(headers, row)) for row in response.get("rows", [])]


def fetch_channel_metrics(token_path: Path, start_date: date, end_date: date) -> dict:
    response = get_analytics_service(token_path).reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics=CHANNEL_METRICS,
    ).execute()
    rows = _rows_to_dicts(response)
    return rows[0] if rows else {}


def fetch_top_videos(token_path: Path, start_date: date, end_date: date, max_results: int = 10) -> list[dict]:
    response = get_analytics_service(token_path).reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics=VIDEO_METRICS,
        dimensions="video",
        sort="-views",
        maxResults=max_results,
    ).execute()
    return _rows_to_dicts(response)


def build_report(token_path: Path, days: int = 7) -> dict:
    # Analytics data typically lags ~1-2 days, so end the window yesterday.
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "channel": fetch_channel_metrics(token_path, start_date, end_date),
        "top_videos": fetch_top_videos(token_path, start_date, end_date),
    }


def _report_to_markdown(report: dict, track_label: str) -> str:
    lines = [
        f"# Weekly Analytics Report — {track_label} ({report['start_date']} to {report['end_date']})",
        "",
        "## Channel totals",
    ]
    for key, value in report["channel"].items():
        lines.append(f"- **{key}**: {value}")

    lines += ["", "## Top videos this week"]
    if report["top_videos"]:
        for video in report["top_videos"]:
            lines.append(f"- `{video.get('video')}` — {video}")
    else:
        lines.append("- No video-level data returned for this window.")

    return "\n".join(lines) + "\n"


def write_report(
    report: dict, track_key: str, track_label: str, reports_dir: Path = ANALYTICS_REPORTS_DIR,
) -> tuple[Path, Path]:
    track_dir = reports_dir / track_key
    track_dir.mkdir(parents=True, exist_ok=True)
    date_str = report["end_date"]
    json_path = track_dir / f"{date_str}.json"
    md_path = track_dir / f"{date_str}.md"

    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(_report_to_markdown(report, track_label))

    return json_path, md_path
