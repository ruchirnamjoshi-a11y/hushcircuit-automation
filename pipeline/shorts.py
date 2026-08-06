"""Stage 4b: auto-clip a vertical (9:16) Short from the same scene assets used
for the long-form video. No second script-gen or broll-fetch call — reuses
whichever scenes were flagged short_worthy in the script."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.broll import BrollClip
from pipeline.config import MUSIC_VOLUME_DB, SHORT_MAX_SECONDS, SHORT_RESOLUTION
from pipeline.ffmpeg_utils import (
    build_ass_captions,
    build_scene_clip,
    concat_clips,
    group_words_into_captions,
    mix_final,
)
from pipeline.scripts import Script
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
    broll_clips: list[BrollClip],
    work_dir: Path,
    out_path: Path,
    music_path: Optional[Path] = None,
    max_seconds: float = SHORT_MAX_SECONDS,
) -> Path:
    if len(scene_audios) != len(script.scenes) or len(broll_clips) != len(script.scenes):
        raise ValueError("scene_audios and broll_clips must cover every scene in the script")

    indices = _select_within_budget(script.short_scene_indices, scene_audios, max_seconds)

    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = []
    all_words: list[tuple[str, float, float]] = []
    cumulative = 0.0

    for pos, scene_i in enumerate(indices):
        scene_audio = scene_audios[scene_i]
        broll = broll_clips[scene_i]
        clip_path = work_dir / f"short_scene_{pos:02d}.mp4"
        build_scene_clip(broll.path, scene_audio.audio_path, clip_path, SHORT_RESOLUTION)
        clip_paths.append(clip_path)
        for w in scene_audio.word_timings:
            all_words.append((w.word, w.start + cumulative, w.end + cumulative))
        cumulative += scene_audio.duration

    combined_path = work_dir / "short_combined.mp4"
    concat_clips(clip_paths, combined_path)

    caption_lines = group_words_into_captions(all_words, max_words=3)
    ass_path = work_dir / "short_captions.ass"
    build_ass_captions(caption_lines, ass_path, SHORT_RESOLUTION, font_size=90, margin_v=320)

    mix_final(
        combined_path.resolve(),
        out_path.resolve(),
        music_path=music_path.resolve() if music_path else None,
        captions_ass_path=ass_path,
        music_volume_db=MUSIC_VOLUME_DB,
    )
    return out_path
