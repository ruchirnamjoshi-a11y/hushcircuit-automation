import json

import pytest

from pipeline.math_scripts import MathScriptValidationError, load_next_pending, validate
from pipeline.scripts import mark_used

SAMPLE = {
    "id": "gauss-trick",
    "title": "Gauss's Trick",
    "description": "test",
    "tags": ["math"],
    "piece": "gauss_trick",  # a real piece must exist at math_pieces/gauss_trick/piece.html
    "narration_lines": ["Line one.", "Line two."],
}


def test_validate_accepts_well_formed_manifest():
    script = validate(SAMPLE)
    assert script.id == "gauss-trick"
    assert script.piece == "gauss_trick"
    assert script.narration_lines == ["Line one.", "Line two."]
    assert script.tags == ["math"]


def test_validate_rejects_missing_top_level_field():
    bad = {k: v for k, v in SAMPLE.items() if k != "title"}
    with pytest.raises(MathScriptValidationError):
        validate(bad)


def test_validate_rejects_empty_narration_lines():
    bad = json.loads(json.dumps(SAMPLE))
    bad["narration_lines"] = []
    with pytest.raises(MathScriptValidationError):
        validate(bad)


def test_validate_rejects_nonexistent_piece():
    bad = json.loads(json.dumps(SAMPLE))
    bad["piece"] = "no-such-piece-exists"
    with pytest.raises(MathScriptValidationError):
        validate(bad)


def test_validate_defaults_tags_to_empty_list():
    no_tags = {k: v for k, v in SAMPLE.items() if k != "tags"}
    script = validate(no_tags)
    assert script.tags == []


def test_queue_load_and_mark_used(tmp_path):
    pending_dir = tmp_path / "pending"
    used_dir = tmp_path / "used"
    pending_dir.mkdir()

    (pending_dir / "001_first.json").write_text(json.dumps(SAMPLE))
    second = json.loads(json.dumps(SAMPLE))
    second["id"] = "second-piece"
    (pending_dir / "002_second.json").write_text(json.dumps(second))

    path, script = load_next_pending(pending_dir)
    assert path.name == "001_first.json"
    assert script.id == "gauss-trick"

    new_path = mark_used(path, used_dir)
    assert new_path == used_dir / "001_first.json"
    assert not path.exists()
    assert new_path.exists()

    path2, script2 = load_next_pending(pending_dir)
    assert path2.name == "002_second.json"
    assert script2.id == "second-piece"


def test_load_next_pending_returns_none_for_empty_queue(tmp_path):
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    assert load_next_pending(pending_dir) is None
