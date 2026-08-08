import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.config import TRACKS
from pipeline.story_writer import generate_stories, write_stories

KIDS_TRACK = TRACKS["kids"]

VALID_STORY = {
    "id": "test-story",
    "title": "Test Story",
    "description": "A test story.",
    "tags": ["test", "story"],
    "scenes": [
        {"narration": "hook line", "on_screen_text": "HOOK", "duration_hint": 4.0, "visual": "a small robot"},
        {"narration": "body beat", "on_screen_text": "BEAT", "duration_hint": 5.0, "visual": "a busy street"},
        {"narration": "outro cta", "on_screen_text": "SUBSCRIBE", "duration_hint": 3.0, "visual": "a sunset"},
    ],
}


def _fake_gemini_response(payload: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]
    }
    return mock_response


def test_generate_stories_returns_validated_scripts(tmp_path):
    mock_response = _fake_gemini_response({"stories": [VALID_STORY]})
    with patch("pipeline.story_writer.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.story_writer.requests.post", return_value=mock_response) as mock_post, \
         patch("pipeline.story_writer.QUEUE_PENDING_DIR", tmp_path / "pending"), \
         patch("pipeline.story_writer.QUEUE_USED_DIR", tmp_path / "used"):
        scripts = generate_stories(KIDS_TRACK, count=1)

    assert len(scripts) == 1
    assert scripts[0].id == "test-story"
    assert len(scripts[0].scenes) == 3
    # sanity-check the request included structured-output constraints
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_generate_stories_skips_invalid_entries_but_keeps_valid_ones(tmp_path):
    invalid_story = {"id": "broken", "title": "Broken"}  # missing required fields
    mock_response = _fake_gemini_response({"stories": [invalid_story, VALID_STORY]})
    with patch("pipeline.story_writer.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.story_writer.requests.post", return_value=mock_response), \
         patch("pipeline.story_writer.QUEUE_PENDING_DIR", tmp_path / "pending"), \
         patch("pipeline.story_writer.QUEUE_USED_DIR", tmp_path / "used"):
        scripts = generate_stories(KIDS_TRACK, count=2)

    assert len(scripts) == 1
    assert scripts[0].id == "test-story"


def test_generate_stories_raises_if_all_entries_invalid(tmp_path):
    mock_response = _fake_gemini_response({"stories": [{"id": "broken"}]})
    with patch("pipeline.story_writer.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.story_writer.requests.post", return_value=mock_response), \
         patch("pipeline.story_writer.QUEUE_PENDING_DIR", tmp_path / "pending"), \
         patch("pipeline.story_writer.QUEUE_USED_DIR", tmp_path / "used"):
        with pytest.raises(RuntimeError):
            generate_stories(KIDS_TRACK, count=1)


def test_generate_stories_raises_without_api_key(tmp_path):
    with patch("pipeline.story_writer.GEMINI_API_KEY", ""):
        with pytest.raises(RuntimeError):
            generate_stories(KIDS_TRACK, count=1)


def test_write_stories_numbers_sequentially_after_existing_files(tmp_path):
    pending_dir = tmp_path / "pending" / "kids"
    used_dir = tmp_path / "used" / "kids"
    pending_dir.mkdir(parents=True)
    used_dir.mkdir(parents=True)
    (used_dir / "001-old-story.json").write_text("{}")
    (pending_dir / "002-another-story.json").write_text("{}")

    from pipeline.scripts import validate
    scripts = [validate(VALID_STORY)]

    with patch("pipeline.story_writer.QUEUE_PENDING_DIR", tmp_path / "pending"), \
         patch("pipeline.story_writer.QUEUE_USED_DIR", tmp_path / "used"):
        written = write_stories(KIDS_TRACK, scripts)

    assert len(written) == 1
    assert written[0].name == "003-test-story.json"
    assert written[0].exists()
    data = json.loads(written[0].read_text())
    assert data["id"] == "test-story"
