import subprocess

import pytest

from pipeline.ffmpeg_utils import probe_duration
from pipeline.scripts import validate
from pipeline.shorts import _select_within_budget, assemble_short
from pipeline.textcard import make_gradient_image
from pipeline.tts import SceneAudio, WordTiming

SAMPLE = {
    "id": "shorts-test",
    "title": "Shorts Test",
    "description": "test",
    "tags": [],
    "scenes": [
        {"narration": "hook", "on_screen_text": "HOOK", "duration_hint": 8, "short_worthy": True},
        {"narration": "long explainer beat one", "on_screen_text": "BEAT ONE", "duration_hint": 60, "short_worthy": False},
        {"narration": "quick punchy tip", "on_screen_text": "QUICK TIP", "duration_hint": 18, "short_worthy": True},
        {"narration": "another long explainer beat", "on_screen_text": "BEAT TWO", "duration_hint": 60, "short_worthy": False},
        {"narration": "outro and cta", "on_screen_text": "SUBSCRIBE", "duration_hint": 8, "short_worthy": True},
    ],
}


def make_fake_scene_audio(index, duration):
    return SceneAudio(
        scene_index=index, audio_path=None, timings_path=None, duration=duration,
        word_timings=[WordTiming(word="x", start=0, end=min(1, duration))],
    )


def test_short_scene_indices_selects_hook_flagged_and_outro():
    script = validate(SAMPLE)
    assert script.short_scene_indices == [0, 2, 4]


def test_select_within_budget_keeps_hook_and_outro_drops_longest_middle():
    script = validate(SAMPLE)
    scene_audios = [make_fake_scene_audio(i, sc.duration_hint) for i, sc in enumerate(script.scenes)]
    # indices [0,2,4] durations [8,18,8] = 34s, well under budget, nothing dropped
    selected = _select_within_budget(script.short_scene_indices, scene_audios, max_seconds=58)
    assert selected == [0, 2, 4]


def test_select_within_budget_trims_when_over_cap():
    # hook(8) + two flagged middles (30 each) + outro(8) = 76s > 58 cap
    audios = [
        make_fake_scene_audio(0, 8),
        make_fake_scene_audio(1, 30),
        make_fake_scene_audio(2, 30),
        make_fake_scene_audio(3, 8),
    ]
    indices = [0, 1, 2, 3]
    selected = _select_within_budget(indices, audios, max_seconds=58)
    assert 0 in selected and 3 in selected  # hook and outro always kept
    assert sum(audios[i].duration for i in selected) <= 58


def make_silent_audio(path, duration):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo", "-t", str(duration),
         "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def probe_resolution(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def test_assemble_short_end_to_end(tmp_path):
    script = validate(SAMPLE)
    scene_audios = []
    for i, sc in enumerate(script.scenes):
        dur = min(sc.duration_hint, 3.0)  # keep test fast regardless of authored duration_hint
        audio_path = make_silent_audio(tmp_path / f"audio_{i}.aac", dur)
        scene_audios.append(SceneAudio(
            scene_index=i, audio_path=audio_path, timings_path=tmp_path / f"t{i}.json",
            duration=dur, word_timings=[WordTiming(word="tip", start=0, end=min(1, dur))],
        ))

    scene_images = [
        make_gradient_image(tmp_path / f"img_{i}.png", (1080, 1920))
        for i in range(len(script.scenes))
    ]

    out = assemble_short(
        script, scene_audios, scene_images,
        work_dir=tmp_path / "work", out_path=tmp_path / "short.mp4",
    )

    assert out.exists()
    # 3 selected scenes (hook, quick tip, outro) at 3s each = 9s
    assert probe_duration(out) == pytest.approx(9.0, abs=0.5)
    assert probe_resolution(out) == (1080, 1920)


def test_assemble_short_regenerates_orbs_for_fallback_scenes(tmp_path):
    script = validate(SAMPLE)
    scene_audios = []
    for i, sc in enumerate(script.scenes):
        dur = min(sc.duration_hint, 3.0)
        audio_path = make_silent_audio(tmp_path / f"audio2_{i}.aac", dur)
        scene_audios.append(SceneAudio(
            scene_index=i, audio_path=audio_path, timings_path=tmp_path / f"t2_{i}.json",
            duration=dur, word_timings=[WordTiming(word="tip", start=0, end=min(1, dur))],
        ))
    scene_images = [
        make_gradient_image(tmp_path / f"img2_{i}.png", (1080, 1920))
        for i in range(len(script.scenes))
    ]
    scene_used_fallback = [True] + [False] * (len(script.scenes) - 1)  # hook fell back, rest didn't

    out = assemble_short(
        script, scene_audios, scene_images,
        work_dir=tmp_path / "work2", out_path=tmp_path / "short2.mp4",
        scene_used_fallback=scene_used_fallback,
    )

    assert out.exists()
    assert probe_resolution(out) == (1080, 1920)
