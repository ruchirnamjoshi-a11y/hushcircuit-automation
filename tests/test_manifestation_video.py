from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import pipeline.manifestation_video as mv
from pipeline.ai_image import CloudflareQuotaExhausted, CloudflareUnavailable


def _flaky_then_ok(fail_times):
    calls = {"n": 0}

    def call(prompt, seed=None, reference_image_bytes=None, resolution=None):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise TimeoutError("read timed out")
        return MagicMock()

    return call, calls


def test_retries_transient_failure_then_succeeds():
    # Real production bug (2026-09-10, three separate runs in one day):
    # every _call_cloudflare call in this module was naked -- no retry at
    # all -- so a single timeout or transient 5xx crashed the whole run
    # even after lyrics/song synthesis/transcription had all already
    # succeeded. This is the fix: retry like the story tracks' generate_
    # scene_image_raw already does.
    call, calls = _flaky_then_ok(fail_times=2)
    with patch("pipeline.manifestation_video._call_cloudflare", side_effect=call), \
         patch("pipeline.manifestation_video.time.sleep"):
        mv._call_cloudflare_with_retry("prompt", label="test")
    assert calls["n"] == 3


def test_raises_cloudflare_unavailable_after_exhausting_retries():
    def always_fails(prompt, seed=None, reference_image_bytes=None, resolution=None):
        raise TimeoutError("read timed out")

    with patch("pipeline.manifestation_video._call_cloudflare", side_effect=always_fails), \
         patch("pipeline.manifestation_video.time.sleep"):
        with pytest.raises(CloudflareUnavailable):
            mv._call_cloudflare_with_retry("prompt", label="test")


def test_quota_exhaustion_is_not_retried():
    attempts = {"n": 0}

    def quota_exhausted(prompt, seed=None, reference_image_bytes=None, resolution=None):
        attempts["n"] += 1
        raise CloudflareQuotaExhausted("daily neurons exhausted")

    with patch("pipeline.manifestation_video._call_cloudflare", side_effect=quota_exhausted), \
         patch("pipeline.manifestation_video.time.sleep"):
        with pytest.raises(CloudflareQuotaExhausted):
            mv._call_cloudflare_with_retry("prompt", label="test")
    assert attempts["n"] == 1  # no point retrying -- every remaining call hits the same wall


def test_scene_image_falls_back_to_gradient_instead_of_crashing(tmp_path):
    # A scene image failing shouldn't crash the whole run the way the
    # reference portrait failing does -- one gradient scene out of ~15
    # beats losing the entire video.
    def always_fails(prompt, seed=None, reference_image_bytes=None, resolution=None):
        raise TimeoutError("read timed out")

    out_path = tmp_path / "scene.png"
    with patch("pipeline.manifestation_video._call_cloudflare", side_effect=always_fails), \
         patch("pipeline.manifestation_video.time.sleep"), \
         patch("pipeline.manifestation_video.make_gradient_image") as mock_gradient:
        result = mv.generate_scene_image("a scene", b"ref-bytes", out_path)

    assert result == out_path
    mock_gradient.assert_called_once_with(out_path, mv.RESOLUTION)


def test_synthesize_song_retries_transient_failure_then_succeeds(tmp_path):
    # Real production bug (2026-11-12, three separate runs in one day):
    # ACE-Step's Space flaked (RepositoryNotFoundError, then a 403
    # fetching the generated file) even though the Space was confirmed
    # RUNNING and reachable moments later -- transient HF-side congestion,
    # not a quota signal, so a short retry should just work.
    calls = {"n": 0}

    def fake_client(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("RepositoryNotFoundError")
        mock_client = MagicMock()
        mock_client.predict.return_value = ["/tmp/fake-song.mp3"]
        return mock_client

    with patch("gradio_client.Client", side_effect=fake_client), \
         patch("pipeline.manifestation_video.run_ffmpeg"), \
         patch("pipeline.manifestation_video.time.sleep"):
        out = mv.synthesize_song("la la la", "pop, upbeat", 30.0, tmp_path / "song.mp3")

    assert calls["n"] == 2
    assert out == tmp_path / "song.mp3"


def test_synthesize_song_raises_ace_step_unavailable_after_exhausting_retries(tmp_path):
    with patch("gradio_client.Client", side_effect=RuntimeError("RepositoryNotFoundError")), \
         patch("pipeline.manifestation_video.time.sleep"):
        with pytest.raises(mv.ACEStepUnavailable):
            mv.synthesize_song("la la la", "pop, upbeat", 30.0, tmp_path / "song.mp3")


def test_synthesize_song_quota_exhaustion_is_not_retried(tmp_path):
    attempts = {"n": 0}

    def always_quota_exhausted(*args, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("ZeroGPU quota exceeded for today")

    with patch("gradio_client.Client", side_effect=always_quota_exhausted), \
         patch("pipeline.manifestation_video.time.sleep"):
        with pytest.raises(mv.ZeroGPUQuotaExhausted):
            mv.synthesize_song("la la la", "pop, upbeat", 30.0, tmp_path / "song.mp3")
    assert attempts["n"] == 1  # no point retrying -- the daily budget is gone either way


def test_character_reference_propagates_instead_of_falling_back(tmp_path):
    # Unlike a scene image, the reference portrait has no safe degraded
    # option -- every scene's image-to-image conditioning depends on it,
    # so a gradient "reference" would degrade every scene, not just one.
    def always_fails(prompt, seed=None, reference_image_bytes=None, resolution=None):
        raise TimeoutError("read timed out")

    with patch("pipeline.manifestation_video._call_cloudflare", side_effect=always_fails), \
         patch("pipeline.manifestation_video.time.sleep"):
        with pytest.raises(CloudflareUnavailable):
            mv.generate_character_reference("a person", tmp_path / "ref.png")
