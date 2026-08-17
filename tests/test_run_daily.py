from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.ai_image import CloudflareQuotaExhausted
from pipeline.config import TRACKS

import run_daily

KIDS_TRACK = TRACKS["kids"]  # produce_long_form=True, shares_images_with=""
TEENS_TRACK = TRACKS["teens"]  # produce_long_form=False, shares_images_with=""
HINDI_TRACK = TRACKS["hindi_mythology"]  # produce_long_form=True, shares_images_with="kids"
MATH_TRACK = TRACKS["math_explainers"]  # content_type="canvas"


def _patch_pipeline(**overrides):
    """Common set of downstream-stage patches for a successful run_track
    pass, with sane defaults; pass overrides to replace specific ones."""
    defaults = dict(
        generate_scene_image_raw=MagicMock(return_value=(Path("/fake/img.png"), False)),
        synthesize_script_for_track=MagicMock(return_value=[MagicMock(), MagicMock()]),
        fit_scene_image=MagicMock(return_value=Path("/fake/fitted.png")),
        pick_music_track=MagicMock(return_value=None),
        assemble_short=MagicMock(return_value=Path("/fake/video.mp4")),
        generate_thumbnail=MagicMock(return_value=Path("/fake/thumb.jpg")),
        upload_daily_video=MagicMock(return_value={"video": {}, "thumbnail": {}}),
        mark_used=MagicMock(),
        mark_produced_today=MagicMock(),
    )
    defaults.update(overrides)
    return defaults


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


def test_run_track_raises_quota_exhausted_from_reference_portrait_probe(tmp_path):
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

    # aborted before TTS ever ran — the reference-portrait probe is the very first real work
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
    def fake_run_track(track, dry_run=False, privacy_status="private", produced_images=None):
        if track.key == "kids":
            raise RuntimeError("boom")
        return True

    # run() dispatches content_type="canvas" tracks (math_explainers) to
    # run_math_track instead of run_track -- both need mocking here, or the
    # real (unmocked) run_math_track runs for real against whatever's
    # actually queued/authorized on this machine.
    with patch("run_daily.run_track", side_effect=fake_run_track) as mock_run_track, \
         patch("run_daily.run_math_track", return_value=True) as mock_run_math_track:
        exit_code = run_daily.run(dry_run=False)

    story_tracks = [t for t in TRACKS.values() if t.content_type != "canvas"]
    canvas_tracks = [t for t in TRACKS.values() if t.content_type == "canvas"]
    assert mock_run_track.call_count == len(story_tracks)  # all story tracks still attempted
    assert mock_run_math_track.call_count == len(canvas_tracks)
    assert exit_code == 1  # a real failure does affect the exit code


def test_run_track_marks_produced_today_only_on_real_success(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"fake-image-bytes")

    patches = _patch_pipeline(
        generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)),
    )
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(KIDS_TRACK, dry_run=False)

    patches["mark_produced_today"].assert_called_once_with(KIDS_TRACK.key)


def test_story_seed_is_stable_and_varies_by_id():
    assert run_daily._story_seed("the-tryout") == run_daily._story_seed("the-tryout")
    assert run_daily._story_seed("the-tryout") != run_daily._story_seed("a-different-story")


def test_run_track_passes_character_reference_and_shared_seed_to_every_call(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.character_reference = "a small cream-colored rabbit"
    fake_script.scenes = [MagicMock(), MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"fake-image-bytes")

    patches = _patch_pipeline(
        generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)),
        synthesize_script_for_track=MagicMock(return_value=[MagicMock(), MagicMock(), MagicMock()]),
    )
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(KIDS_TRACK, dry_run=False)

    mock_gen = patches["generate_scene_image_raw"]
    expected_seed = run_daily._story_seed("test-id")
    # one reference-portrait call + one call per scene (3 scenes)
    assert mock_gen.call_count == 4
    for call in mock_gen.call_args_list:
        assert call.kwargs["character_reference"] == "a small cream-colored rabbit"
        assert call.kwargs["seed"] == expected_seed


def test_run_track_threads_reference_portrait_to_every_scene(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.character_reference = ""
    fake_script.scenes = [MagicMock(), MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_bytes = b"the-reference-portraits-real-image-bytes"
    fake_image_path.write_bytes(fake_image_bytes)

    patches = _patch_pipeline(
        generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)),
        synthesize_script_for_track=MagicMock(return_value=[MagicMock(), MagicMock(), MagicMock()]),
    )
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(KIDS_TRACK, dry_run=False)

    calls = patches["generate_scene_image_raw"].call_args_list
    assert len(calls) == 4  # portrait + 3 scenes
    # the portrait call predates its own image existing, so it's not passed
    # a reference at all (not just None)
    assert calls[0].kwargs.get("reference_image_bytes") is None
    # every scene (including the first) gets the portrait's real bytes
    assert calls[1].kwargs["reference_image_bytes"] == fake_image_bytes
    assert calls[2].kwargs["reference_image_bytes"] == fake_image_bytes
    assert calls[3].kwargs["reference_image_bytes"] == fake_image_bytes


def test_run_track_omits_reference_image_when_portrait_used_fallback(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.character_reference = ""
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"gradient-fallback-bytes")

    # every call (portrait included) reports used_fallback=True
    patches = _patch_pipeline(
        generate_scene_image_raw=MagicMock(return_value=(fake_image_path, True)),
        synthesize_script_for_track=MagicMock(return_value=[MagicMock(), MagicMock()]),
    )
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(KIDS_TRACK, dry_run=False)

    calls = patches["generate_scene_image_raw"].call_args_list
    assert calls[1].kwargs["reference_image_bytes"] is None
    assert calls[2].kwargs["reference_image_bytes"] is None


def test_run_track_uses_track_key_seed_for_serialized_tracks(tmp_path):
    hero_track = TRACKS["hero_saga"]
    fake_script = MagicMock()
    fake_script.title = "Episode 1"
    fake_script.id = "episode-1"  # deliberately different from track.key
    fake_script.character_reference = "a masked hero in a blue coat"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"fake-image-bytes")

    patches = _patch_pipeline(
        generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)),
    )
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(hero_track, dry_run=False)

    expected_seed = run_daily._story_seed(hero_track.key)  # not the episode's script.id
    for call in patches["generate_scene_image_raw"].call_args_list:
        assert call.kwargs["seed"] == expected_seed


def test_run_track_reuses_sibling_images_without_calling_cloudflare(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Hindi Version"
    fake_script.id = "story-hi"
    fake_script.source_script_id = "story-en"
    fake_script.character_reference = ""
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")

    source_images_dir = tmp_path / "source_images_raw"
    source_images_dir.mkdir()
    produced_images = {("kids", "story-en"): (source_images_dir, [False, False])}

    patches = _patch_pipeline()
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(HINDI_TRACK, dry_run=False, produced_images=produced_images)

    patches["generate_scene_image_raw"].assert_not_called()
    fit_calls = patches["fit_scene_image"].call_args_list
    assert fit_calls[0].args[0] == source_images_dir / "scene_00.png"
    assert fit_calls[1].args[0] == source_images_dir / "scene_01.png"


def test_run_track_falls_back_to_independent_generation_when_no_sibling_images(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Hindi Version"
    fake_script.id = "story-hi"
    fake_script.source_script_id = "story-en"
    fake_script.character_reference = ""
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"fake-image-bytes")

    patches = _patch_pipeline(
        generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)),
    )
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(HINDI_TRACK, dry_run=False, produced_images={})  # nothing to reuse

    # portrait + 2 scenes, generated independently since no sibling images exist yet
    assert patches["generate_scene_image_raw"].call_count == 3


def test_run_track_produce_long_form_uploads_both_short_and_long(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"fake-image-bytes")

    patches = _patch_pipeline(generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)))
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(KIDS_TRACK, dry_run=False)  # produce_long_form=True

    assemble_calls = patches["assemble_short"].call_args_list
    upload_calls = patches["upload_daily_video"].call_args_list
    assert len(assemble_calls) == 2
    assert len(upload_calls) == 2
    max_seconds_values = [c.kwargs.get("max_seconds", "default") for c in assemble_calls]
    assert None in max_seconds_values  # the untrimmed long-form pass
    is_short_values = sorted(c.kwargs["is_short"] for c in upload_calls)
    assert is_short_values == [False, True]


def test_run_track_produce_long_form_false_uploads_only_short(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Test"
    fake_script.id = "test-id"
    fake_script.scenes = [MagicMock(), MagicMock()]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"fake-image-bytes")

    patches = _patch_pipeline(generate_scene_image_raw=MagicMock(return_value=(fake_image_path, False)))
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_track(TEENS_TRACK, dry_run=False)  # produce_long_form=False

    assert len(patches["assemble_short"].call_args_list) == 1
    upload_calls = patches["upload_daily_video"].call_args_list
    assert len(upload_calls) == 1
    assert upload_calls[0].kwargs["is_short"] is True


def _patch_math_pipeline(**overrides):
    fake_line_times = [MagicMock(end=5.0)]
    defaults = dict(
        synthesize_narration_lines=MagicMock(return_value=(Path("/fake/narration.mp3"), fake_line_times)),
        render_piece_video=MagicMock(return_value=Path("/fake/video.mp4")),
        pick_music_track=MagicMock(return_value=None),
        generate_thumbnail=MagicMock(return_value=Path("/fake/thumb.jpg")),
        upload_daily_video=MagicMock(return_value={"video": {}, "thumbnail": {}}),
        mark_used=MagicMock(),
        mark_produced_today=MagicMock(),
    )
    defaults.update(overrides)
    return defaults


def test_run_math_track_skips_if_already_produced_today():
    with patch("run_daily.already_produced_today", return_value=True), \
         patch("run_daily.load_next_pending_math") as mock_load:
        result = run_daily.run_math_track(MATH_TRACK, dry_run=False)

    assert result is True
    mock_load.assert_not_called()


def test_run_math_track_skips_if_no_youtube_token_yet(tmp_path):
    missing_token = tmp_path / "no_such_token.json"
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=missing_token), \
         patch("run_daily.load_next_pending_math") as mock_load:
        result = run_daily.run_math_track(MATH_TRACK, dry_run=False)

    assert result is True
    mock_load.assert_not_called()


def test_run_math_track_skips_cleanly_on_empty_queue(tmp_path):
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending_math", return_value=None) as mock_load:
        result = run_daily.run_math_track(MATH_TRACK, dry_run=False)

    assert result is True
    mock_load.assert_called_once()


def test_run_math_track_produces_and_uploads_real_pending_piece(tmp_path):
    fake_script = MagicMock()
    fake_script.title = "Gauss's Trick"
    fake_script.id = "gauss-trick"
    fake_script.piece = "gauss_trick"
    fake_script.narration_lines = ["line one", "line two"]
    existing_token = tmp_path / "token.json"
    existing_token.write_text("{}")

    patches = _patch_math_pipeline()
    with patch("run_daily.already_produced_today", return_value=False), \
         patch("run_daily.youtube_token_path", return_value=existing_token), \
         patch("run_daily.load_next_pending_math", return_value=(tmp_path / "script.json", fake_script)), \
         patch("run_daily.OUTPUT_DIR", tmp_path), \
         patch.multiple("run_daily", **patches):
        run_daily.run_math_track(MATH_TRACK, dry_run=False)

    patches["synthesize_narration_lines"].assert_called_once()
    call = patches["synthesize_narration_lines"].call_args
    assert call.args[0] == ["line one", "line two"]
    assert call.args[2] == MATH_TRACK.voice

    patches["render_piece_video"].assert_called_once()
    patches["pick_music_track"].assert_called_once()
    assert "music_path" in patches["render_piece_video"].call_args.kwargs

    upload_call = patches["upload_daily_video"].call_args
    assert upload_call.args[0] is fake_script
    assert upload_call.kwargs["is_short"] is True
    patches["mark_produced_today"].assert_called_once_with(MATH_TRACK.key)


def test_run_dispatches_canvas_tracks_to_run_math_track():
    def fake_run_track(*a, **k):
        raise AssertionError("run_track should not be called for a content_type='canvas' track")

    with patch("run_daily.run_track", side_effect=fake_run_track), \
         patch("run_daily.run_math_track", return_value=True) as mock_math:
        run_daily.run(dry_run=False, track_key="math_explainers")

    mock_math.assert_called_once()
    assert mock_math.call_args.args[0] is MATH_TRACK
