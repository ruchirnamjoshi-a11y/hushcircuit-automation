from datetime import date
from unittest.mock import MagicMock, patch

from pipeline.analytics import (
    _rows_to_dicts,
    build_report,
    fetch_channel_metrics,
    fetch_top_videos,
    write_report,
)

CHANNEL_RESPONSE = {
    "columnHeaders": [
        {"name": "views"}, {"name": "estimatedMinutesWatched"},
        {"name": "averageViewDuration"}, {"name": "averageViewPercentage"},
        {"name": "subscribersGained"}, {"name": "subscribersLost"},
    ],
    "rows": [[1200, 3400, 170, 62.5, 15, 2]],
}

VIDEO_RESPONSE = {
    "columnHeaders": [{"name": "video"}, {"name": "views"}, {"name": "averageViewDuration"}, {"name": "averageViewPercentage"}],
    "rows": [
        ["abc123", 800, 180, 65.0],
        ["def456", 400, 150, 58.0],
    ],
}


def test_rows_to_dicts_zips_headers_and_values():
    result = _rows_to_dicts(CHANNEL_RESPONSE)
    assert result == [{
        "views": 1200, "estimatedMinutesWatched": 3400, "averageViewDuration": 170,
        "averageViewPercentage": 62.5, "subscribersGained": 15, "subscribersLost": 2,
    }]


def test_rows_to_dicts_handles_empty_response():
    assert _rows_to_dicts({"columnHeaders": [], "rows": []}) == []


def _mock_service(response):
    service = MagicMock()
    service.reports.return_value.query.return_value.execute.return_value = response
    return service


def test_fetch_channel_metrics_returns_first_row_as_dict():
    with patch("pipeline.analytics.get_analytics_service", return_value=_mock_service(CHANNEL_RESPONSE)):
        result = fetch_channel_metrics(date(2026, 7, 1), date(2026, 7, 7))
    assert result["views"] == 1200
    assert result["subscribersGained"] == 15


def test_fetch_top_videos_returns_all_rows():
    with patch("pipeline.analytics.get_analytics_service", return_value=_mock_service(VIDEO_RESPONSE)):
        result = fetch_top_videos(date(2026, 7, 1), date(2026, 7, 7))
    assert len(result) == 2
    assert result[0]["video"] == "abc123"
    assert result[0]["views"] == 800


def test_build_report_combines_channel_and_video_data():
    with patch("pipeline.analytics.fetch_channel_metrics", return_value={"views": 1200}), \
         patch("pipeline.analytics.fetch_top_videos", return_value=[{"video": "abc123", "views": 800}]):
        report = build_report(days=7)

    assert report["channel"] == {"views": 1200}
    assert report["top_videos"] == [{"video": "abc123", "views": 800}]
    assert report["start_date"] < report["end_date"]


def test_write_report_writes_json_and_markdown(tmp_path):
    report = {
        "start_date": "2026-07-01", "end_date": "2026-07-07",
        "channel": {"views": 1200, "subscribersGained": 15},
        "top_videos": [{"video": "abc123", "views": 800}],
    }
    json_path, md_path = write_report(report, reports_dir=tmp_path)

    assert json_path.exists() and json_path.name == "2026-07-07.json"
    assert md_path.exists() and md_path.name == "2026-07-07.md"

    md_content = md_path.read_text()
    assert "Weekly Analytics Report" in md_content
    assert "views" in md_content
    assert "abc123" in md_content
