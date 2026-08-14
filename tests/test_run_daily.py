from unittest.mock import MagicMock, patch

from pipeline.ai_image import CloudflareQuotaExhausted
from pipeline.config import TRACKS

import run_daily

KIDS_TRACK = TRACKS["kids"]
TEENS_TRACK = TRACKS["teens"]


def test_run_track_skips_if_already_produced_today(tmp_path):
    with patch("run_daily.already_produced_today", return_value=True), \
         patch("run_daily.load_next_pending") as mock_load:
        result = run_daily.run_track(KIDS_TRACK, dry_run=False)

    assert result is True
    mock_load.assert_not_called()  # never even looked at the queue


def test_run_track_dry_run_ignores_already_produced_today(tmp_path):
    # dry runs are for local testing and shouldn't be blocked by real
    # production state, nor should they be able to set it
    with patch("run_daily.already_produced_today", return_value=True) as mock_check, \
         patch("run_daily.load_next_pending", return_value=None):
        result = run_daily.run_track(KIDS_TRACK, dry_run=True)

    assert result is True
    mock_check.assert_not_called()


def test_run_track_skips_if_no_youtube_token_yet(tmp_path):
    missing_token = tmp_path / "no_such_token.json"
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=missing_token), \
         patch("run_daily.load_next_pending") as mock_load:
        result = run_daily.run_track(KIDS_TRACK, dry_run=False)

    assert result is True
    mock_load.assert_not_called()  # never even looked at the queue


def test_run_track_dry_run_ignores_missing_youtube_token(tmp_path):
    missing_token = tmp_path / "no_such_token.json"
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=missing_token), \
         patch("run_daily.load_next_pending", return_value=None):
        result = run_daily.run_track(KIDS_TRACK, dry_run=True)

    assert result is True


def test_run_track_raises_quota_exhausted_from_first_scene_probe(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")

    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch("run_daily.generate_scene_image_raw", side_effect=CloudflareQuotaExhausted("exhausted")) as mock_gen, \
         patch("run_daily.synthesize_script_for_track") as mock_tts:
        try:
            run_daily.run_track(KIDS_TRACK, dry_run=False)
            assert False, "expected CloudflareQuotaExhausted to propagate"
        except CloudflareQuotaExhausted:
            pass

    # aborted before TTS ever ran — the probe is the very first real work
    mock_tts.assert_not_called()
    assert mock_gen.call_count == 1
    _, kwargs = mock_gen.call_args
    assert kwargs.get("raise_on_quota_exhausted") is True


def test_run_skips_remaining_tracks_after_quota_exhaustion():
    with patch("run_daily.run_track", side_effect=CloudflareQuotaExhausted("exhausted")) as mock_run_track:
        exit_code = run_daily.run(dry_run=False)

    # every track attempted exactly once before giving up (first raises,
    # the rest are skipped without calling run_track again)
    assert mock_run_track.call_count == 1
    assert exit_code == 0  # quota exhaustion is expected/handled, not a failure


def test_run_continues_past_a_real_failure_on_one_track():
    def fake_run_track(track, dry_run=False, privacy_status="private"):
        if track.key == "kids":
            raise RuntimeError("boom")
        return True

    with patch("run_daily.run_track", side_effect=fake_run_track) as mock_run_track:
        exit_code = run_daily.run(dry_run=False)

    assert mock_run_track.call_count == len(TRACKS)  # all tracks still attempted
    assert exit_code == 1  # a real failure does affect the exit code


def test_run_track_marks_produced_today_only_on_real_success(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")

    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch("run_daily.generate_scene_image_raw", return_value=(tmp_path / "img.png", False)), \
         patch("run_daily.synthesize_script_for_track", return_value=[MagicMock(), MagicMock()]), \
         patch("run_daily.fit_scene_image", return_value=tmp_path / "fitted.png"), \
         patch("run_daily.pick_music_track", return_value=None), \
         patch("run_daily.assemble_short", return_value=tmp_path / "video.mp4"), \
         patch("run_daily.generate_thumbnail", return_value=tmp_path / "thumb.jpg"), \
         patch("run_daily.upload_daily_video", return_value={"video": {}, "thumbnail": {}}), \
         patch("run_daily.mark_used"), \
         patch("run_daily.mark_produced_today") as mock_mark:
        run_daily.run_track(KIDS_TRACK, dry_run=False)

    mock_mark.assert_called_once_with(KIDS_TRACK.key)


def test_story_seed_is_stable_and_varies_by_id():
    assert run_daily._story_seed("the-tryout") == run_daily._story_seed("the-tryout")
    assert run_daily._story_seed("the-tryout") != run_daily._story_seed("a-different-story")


def test_run_track_passes_character_reference_and_shared_seed_to_every_scene(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.character_reference = "a small cream-colored rabbit"
    fake_script.scenes = [MagicMock(), MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")

    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch("run_daily.generate_scene_image_raw", return_value=(tmp_path / "img.png", False)) as mock_gen, \
         patch("run_daily.synthesize_script_for_track", return_value=[MagicMock(), MagicMock(), MagicMock()]), \
         patch("run_daily.fit_scene_image", return_value=tmp_path / "fitted.png"), \
         patch("run_daily.pick_music_track", return_value=None), \
         patch("run_daily.assemble_short", return_value=tmp_path / "video.mp4"), \
         patch("run_daily.generate_thumbnail", return_value=tmp_path / "thumb.jpg"), \
         patch("run_daily.upload_daily_video", return_value={"video": {}, "thumbnail": {}}), \
         patch("run_daily.mark_used"), \
         patch("run_daily.mark_produced_today"):
        run_daily.run_track(KIDS_TRACK, dry_run=False)

    expected_seed = run_daily._story_seed("test-id")
    assert mock_gen.call_count == 3  # every scene, including the probe
    for call in mock_gen.call_args_list:
        assert call.kwargs["character_reference"] == "a small cream-colored rabbit"
        assert call.kwargs["seed"] == expected_seed


def test_run_track_uses_track_key_seed_for_serialized_tracks(tmp_path):
    hero_track = TRACKS["hero_saga"]
    fake_script = MagicMock()
    fake_script.title = "Episode 1"
    fake_script.id = "episode-1"  # deliberately different from track.key
    fake_script.character_reference = "a masked hero in a blue coat"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")

    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch("run_daily.generate_scene_image_raw", return_value=(tmp_path / "img.png", False)) as mock_gen, \
         patch("run_daily.synthesize_script_for_track", return_value=[MagicMock(), MagicMock()]), \
         patch("run_daily.fit_scene_image", return_value=tmp_path / "fitted.png"), \
         patch("run_daily.pick_music_track", return_value=None), \
         patch("run_daily.assemble_short", return_value=tmp_path / "video.mp4"), \
         patch("run_daily.generate_thumbnail", return_value=tmp_path / "thumb.jpg"), \
         patch("run_daily.upload_daily_video", return_value={"video": {}, "thumbnail": {}}), \
         patch("run_daily.mark_used"), \
         patch("run_daily.mark_produced_today"):
        run_daily.run_track(hero_track, dry_run=False)

    expected_seed = run_daily._story_seed(hero_track.key)  # not the episode's script.id
    for call in mock_gen.call_args_list:
        assert call.kwargs["seed"] == expected_seed
