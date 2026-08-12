"""Stage 4: assemble the single vertical (9:16) Short that carries the full
story — every scene, in order. `max_seconds` is a safety ceiling (not a
highlight-reel trim target): our ~8-scene stories run ~70-90s, well under
it, so it only ever trims an unusually long script."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.config import MUSIC_VOLUME_DB, SHORT_MAX_SECONDS, SHORT_RESOLUTION
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


def _select_within_budget(
    indices: list[int],
    scene_audios: list[SceneAudio],
    max_seconds: float,
) -> list[int]:
    """Keeps the first (hook) and last (outro) selected scene always; drops
    the longest remaining middle scenes first until under the time budget."""
    if not indices:
        return indices

    selected = list(indices)
    total = sum(scene_audios[i].duration for i in selected)

    while total > max_seconds and len(selected) > 2:
        middle = selected[1:-1]
        if not middle:
            break
        longest = max(middle, key=lambda i: scene_audios[i].duration)
        selected.remove(longest)
        total = sum(scene_audios[i].duration for i in selected)

    return selected


def assemble_short(
    script: Script,
    scene_audios: list[SceneAudio],
    scene_images: list[Path],
    work_dir: Path,
    out_path: Path,
    music_path: Optional[Path] = None,
    max_seconds: float = SHORT_MAX_SECONDS,
    scene_used_fallback: Optional[list[bool]] = None,
    burn_captions: bool = True,
) -> Path:
    """scene_used_fallback (one bool per scene, from
    ai_image.generate_scene_image_raw) marks scenes that fell back to the
    brand gradient — those get regenerated with orbs rather than reusing the
    plain fallback image, so a fallback scene doesn't look flatter than a
    real AI illustration.

    burn_captions=False skips the word-pop captions and progress badge
    entirely (video + audio only) — for tracks without a matching caption
    font (e.g. non-Latin scripts), rather than rendering wrong/missing-glyph
    text on top of the video."""
    if len(scene_audios) != len(script.scenes):
        raise ValueError("scene_audios must cover every scene in the script")
    if len(scene_images) != len(script.scenes):
        raise ValueError("scene_images must cover every scene in the script")

    indices = _select_within_budget(list(range(len(script.scenes))), scene_audios, max_seconds)

    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = []
    all_words: list[tuple[str, float, float, bool]] = []
    badge_lines: list[tuple[str, float, float]] = []
    cumulative = 0.0

    for pos, scene_i in enumerate(indices):
        scene_audio = scene_audios[scene_i]

        bg_path = work_dir / f"short_bg_{pos:02d}.mp4"
        if scene_used_fallback and scene_used_fallback[scene_i]:
            generate_background_clip(SHORT_RESOLUTION, scene_audio.duration, bg_path)
        else:
            generate_image_background_clip(scene_images[scene_i], SHORT_RESOLUTION, scene_audio.duration, bg_path)

        clip_path = work_dir / f"short_scene_{pos:02d}.mp4"
        build_scene_clip(bg_path, scene_audio.audio_path, clip_path, SHORT_RESOLUTION)
        clip_paths.append(clip_path)

        for w in scene_audio.word_timings:
            all_words.append((w.word, w.start + cumulative, w.end + cumulative, w.ends_sentence))
        badge_lines.append((script.badge_text(scene_i), cumulative, cumulative + scene_audio.duration))
        cumulative += scene_audio.duration

    combined_path = work_dir / "short_combined.mp4"
    concat_clips(clip_paths, combined_path)

    ass_path = None
    if burn_captions:
        caption_lines = group_words_into_captions(all_words, max_words=2)
        ass_path = work_dir / "short_captions.ass"
        build_ass_captions(
            caption_lines, ass_path, SHORT_RESOLUTION,
            font_size=130, badge_lines=badge_lines, badge_font_size=48,
        )

    mix_final(
        combined_path.resolve(),
        out_path.resolve(),
        music_path=music_path.resolve() if music_path else None,
        captions_ass_path=ass_path,
        music_volume_db=MUSIC_VOLUME_DB,
    )
    return out_path
