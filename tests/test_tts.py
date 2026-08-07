from pipeline.scripts import validate
from pipeline.tts import _sentence_ending_word_indices, synthesize_scene, synthesize_script

SAMPLE_SCRIPT = {
    "id": "tts-test",
    "title": "TTS Test",
    "description": "test",
    "tags": [],
    "scenes": [
        {"narration": "This is the hook line for testing.", "on_screen_text": "TEST", "duration_hint": 4.0, "short_worthy": True},
        {"narration": "This is a short body scene to verify synthesis works end to end.", "on_screen_text": "TEST", "duration_hint": 6.0, "short_worthy": False},
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
