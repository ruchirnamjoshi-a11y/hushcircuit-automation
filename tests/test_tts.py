import base64
import json
from unittest.mock import MagicMock, patch

import requests

from pipeline.config import Track
from pipeline.scripts import validate
from pipeline.tts import (
    GeminiTTSError,
    _call_gemini_tts,
    _pick_voice,
    _sentence_ending_word_indices,
    synthesize_scene,
    synthesize_scene_gemini,
    synthesize_script,
    synthesize_script_for_track,
)

SAMPLE_SCRIPT = {
    "id": "tts-test",
    "title": "TTS Test",
    "description": "test",
    "tags": [],
    "scenes": [
        {"narration": "This is the hook line for testing.", "on_screen_text": "TEST", "duration_hint": 4.0},
        {"narration": "This is a short body scene to verify synthesis works end to end.", "on_screen_text": "TEST", "duration_hint": 6.0},
    ],
}


def test_synthesize_scene_produces_audio_and_word_timings(tmp_path):
    result = synthesize_scene("Hello world, this is a test of the voiceover pipeline.", tmp_path, "scene_00")

    assert result.audio_path.exists()
    assert result.audio_path.stat().st_size > 0
    assert result.duration > 0

    assert result.timings_path.exists()
    assert len(result.word_timings) >= 5  # roughly one entry per word

    # timings should be monotonically non-decreasing and within the clip duration
    prev_start = -1.0
    for w in result.word_timings:
        assert w.start >= prev_start
        assert w.end >= w.start
        assert w.end <= result.duration + 1.0  # small tolerance
        prev_start = w.start


def test_sentence_ending_word_indices_finds_last_word_of_each_sentence():
    text = "This is one. This is two words longer. Three."
    # word indices (0-based): "This is one." -> 0,1,2  "This is two words longer." -> 3,4,5,6,7  "Three." -> 8
    assert _sentence_ending_word_indices(text) == {2, 7, 8}


def test_synthesize_scene_marks_ends_sentence_on_real_synthesis(tmp_path):
    # edge-tts strips punctuation from WordBoundary text entirely, so this
    # must come from the original narration, not the synthesized word text
    # (regression test for captions merging two sentences onto one line).
    result = synthesize_scene("Trick two: ask for options. Give me three ideas.", tmp_path, "scene_00")
    words = [w.word for w in result.word_timings]
    ends = [w.ends_sentence for w in result.word_timings]

    assert words[4] == "options"
    assert ends[4] is True  # "options." was the end of the first sentence
    assert not any(ends[:4])
    assert ends[-1] is True  # "ideas." ends the final sentence


def test_synthesize_script_produces_one_file_per_scene(tmp_path):
    script = validate(SAMPLE_SCRIPT)
    results = synthesize_script(script, tmp_path)

    assert len(results) == len(script.scenes)
    for i, scene_audio in enumerate(results):
        assert scene_audio.scene_index == i
        assert scene_audio.audio_path.exists()
        assert scene_audio.duration > 0


EDGE_TRACK = Track(
    key="kids", label="Kids", voice="en-GB-SoniaNeural",
    image_style_prefix="", image_style_suffix="", made_for_kids=True, category_id="24",
)
GEMINI_TRACK = Track(
    key="hindi_mythology", label="Hindi", voice="hi-IN-MadhurNeural",
    image_style_prefix="", image_style_suffix="", made_for_kids=False, category_id="24",
    tts_provider="gemini", tts_voices=["Kore", "Puck", "Aoede"],
)


def _fake_gemini_tts_response(status_code, pcm_bytes=b"", retry_delay=None):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    if status_code == 200:
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(pcm_bytes).decode()}}]}}]
        }
    else:
        details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{retry_delay}s"}] if retry_delay else []
        mock_response.json.return_value = {"error": {"code": 429, "details": details}}
        mock_response.raise_for_status.side_effect = Exception("should not be called on 429 retry path")
    return mock_response


def test_call_gemini_tts_returns_decoded_audio_bytes():
    pcm = b"\x00\x01" * 100
    mock_response = _fake_gemini_tts_response(200, pcm_bytes=pcm)
    with patch("pipeline.tts.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.tts.requests.post", return_value=mock_response):
        result = _call_gemini_tts("नमस्ते", "Kore")
    assert result == pcm


def test_call_gemini_tts_retries_on_429_then_succeeds():
    pcm = b"\x00\x01" * 100
    rate_limited = _fake_gemini_tts_response(429, retry_delay=12)
    success = _fake_gemini_tts_response(200, pcm_bytes=pcm)
    with patch("pipeline.tts.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.tts.requests.post", side_effect=[rate_limited, success]), \
         patch("pipeline.tts.time.sleep") as mock_sleep:
        result = _call_gemini_tts("नमस्ते", "Kore")
    assert result == pcm
    mock_sleep.assert_called_once_with(16.0)  # floored to min 15s (reported 12s was too low) + 1s margin


def test_call_gemini_tts_respects_a_longer_reported_delay_over_the_floor():
    pcm = b"\x00\x01" * 100
    rate_limited = _fake_gemini_tts_response(429, retry_delay=45)
    success = _fake_gemini_tts_response(200, pcm_bytes=pcm)
    with patch("pipeline.tts.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.tts.requests.post", side_effect=[rate_limited, success]), \
         patch("pipeline.tts.time.sleep") as mock_sleep:
        result = _call_gemini_tts("नमस्ते", "Kore")
    assert result == pcm
    mock_sleep.assert_called_once_with(46.0)  # reported delay (45s) already above the floor


def test_call_gemini_tts_retries_on_network_timeout_then_succeeds():
    # regression test: a real production run hit a bare ReadTimeout with no
    # retry at all before this path was added
    pcm = b"\x00\x01" * 100
    success = _fake_gemini_tts_response(200, pcm_bytes=pcm)
    with patch("pipeline.tts.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.tts.requests.post", side_effect=[requests.exceptions.ReadTimeout("timed out"), success]), \
         patch("pipeline.tts.time.sleep") as mock_sleep:
        result = _call_gemini_tts("नमस्ते", "Kore")
    assert result == pcm
    mock_sleep.assert_called_once()


def test_call_gemini_tts_raises_after_repeated_network_timeouts():
    from pipeline.tts import GEMINI_TTS_MAX_RETRIES
    with patch("pipeline.tts.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.tts.requests.post", side_effect=requests.exceptions.ReadTimeout("timed out")), \
         patch("pipeline.tts.time.sleep"):
        try:
            _call_gemini_tts("नमस्ते", "Kore")
            assert False, "expected GeminiTTSError"
        except GeminiTTSError:
            pass


def test_call_gemini_tts_raises_after_max_retries():
    from pipeline.tts import GEMINI_TTS_MAX_RETRIES
    always_limited = _fake_gemini_tts_response(429, retry_delay=1)
    with patch("pipeline.tts.GEMINI_API_KEY", "fake-key"), \
         patch("pipeline.tts.requests.post", return_value=always_limited), \
         patch("pipeline.tts.time.sleep"):
        try:
            _call_gemini_tts("नमस्ते", "Kore")
            assert False, "expected GeminiTTSError"
        except GeminiTTSError:
            pass


def test_synthesize_scene_gemini_produces_audio_with_empty_word_timings(tmp_path):
    silence_pcm = b"\x00\x00" * 4800  # 0.2s of silence at 24kHz mono s16le
    with patch("pipeline.tts._call_gemini_tts", return_value=silence_pcm):
        result = synthesize_scene_gemini("नमस्ते दुनिया", tmp_path, "scene_00", "Kore")

    assert result.audio_path.exists()
    assert result.audio_path.stat().st_size > 0
    assert result.duration > 0
    assert result.word_timings == []
    assert json.loads(result.timings_path.read_text()) == []


def test_pick_voice_is_deterministic_and_from_the_configured_pool():
    script_a = validate({**SAMPLE_SCRIPT, "id": "story-a"})
    script_b = validate({**SAMPLE_SCRIPT, "id": "story-b"})

    voice_a1 = _pick_voice(GEMINI_TRACK, script_a)
    voice_a2 = _pick_voice(GEMINI_TRACK, script_a)
    assert voice_a1 == voice_a2  # stable across calls for the same script
    assert voice_a1 in GEMINI_TRACK.tts_voices

    # not asserting a1 != b1 (could coincidentally collide), just that both are valid
    assert _pick_voice(GEMINI_TRACK, script_b) in GEMINI_TRACK.tts_voices


def test_pick_voice_falls_back_to_track_voice_when_no_pool_configured():
    script = validate(SAMPLE_SCRIPT)
    assert _pick_voice(EDGE_TRACK, script) == EDGE_TRACK.voice


def test_synthesize_script_for_track_dispatches_to_gemini_when_configured(tmp_path):
    script = validate(SAMPLE_SCRIPT)
    with patch("pipeline.tts.synthesize_scene_gemini") as mock_gemini, \
         patch("pipeline.tts.synthesize_scene") as mock_edge:
        mock_gemini.return_value = MagicMock(scene_index=-1)
        synthesize_script_for_track(script, tmp_path, GEMINI_TRACK)

    assert mock_gemini.call_count == len(script.scenes)
    mock_edge.assert_not_called()
    expected_voice = _pick_voice(GEMINI_TRACK, script)
    for call in mock_gemini.call_args_list:
        assert call.args[3] == expected_voice


def test_synthesize_script_for_track_dispatches_to_edge_by_default(tmp_path):
    script = validate(SAMPLE_SCRIPT)
    with patch("pipeline.tts.synthesize_scene_gemini") as mock_gemini, \
         patch("pipeline.tts.synthesize_scene") as mock_edge:
        mock_edge.return_value = MagicMock(scene_index=-1)
        synthesize_script_for_track(script, tmp_path, EDGE_TRACK)

    assert mock_edge.call_count == len(script.scenes)
    mock_gemini.assert_not_called()
    for call in mock_edge.call_args_list:
        assert call.args[3] == EDGE_TRACK.voice
