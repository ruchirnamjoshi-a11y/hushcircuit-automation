from datetime import date, timedelta

from pipeline.state import already_produced_today, mark_produced_today


def test_already_produced_today_false_when_no_state_file(tmp_path):
    assert already_produced_today("kids", state_dir=tmp_path) is False


def test_mark_then_already_produced_today_true(tmp_path):
    mark_produced_today("kids", state_dir=tmp_path)
    assert already_produced_today("kids", state_dir=tmp_path) is True


def test_state_is_per_track(tmp_path):
    mark_produced_today("kids", state_dir=tmp_path)
    assert already_produced_today("teens", state_dir=tmp_path) is False


def test_already_produced_today_false_for_a_past_date(tmp_path):
    state_path = tmp_path / "kids.json"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(f'{{"last_produced_date": "{yesterday}"}}')
    assert already_produced_today("kids", state_dir=tmp_path) is False


def test_already_produced_today_false_on_corrupt_state_file(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "kids.json").write_text("not valid json{{{")
    assert already_produced_today("kids", state_dir=tmp_path) is False
