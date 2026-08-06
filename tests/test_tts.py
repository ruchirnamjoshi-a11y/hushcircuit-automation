from pipeline.scripts import validate
from pipeline.tts import synthesize_scene, synthesize_script

SAMPLE_SCRIPT = {
    "id": "tts-test",
    "title": "TTS Test",
    "description": "test",
    "tags": [],
    "scenes": [
        {"narration": "This is the hook line for testing.", "visual_keyword": "test", "duration_hint": 4.0, "short_worthy": True},
        {"narration": "This is a short body scene to verify synthesis works end to end.", "visual_keyword": "test", "duration_hint": 6.0, "short_worthy": False},
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


def test_synthesize_script_produces_one_file_per_scene(tmp_path):
    script = validate(SAMPLE_SCRIPT)
    results = synthesize_script(script, tmp_path)

    assert len(results) == len(script.scenes)
    for i, scene_audio in enumerate(results):
        assert scene_audio.scene_index == i
        assert scene_audio.audio_path.exists()
        assert scene_audio.duration > 0
