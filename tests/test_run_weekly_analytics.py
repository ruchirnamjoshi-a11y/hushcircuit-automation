import dataclasses
from unittest.mock import patch

from pipeline.config import TRACKS

import run_weekly_analytics

MATH_TRACK = TRACKS["math_explainers"]  # content_type="canvas", not auto-refilled


def test_warn_if_queue_low_stays_silent_for_story_tracks():
    # Story tracks get auto-refilled by run_weekly_story_refill.py right
    # after this script in the same workflow, so a low count here is
    # expected and self-correcting -- warning about it would just be noise.
    story_track = dataclasses.replace(TRACKS["teens"], key="story-fixture", content_type="story")
    with patch("run_weekly_analytics.count_pending_scripts", return_value=0) as mock_count:
        run_weekly_analytics._warn_if_queue_low(story_track)
    mock_count.assert_not_called()


def test_warn_if_queue_low_warns_for_low_non_story_queue(capsys):
    # Real production bug: math_explainers' queue sat empty for ~2 months
    # (2026-09-11 to 2026-11-04) unnoticed, because canvas tracks are
    # deliberately excluded from the automated refill and "Queue empty,
    # skipping" only ever showed up in CI logs nobody reads regularly.
    with patch("run_weekly_analytics.count_pending_scripts", return_value=2):
        run_weekly_analytics._warn_if_queue_low(MATH_TRACK)

    captured = capsys.readouterr()
    assert "::warning::" in captured.out
    assert "math_explainers" in captured.out
    assert "2 script(s) left" in captured.out


def test_warn_if_queue_low_silent_for_healthy_non_story_queue(capsys):
    with patch("run_weekly_analytics.count_pending_scripts", return_value=20):
        run_weekly_analytics._warn_if_queue_low(MATH_TRACK)

    captured = capsys.readouterr()
    assert captured.out == ""
