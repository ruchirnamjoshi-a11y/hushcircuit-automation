"""Stage 4: long-form (16:9) video assembly — concat scenes, burn captions, mix music."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from pipeline.config import LONG_FORM_RESOLUTION, MUSIC_DIR, MUSIC_VOLUME_DB
from pipeline.ffmpeg_utils import (
    build_ass_captions,
    build_scene_clip,
    concat_clips,
    group_words_into_captions,
    mix_final,
)
from pipeline.scripts import Script
from pipeline.textcard import generate_background_clip, generate_image_background_clip
from pipeline.tts import SceneAudio


def pick_music_track(music_dir: Path = MUSIC_DIR) -> Optional[Path]:
    tracks = [p for p in music_dir.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a")]
    return random.choice(tracks) if tracks else None


def assemble_long_form(
    script: Script,
    scene_audios: list[SceneAudio],
    scene_images: list[Path],
    work_dir: Path,
    out_path: Path,
    music_path: Optional[Path] = None,
    scene_used_fallback: Optional[list[bool]] = None,
    font_name: str = "Arial",
) -> Path:
    """scene_used_fallback (one bool per scene, from
    ai_image.generate_scene_image_raw) marks scenes that fell back to the
    brand gradient — those regenerate a fresh gradient+orbs clip instead of
    zooming scene_images[i] plain, so a fallback scene doesn't look flatter
    than a real AI illustration or the original all-gradient design.

    font_name: pass a font with the right glyphs for non-Latin-script
    narration (see build_ass_captions)."""
    if len(scene_audios) != len(script.scenes):
        raise ValueError("scene_audios must cover every scene in the script")
    if len(scene_images) != len(script.scenes):
        raise ValueError("scene_images must cover every scene in the script")

    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = []
    all_words: list[tuple[str, float, float, bool]] = []
    cumulative = 0.0

    for i, scene_audio in enumerate(scene_audios):
        bg_path = work_dir / f"bg_{i:02d}.mp4"
        if scene_used_fallback and scene_used_fallback[i]:
            generate_background_clip(LONG_FORM_RESOLUTION, scene_audio.duration, bg_path)
        else:
            generate_image_background_clip(scene_images[i], LONG_FORM_RESOLUTION, scene_audio.duration, bg_path)

        clip_path = work_dir / f"scene_{i:02d}.mp4"
        build_scene_clip(bg_path, scene_audio.audio_path, clip_path, LONG_FORM_RESOLUTION)
        clip_paths.append(clip_path)

        for w in scene_audio.word_timings:
            all_words.append((w.word, w.start + cumulative, w.end + cumulative, w.ends_sentence))
        cumulative += scene_audio.duration

    combined_path = work_dir / "combined.mp4"
    concat_clips(clip_paths, combined_path)

    caption_lines = group_words_into_captions(all_words, max_words=2)
    ass_path = work_dir / "captions.ass"
    build_ass_captions(caption_lines, ass_path, LONG_FORM_RESOLUTION, font_size=100, font_name=font_name)

    mix_final(
        combined_path.resolve(),
        out_path.resolve(),
        music_path=music_path.resolve() if music_path else None,
        captions_ass_path=ass_path,
        music_volume_db=MUSIC_VOLUME_DB,
    )
    return out_path
