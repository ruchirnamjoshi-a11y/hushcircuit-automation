from datetime import date, timedelta

from pipeline.state import already_produced_today, mark_produced_today, produced_count_today


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


def test_higher_limit_allows_a_second_video_the_same_day(tmp_path):
    mark_produced_today("math_explainers", state_dir=tmp_path)
    assert produced_count_today("math_explainers", state_dir=tmp_path) == 1
    # limit=2 (Track.videos_per_day): one video in isn't at the limit yet
    assert already_produced_today("math_explainers", limit=2, state_dir=tmp_path) is False

    mark_produced_today("math_explainers", state_dir=tmp_path)
    assert produced_count_today("math_explainers", state_dir=tmp_path) == 2
    assert already_produced_today("math_explainers", limit=2, state_dir=tmp_path) is True


def test_default_limit_of_one_matches_old_single_video_per_day_behavior(tmp_path):
    mark_produced_today("kids", state_dir=tmp_path)
    assert already_produced_today("kids", state_dir=tmp_path) is True  # limit=1 default


def test_produced_count_today_resets_for_a_past_date(tmp_path):
    state_path = tmp_path / "kids.json"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(f'{{"last_produced_date": "{yesterday}", "count": 5}}')
    assert produced_count_today("kids", state_dir=tmp_path) == 0
