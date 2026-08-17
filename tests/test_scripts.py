import json

import pytest

from pipeline.scripts import (
    ScriptValidationError,
    load_next_pending,
    mark_used,
    to_dict,
    validate,
)

SAMPLE = {
    "id": "test-topic",
    "title": "Test Title",
    "description": "Test description",
    "tags": ["ai", "tools"],
    "scenes": [
        {"narration": "hook line", "on_screen_text": "ROBOT TYPING", "duration_hint": 4.0},
        {"narration": "body beat one", "on_screen_text": "CODE EDITOR", "duration_hint": 8.0},
        {"narration": "body beat two", "on_screen_text": "CHATBOT UI", "duration_hint": 8.0},
        {"narration": "outro and cta", "on_screen_text": "SUBSCRIBE", "duration_hint": 5.0},
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


def test_validate_defaults_character_reference_to_empty_string():
    script = validate(SAMPLE)
    assert script.character_reference == ""


def test_validate_reads_character_reference_when_present():
    with_ref = json.loads(json.dumps(SAMPLE))
    with_ref["character_reference"] = "a small cream-colored rabbit with a red neckerchief"
    script = validate(with_ref)
    assert script.character_reference == "a small cream-colored rabbit with a red neckerchief"


def test_to_dict_round_trips_character_reference():
    with_ref = json.loads(json.dumps(SAMPLE))
    with_ref["character_reference"] = "a small cream-colored rabbit"
    script = validate(with_ref)
    assert to_dict(script)["character_reference"] == "a small cream-colored rabbit"


def test_validate_defaults_source_script_id_to_empty_string():
    script = validate(SAMPLE)
    assert script.source_script_id == ""


def test_to_dict_round_trips_source_script_id():
    variant = json.loads(json.dumps(SAMPLE))
    variant["source_script_id"] = "test-topic"
    script = validate(variant)
    assert script.source_script_id == "test-topic"
    assert to_dict(script)["source_script_id"] == "test-topic"


def test_scene_image_concept_falls_back_to_on_screen_text_when_visual_blank():
    script = validate(SAMPLE)
    assert script.hook.visual == ""
    assert script.hook.image_concept == "ROBOT TYPING"


def test_scene_image_concept_prefers_visual_when_present():
    with_visual = json.loads(json.dumps(SAMPLE))
    with_visual["scenes"][0]["visual"] = "a small robot typing on a tiny keyboard"
    script = validate(with_visual)
    assert script.hook.image_concept == "a small robot typing on a tiny keyboard"


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
