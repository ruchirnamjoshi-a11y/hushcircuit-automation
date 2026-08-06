import subprocess
from pathlib import Path

import pytest

from pipeline.assemble import assemble_long_form
from pipeline.broll import BrollClip
from pipeline.ffmpeg_utils import (
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
    words = [(f"w{i}", float(i), float(i) + 0.5) for i in range(10)]
    chunks = group_words_into_captions(words, max_words=4)
    assert len(chunks) == 3
    text0, start0, end0 = chunks[0]
    assert text0 == "w0 w1 w2 w3"
    assert start0 == 0.0
    assert end0 == 3.5


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


def test_build_ass_captions_writes_valid_file(tmp_path):
    lines = [("hello world", 0.0, 1.0), ("second line", 1.0, 2.5)]
    out = build_ass_captions(lines, tmp_path / "captions.ass", resolution=(1920, 1080))
    content = out.read_text()
    assert "[Script Info]" in content
    assert "hello world" in content
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Default,hello world" in content


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

    ass = build_ass_captions([("test caption", 0.0, 1.0)], tmp_path / "captions.ass", resolution=(640, 360))

    out = mix_final(scene, tmp_path / "final.mp4", music_path=music, captions_ass_path=ass)
    assert out.exists()
    assert probe_duration(out) == pytest.approx(3.0, abs=0.5)


# ---- full pipeline integration, synthetic inputs ----

def test_assemble_long_form_end_to_end(tmp_path):
    scene_audios = []
    broll_clips = []
    for i, (color, dur) in enumerate([("blue", 3.0), ("green", 4.0)]):
        audio_path = make_silent_audio(tmp_path / f"audio_{i}.aac", dur)
        word_timings = [
            WordTiming(word=f"word{j}", start=j * 0.5, end=j * 0.5 + 0.4)
            for j in range(4)
        ]
        scene_audios.append(SceneAudio(
            scene_index=i, audio_path=audio_path, timings_path=tmp_path / f"t{i}.json",
            duration=dur, word_timings=word_timings,
        ))
        broll_path = make_color_clip(tmp_path / f"broll_{i}.mp4", color, dur)
        broll_clips.append(BrollClip(path=broll_path, duration=dur, source="test"))

    out = assemble_long_form(
        scene_audios, broll_clips,
        work_dir=tmp_path / "work",
        out_path=tmp_path / "final.mp4",
    )

    assert out.exists()
    assert probe_duration(out) == pytest.approx(7.0, abs=0.5)
    assert probe_resolution(out) == (1920, 1080)
