import json

import pytest

from pipeline.scripts import (
    ScriptValidationError,
    load_next_pending,
    mark_used,
    validate,
)

SAMPLE = {
    "id": "test-topic",
    "title": "Test Title",
    "description": "Test description",
    "tags": ["ai", "tools"],
    "scenes": [
        {"narration": "hook line", "visual_keyword": "robot typing", "duration_hint": 4.0, "short_worthy": True},
        {"narration": "body beat one", "visual_keyword": "code editor", "duration_hint": 8.0, "short_worthy": False},
        {"narration": "body beat two", "visual_keyword": "chatbot ui", "duration_hint": 8.0, "short_worthy": True},
        {"narration": "outro and cta", "visual_keyword": "subscribe button", "duration_hint": 5.0, "short_worthy": True},
    ],
}


def test_validate_accepts_well_formed_script():
    script = validate(SAMPLE)
    assert script.id == "test-topic"
    assert len(script.scenes) == 4
    assert script.hook.narration == "hook line"
    assert script.outro.narration == "outro and cta"
    assert len(script.body_scenes) == 2
    assert script.total_duration() == 25.0


def test_short_scenes_includes_hook_and_flagged_scenes():
    script = validate(SAMPLE)
    short = script.short_scenes
    assert short[0] is script.hook
    assert len(short) == 3  # hook + 2 flagged (body beat two, outro)


def test_validate_rejects_missing_top_level_field():
    bad = {k: v for k, v in SAMPLE.items() if k != "title"}
    with pytest.raises(ScriptValidationError):
        validate(bad)


def test_validate_rejects_missing_scene_field():
    bad = json.loads(json.dumps(SAMPLE))
    del bad["scenes"][0]["duration_hint"]
    with pytest.raises(ScriptValidationError):
        validate(bad)


def test_validate_rejects_too_few_scenes():
    bad = json.loads(json.dumps(SAMPLE))
    bad["scenes"] = bad["scenes"][:1]
    with pytest.raises(ScriptValidationError):
        validate(bad)


def test_queue_load_and_mark_used(tmp_path):
    pending_dir = tmp_path / "pending"
    used_dir = tmp_path / "used"
    pending_dir.mkdir()

    (pending_dir / "001_first.json").write_text(json.dumps(SAMPLE))
    second = json.loads(json.dumps(SAMPLE))
    second["id"] = "second-topic"
    (pending_dir / "002_second.json").write_text(json.dumps(second))

    path, script = load_next_pending(pending_dir)
    assert path.name == "001_first.json"
    assert script.id == "test-topic"

    new_path = mark_used(path, used_dir)
    assert new_path == used_dir / "001_first.json"
    assert not path.exists()
    assert new_path.exists()

    # next call now returns the second file
    path2, script2 = load_next_pending(pending_dir)
    assert path2.name == "002_second.json"
    assert script2.id == "second-topic"


def test_load_next_pending_returns_none_when_empty(tmp_path):
    empty_dir = tmp_path / "pending"
    empty_dir.mkdir()
    assert load_next_pending(empty_dir) is None
