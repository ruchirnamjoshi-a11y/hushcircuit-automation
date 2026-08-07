import subprocess
from pathlib import Path

import pytest

from pipeline.assemble import assemble_long_form
from pipeline.scripts import validate
from pipeline.textcard import make_gradient_image
from pipeline.ffmpeg_utils import (
    ACCENT_INLINE_ASS,
    WHITE_INLINE_ASS,
    build_ass_captions,
    build_scene_clip,
    concat_clips,
    group_words_into_captions,
    mix_final,
    probe_duration,
    seconds_to_ass_timestamp,
)
from pipeline.tts import SceneAudio, WordTiming


def make_color_clip(path: Path, color: str, duration: float, size: str = "640x360") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s={size}:d={duration}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def make_silent_audio(path: Path, duration: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo", "-t", str(duration),
         "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def probe_resolution(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


# ---- pure-python helpers, no ffmpeg ----

def test_seconds_to_ass_timestamp():
    assert seconds_to_ass_timestamp(0) == "0:00:00.00"
    assert seconds_to_ass_timestamp(65.5) == "0:01:05.50"
    assert seconds_to_ass_timestamp(3661.25) == "1:01:01.25"


def test_group_words_into_captions_chunks_by_max_words():
    words = [(f"word{chr(97 + i)}", float(i), float(i) + 0.5, False) for i in range(10)]
    chunks = group_words_into_captions(words, max_words=4)
    assert len(chunks) == 3
    text0, start0, end0, emphasize0 = chunks[0]
    assert text0 == "worda wordb wordc wordd"
    assert start0 == 0.0
    assert end0 == 3.5
    assert emphasize0 is False


def test_group_words_into_captions_breaks_early_at_sentence_end():
    # "end." after 2 words should close the chunk even though max_words=4
    words = [
        ("This", 0.0, 0.2, False),
        ("sentence.", 0.2, 0.5, True),
        ("Next", 0.5, 0.7, False),
        ("one.", 0.7, 0.9, True),
    ]
    chunks = group_words_into_captions(words, max_words=4)
    assert [text for text, _, _, _ in chunks] == ["This sentence.", "Next one."]


def test_group_words_into_captions_flags_emphasis_words():
    words = [
        ("check", 0.0, 0.2, False),
        ("chatgpt", 0.2, 0.4, False),
        ("out.", 0.4, 0.6, True),
        ("five", 0.6, 0.8, False),
        ("tricks.", 0.8, 1.0, True),
    ]
    chunks = group_words_into_captions(words, max_words=3)
    texts_and_emphasis = [(text, emphasize) for text, _, _, emphasize in chunks]
    assert texts_and_emphasis == [
        ("check chatgpt out.", True),   # "chatgpt" triggers emphasis
        ("five tricks.", False),         # spelled-out numbers don't trigger it
    ]


# ---- ffmpeg-backed unit tests (synthetic lavfi inputs, no network) ----

def test_build_scene_clip_loops_short_broll_to_match_audio(tmp_path):
    broll = make_color_clip(tmp_path / "broll.mp4", "blue", duration=1.0)
    audio = make_silent_audio(tmp_path / "audio.aac", duration=4.0)

    out = build_scene_clip(broll, audio, tmp_path / "scene.mp4", resolution=(640, 360))

    assert out.exists()
    assert probe_duration(out) == pytest.approx(4.0, abs=0.3)
    assert probe_resolution(out) == (640, 360)


def test_build_scene_clip_trims_long_broll_to_match_audio(tmp_path):
    broll = make_color_clip(tmp_path / "broll.mp4", "red", duration=8.0)
    audio = make_silent_audio(tmp_path / "audio.aac", duration=2.0)

    out = build_scene_clip(broll, audio, tmp_path / "scene.mp4", resolution=(640, 360))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.3)


def test_concat_clips_sums_durations(tmp_path):
    clip1 = tmp_path / "c1.mp4"
    clip2 = tmp_path / "c2.mp4"
    for path, color, dur in [(clip1, "blue", 2.0), (clip2, "green", 3.0)]:
        broll = make_color_clip(tmp_path / f"broll_{color}.mp4", color, dur)
        audio = make_silent_audio(tmp_path / f"audio_{color}.aac", dur)
        build_scene_clip(broll, audio, path, resolution=(640, 360))

    out = concat_clips([clip1, clip2], tmp_path / "combined.mp4")
    assert probe_duration(out) == pytest.approx(5.0, abs=0.5)


def test_build_ass_captions_writes_valid_file_with_pop_animation(tmp_path):
    lines = [("hello world", 0.0, 1.0, False), ("second line", 1.0, 2.5, False)]
    out = build_ass_captions(lines, tmp_path / "captions.ass", resolution=(1920, 1080))
    content = out.read_text()
    assert "[Script Info]" in content
    assert "Style: Caption," in content
    assert "hello world" in content
    # pop-in scale/fade tags and default white color should be present
    assert r"\fad(60,0)" in content
    assert f"\\c{WHITE_INLINE_ASS}" in content
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Caption," in content


def test_build_ass_captions_colors_emphasized_words_with_accent(tmp_path):
    lines = [("regular text", 0.0, 1.0, False), ("chatgpt tip", 1.0, 2.0, True)]
    out = build_ass_captions(lines, tmp_path / "captions.ass", resolution=(1920, 1080))
    content = out.read_text()
    lines_out = content.splitlines()
    regular_line = next(l for l in lines_out if "regular text" in l)
    emphasized_line = next(l for l in lines_out if "chatgpt tip" in l)
    assert f"\\c{WHITE_INLINE_ASS}" in regular_line
    assert f"\\c{ACCENT_INLINE_ASS}" in emphasized_line


def test_build_ass_captions_includes_badge_style_and_uppercases_text(tmp_path):
    caption_lines = [("hello world", 0.0, 1.0, False)]
    badge_lines = [("give it a role", 0.0, 8.0)]
    out = build_ass_captions(
        caption_lines, tmp_path / "captions.ass", resolution=(1920, 1080),
        badge_lines=badge_lines,
    )
    content = out.read_text()
    assert "Style: Badge," in content
    assert "GIVE IT A ROLE" in content
    assert "Dialogue: 0,0:00:00.00,0:00:08.00,Badge," in content
    # flowing captions stay on the Caption style, untouched
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Caption," in content


def test_build_ass_captions_works_without_badges(tmp_path):
    out = build_ass_captions([("just captions", 0.0, 1.0, False)], tmp_path / "captions.ass", resolution=(1920, 1080))
    content = out.read_text()
    assert "just captions" in content
    assert "Style: Badge," in content  # style is always declared, just unused


def test_mix_final_burns_captions_and_mixes_music(tmp_path):
    broll = make_color_clip(tmp_path / "broll.mp4", "yellow", duration=3.0)
    audio = make_silent_audio(tmp_path / "audio.aac", duration=3.0)
    scene = build_scene_clip(broll, audio, tmp_path / "scene.mp4", resolution=(640, 360))

    music = tmp_path / "music.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=220:duration=1", str(music)],
        check=True,
    )

    ass = build_ass_captions([("test caption", 0.0, 1.0, False)], tmp_path / "captions.ass", resolution=(640, 360))

    out = mix_final(scene, tmp_path / "final.mp4", music_path=music, captions_ass_path=ass)
    assert out.exists()
    assert probe_duration(out) == pytest.approx(3.0, abs=0.5)


# ---- full pipeline integration, synthetic inputs ----

SAMPLE_SCRIPT = {
    "id": "assemble-test",
    "title": "Assemble Test",
    "description": "test",
    "tags": [],
    "scenes": [
        {"narration": "hook", "on_screen_text": "HOOK", "duration_hint": 3, "short_worthy": True},
        {"narration": "outro", "on_screen_text": "OUTRO", "duration_hint": 4, "short_worthy": True},
    ],
}


def test_assemble_long_form_end_to_end(tmp_path):
    script = validate(SAMPLE_SCRIPT)
    scene_audios = []
    for i, dur in enumerate([3.0, 4.0]):
        audio_path = make_silent_audio(tmp_path / f"audio_{i}.aac", dur)
        word_timings = [
            WordTiming(word=f"word{j}", start=j * 0.5, end=j * 0.5 + 0.4)
            for j in range(4)
        ]
        scene_audios.append(SceneAudio(
            scene_index=i, audio_path=audio_path, timings_path=tmp_path / f"t{i}.json",
            duration=dur, word_timings=word_timings,
        ))

    scene_images = [
        make_gradient_image(tmp_path / f"img_{i}.png", (1920, 1080))
        for i in range(len(script.scenes))
    ]

    out = assemble_long_form(
        script, scene_audios, scene_images,
        work_dir=tmp_path / "work",
        out_path=tmp_path / "final.mp4",
    )

    assert out.exists()
    assert probe_duration(out) == pytest.approx(7.0, abs=0.5)
    assert probe_resolution(out) == (1920, 1080)


def test_assemble_long_form_regenerates_orbs_for_fallback_scenes(tmp_path):
    # scene_used_fallback marks which scenes fell back to the gradient —
    # those should render fine via generate_background_clip (with orbs)
    # rather than zooming scene_images[i] plain.
    script = validate(SAMPLE_SCRIPT)
    scene_audios = []
    for i, dur in enumerate([3.0, 4.0]):
        audio_path = make_silent_audio(tmp_path / f"audio_{i}.aac", dur)
        scene_audios.append(SceneAudio(
            scene_index=i, audio_path=audio_path, timings_path=tmp_path / f"t{i}.json",
            duration=dur, word_timings=[WordTiming(word="hi", start=0, end=min(1, dur))],
        ))
    scene_images = [
        make_gradient_image(tmp_path / f"img_{i}.png", (1920, 1080))
        for i in range(len(script.scenes))
    ]

    out = assemble_long_form(
        script, scene_audios, scene_images,
        work_dir=tmp_path / "work2",
        out_path=tmp_path / "final2.mp4",
        scene_used_fallback=[True, False],
    )

    assert out.exists()
    assert probe_duration(out) == pytest.approx(7.0, abs=0.5)
