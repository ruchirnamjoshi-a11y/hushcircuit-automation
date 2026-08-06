"""Stage 4: long-form (16:9) video assembly — concat scenes, burn captions, mix music."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from pipeline.broll import BrollClip
from pipeline.config import LONG_FORM_RESOLUTION, MUSIC_DIR, MUSIC_VOLUME_DB
from pipeline.ffmpeg_utils import (
    build_ass_captions,
    build_scene_clip,
    concat_clips,
    group_words_into_captions,
    mix_final,
)
from pipeline.tts import SceneAudio


def pick_music_track(music_dir: Path = MUSIC_DIR) -> Optional[Path]:
    tracks = [p for p in music_dir.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a")]
    return random.choice(tracks) if tracks else None


def assemble_long_form(
    scene_audios: list[SceneAudio],
    broll_clips: list[BrollClip],
    work_dir: Path,
    out_path: Path,
    music_path: Optional[Path] = None,
) -> Path:
    if len(scene_audios) != len(broll_clips):
        raise ValueError("scene_audios and broll_clips must be the same length")

    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = []
    all_words: list[tuple[str, float, float]] = []
    cumulative = 0.0

    for i, (scene_audio, broll) in enumerate(zip(scene_audios, broll_clips)):
        clip_path = work_dir / f"scene_{i:02d}.mp4"
        build_scene_clip(broll.path, scene_audio.audio_path, clip_path, LONG_FORM_RESOLUTION)
        clip_paths.append(clip_path)
        for w in scene_audio.word_timings:
            all_words.append((w.word, w.start + cumulative, w.end + cumulative))
        cumulative += scene_audio.duration

    combined_path = work_dir / "combined.mp4"
    concat_clips(clip_paths, combined_path)

    caption_lines = group_words_into_captions(all_words, max_words=4)
    ass_path = work_dir / "captions.ass"
    build_ass_captions(caption_lines, ass_path, LONG_FORM_RESOLUTION, font_size=64, margin_v=100)

    mix_final(
        combined_path.resolve(),
        out_path.resolve(),
        music_path=music_path.resolve() if music_path else None,
        captions_ass_path=ass_path,
        music_volume_db=MUSIC_VOLUME_DB,
    )
    return out_path
