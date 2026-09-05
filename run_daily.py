#!/usr/bin/env python3
"""Daily orchestrator: for each story track (kids/teens/adults/women/
hindi_mythology/hero_saga), consumes that track's next queued script and
produces + uploads its video(s). Tracks with Track.produce_long_form=True
(currently kids + hindi_mythology) get TWO uploads per day — the full
long-form story and a trimmed Shorts highlight cut — built from the SAME
generated scene images/audio, at no extra image-generation or TTS cost.

A track with Track.shares_images_with set (hindi_mythology -> "kids") is a
language variant of another track's stories: instead of generating its own
scene illustrations, it reuses the primary track's already-generated raw
images from THIS SAME run (matched via Script.source_script_id), so a
second language costs zero extra Cloudflare image-generation quota — only
its own narration/captions/upload are per-language work. If the primary
track hasn't produced its paired script yet this run (e.g. it already
produced today's video in an earlier scheduled run, or its queue is out of
sync), the variant track falls back to generating its own images
independently rather than stalling.

On success per track, moves the consumed script from
scripts_queue/pending/<track>/ to scripts_queue/used/<track>/.

This is meant to run several times a day (see .github/workflows/
daily-video.yml), not just once:

- Before doing any other real work for a track that generates its own
  images (i.e. not reusing a sibling's this run), a dedicated reference
  portrait is generated as a real-work "probe" (see
  ai_image.REFERENCE_PORTRAIT_SCENE). If Cloudflare's free image quota is
  exhausted, that raises CloudflareQuotaExhausted instead of silently
  falling back to the gradient — we'd rather abort and retry later than
  publish a video with no matching artwork. The whole run stops there
  (quota is account-wide, not per-track, so every other track would hit the
  same wall) and every track's script stays in pending/ for the next
  scheduled run to retry.
- pipeline/state.py tracks which tracks already produced a video today, so
  a later same-day run — after quota resets — doesn't produce a *second*
  video for a track that already succeeded earlier.

An empty queue for a given track is not an error — that track is skipped and
the others still run. If a stage raises for a track for any other reason,
that script is left in pending/ so the next run retries it, that track's
error is logged, and the other tracks still get a chance to run; the process
exits non-zero at the end if any track had a real failure (GitHub Actions
surfaces that as a failed run) so a silent per-track failure doesn't go
unnoticed. Quota-exhaustion aborts are expected/handled, not failures, and
don't affect the exit code.
"""

from __future__ import annotations

import argparse
import sys
import traceback
import zlib
from pathlib import Path
from typing import Optional

from pipeline.ai_image import (
    REFERENCE_PORTRAIT_SCENE,
    CloudflareQuotaExhausted,
    fit_scene_image,
    generate_scene_image_raw,
)
from pipeline.assemble import assemble_long_form, pick_music_track
from pipeline.canvas_video import render_piece_video, synthesize_narration_lines
from pipeline.config import (
    LONG_FORM_RESOLUTION,
    MATH_PIECES_DIR,
    OUTPUT_DIR,
    QUEUE_PENDING_DIR,
    QUEUE_USED_DIR,
    SHORT_RESOLUTION,
    TRACKS,
    Track,
    youtube_token_path,
)
from pipeline.manifestation_lyrics import GeminiUnavailable
from pipeline.manifestation_video import ZeroGPUQuotaExhausted
from pipeline.math_scripts import load_next_pending as load_next_pending_math
from pipeline.scripts import Script, load_next_pending, mark_used
from pipeline.shorts import assemble_short
from pipeline.state import already_produced_today, mark_produced_today
from pipeline.thumbnail import generate_thumbnail
from pipeline.tts import synthesize_script_for_track
from pipeline.upload import upload_daily_video

# Populated by a track's own (non-reused) image generation, consulted by any
# later-processed track whose Track.shares_images_with points at it —
# key: (track_key, script_id) -> (images_raw_dir, scene_used_fallback).
# Scoped to a single run() call: an ephemeral CI runner's output/ dir
# doesn't persist between separately scheduled runs, so reuse only works
# when both the primary and variant scripts are due in the same run.
ProducedImages = dict[tuple[str, str], tuple[Path, list[bool]]]


def _story_seed(seed_key: str) -> int:
    """A stable (not Python's randomized-per-process hash()) seed derived
    from seed_key, reused for every scene's Cloudflare call within a story.
    Combined with a fixed reference-portrait image (see
    ai_image.REFERENCE_PORTRAIT_SCENE), this keeps a character visually
    recognizable across independently generated scenes."""
    return zlib.crc32(seed_key.encode())


def _generate_story_images(
    script: Script, track: Track, seed: int, images_raw_dir: Path,
    resolution: tuple[int, int] = (720, 1280),
) -> tuple[list[Path], list[bool]]:
    """Generates a fixed reference portrait, then every scene's illustration
    conditioned on it. The portrait generation is the real-work quota probe
    (raises CloudflareQuotaExhausted if the daily free allocation is
    exhausted) — done before any other work so nothing's wasted if it
    fails.

    `resolution` defaults to (720, 1280) — the Short's 9:16 (see
    run_track, which crops the SAME raw images to both 9:16 and 16:9 to
    share one Cloudflare call across formats). run_long_form_track passes
    (1280, 720) instead: its own independent script has no format to
    share images with, so generating natively in 16:9 avoids the
    inconsistent, sometimes badly-cropped compositions that came from
    center-cropping a 9:16 source down to widescreen."""
    portrait_path, portrait_fallback = generate_scene_image_raw(
        REFERENCE_PORTRAIT_SCENE, images_raw_dir / "reference_portrait.png", track,
        character_reference=script.character_reference, seed=seed, raise_on_quota_exhausted=True,
        resolution=resolution,
    )
    reference_image_bytes = None if portrait_fallback else portrait_path.read_bytes()

    # Per-scene progress print: this loop is the slowest part of a run (one
    # Cloudflare call + INTER_REQUEST_DELAY_SECONDS pacing per scene, times
    # however many scenes the story has) and previously produced zero
    # output until every scene finished — indistinguishable from a genuine
    # hang in CI logs for a large story. A line per scene makes real, slow
    # progress visible instead of going dark for the whole phase.
    raw_results = []
    total = len(script.scenes)
    for i, scene in enumerate(script.scenes):
        result = generate_scene_image_raw(
            scene, images_raw_dir / f"scene_{i:02d}.png", track,
            character_reference=script.character_reference, seed=seed,
            reference_image_bytes=reference_image_bytes, resolution=resolution,
        )
        raw_results.append(result)
        print(f"[{track.key}] [1/4]   scene {i + 1}/{total} done")
    raw_images = [path for path, _ in raw_results]
    scene_used_fallback = [used_fallback for _, used_fallback in raw_results]
    return raw_images, scene_used_fallback


def _reused_story_images(
    script: Script, track: Track, produced_images: ProducedImages,
) -> Optional[tuple[list[Path], list[bool]]]:
    """Looks up a sibling track's already-generated images for this script's
    source_script_id, produced earlier in this SAME run() call. Returns None
    if unavailable — the caller falls back to independent generation."""
    entry = produced_images.get((track.shares_images_with, script.source_script_id or script.id))
    if entry is None:
        return None
    source_images_dir, scene_used_fallback = entry
    if len(scene_used_fallback) != len(script.scenes):
        # Paired scripts must have matching scene counts (visuals must line
        # up 1:1) — a mismatch means these aren't really a matched pair.
        return None
    raw_images = [source_images_dir / f"scene_{i:02d}.png" for i in range(len(script.scenes))]
    return raw_images, scene_used_fallback


def run_track(
    track: Track,
    dry_run: bool = False,
    privacy_status: str = "private",
    produced_images: Optional[ProducedImages] = None,
) -> bool:
    """Returns True if a video was produced (or there was legitimately
    nothing to do — empty queue, already produced today). Raises
    CloudflareQuotaExhausted if the image quota probe fails (caller decides
    whether to skip remaining tracks), or any other exception on a real
    failure. `produced_images` is shared across every track within one
    run() call — see ProducedImages."""
    if produced_images is None:
        produced_images = {}

    if not dry_run and already_produced_today(track.key, limit=track.videos_per_day):
        print(f"[{track.key}] Already produced a video today — skipping until tomorrow.")
        return True

    if not dry_run and not youtube_token_path(track.key).exists():
        # Each track has its own YouTube channel/OAuth token (see README's
        # multi-channel setup). A channel that hasn't been set up yet is a
        # clean skip, not a failure — and we check this BEFORE doing any
        # TTS/image work, since it'd all be wasted on a track that can't
        # upload anyway.
        print(f"[{track.key}] No YouTube OAuth token yet for this channel — skipping until it's set up.")
        return True

    pending_dir = QUEUE_PENDING_DIR / track.key
    used_dir = QUEUE_USED_DIR / track.key

    result = load_next_pending(pending_dir)
    if result is None:
        print(f"[{track.key}] Queue empty — refill needed. Skipping today.")
        return True

    script_path, script = result
    print(f"[{track.key}] Producing: {script.title} ({script.id})")
    run_dir = OUTPUT_DIR / track.key / script.id
    run_dir.mkdir(parents=True, exist_ok=True)
    images_raw_dir = run_dir / "images_raw"

    # Serialized tracks (see Track.serialized) reuse ONE seed across the
    # whole series, not just within one episode, so the protagonist stays
    # visually consistent from episode 1 through episode N.
    seed = _story_seed(track.key if track.serialized else script.id)

    reused = _reused_story_images(script, track, produced_images) if track.shares_images_with else None
    if reused is not None:
        print(f"[{track.key}] [1/4] Reusing images generated for "
              f"'{script.source_script_id or script.id}' earlier this run — no image-generation cost.")
        raw_images, scene_used_fallback = reused
    else:
        if track.shares_images_with:
            print(f"[{track.key}] [1/4] No matching images from '{track.shares_images_with}' "
                  f"this run yet — generating independently instead.")
        else:
            print(f"[{track.key}] [1/4] Probing image generation availability...")
        raw_images, scene_used_fallback = _generate_story_images(script, track, seed, images_raw_dir)
        produced_images[(track.key, script.id)] = (images_raw_dir, scene_used_fallback)

    voice_desc = f"{track.tts_provider}: {'/'.join(track.tts_voices) or track.voice}"
    print(f"[{track.key}] [2/4] Synthesizing voiceover ({voice_desc})...")
    scene_audios = synthesize_script_for_track(script, run_dir / "audio", track)

    print(f"[{track.key}] [3/4] Fitting images for output...")
    fit_images = [
        fit_scene_image(raw, run_dir / "images_fit" / f"scene_{i:02d}.png", SHORT_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]

    music_path = pick_music_track()
    # A real scene illustration (the first one that isn't a gradient
    # fallback) reads far better as the thumbnail than the plain brand
    # gradient — that was the only option before, regardless of how good
    # the generated artwork was. Falls back to the gradient (source_image_
    # path=None) only if every scene fell back too.
    thumbnail_source = next(
        (raw for raw, used_fallback in zip(raw_images, scene_used_fallback) if not used_fallback),
        None,
    )
    thumbnail_path = generate_thumbnail(
        script.title, run_dir / "thumbnail.jpg", draw_text=track.burn_captions,
        source_image_path=thumbnail_source,
    )

    print(f"[{track.key}] [4/4] Assembling and uploading Short (dry_run={dry_run})...")
    short_path = assemble_short(
        script, scene_audios, fit_images,
        work_dir=run_dir / "work_short", out_path=run_dir / "video_short.mp4",
        music_path=music_path, scene_used_fallback=scene_used_fallback,
        burn_captions=track.burn_captions,
    )
    short_upload = upload_daily_video(
        script, short_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run, is_short=True,
    )
    print(short_upload)

    mark_used(script_path, used_dir)
    if not dry_run:
        mark_produced_today(track.key)
    print(f"[{track.key}] Done. Moved {script_path.name} to scripts_queue/used/{track.key}/.")
    return True


LONG_FORM_STATE_SUFFIX = "_long"


def run_long_form_track(
    track: Track,
    dry_run: bool = False,
    privacy_status: str = "private",
    produced_images: Optional[ProducedImages] = None,
) -> bool:
    """The long-form (8-10 min, 16:9) pipeline — a genuinely SEPARATE story
    from the Short (run_track), not a longer cut of the same one: its own
    queue (scripts_queue/pending/<track>/long/), its own scene count/images,
    its own daily-production gate (state key "<track>_long", independent of
    the Short's). Only called for Track.produce_long_form=True tracks.

    Deliberately not a trimmed/expanded version of one shared script — a
    Short and an 8-10 min video have different pacing needs (a handful of
    punchy beats vs. a real multi-act story), so writing them as one script
    and cutting it two ways either made the Short drag or made the
    "long-form" video barely longer than the Short (the bug this was built
    to fix — every kids script except one hand-authored outlier ended up
    45-70s even in its "long-form" cut, since both formats shared the same
    ~7-scene script)."""
    if produced_images is None:
        produced_images = {}
    long_state_key = f"{track.key}{LONG_FORM_STATE_SUFFIX}"

    if not dry_run and already_produced_today(long_state_key, limit=1):
        print(f"[{track.key}] Long-form already produced today — skipping until tomorrow.")
        return True
    if not dry_run and not youtube_token_path(track.key).exists():
        print(f"[{track.key}] No YouTube OAuth token yet for this channel — skipping until it's set up.")
        return True

    pending_dir = QUEUE_PENDING_DIR / track.key / "long"
    used_dir = QUEUE_USED_DIR / track.key / "long"

    result = load_next_pending(pending_dir)
    if result is None:
        print(f"[{track.key}] Long-form queue empty — refill needed. Skipping today.")
        return True

    script_path, script = result
    print(f"[{track.key}] Producing long-form: {script.title} ({script.id})")
    run_dir = OUTPUT_DIR / track.key / "long" / script.id
    run_dir.mkdir(parents=True, exist_ok=True)
    images_raw_dir = run_dir / "images_raw"

    seed = _story_seed(f"{track.key}_long" if track.serialized else script.id)

    reused = _reused_story_images(script, track, produced_images) if track.shares_images_with else None
    if reused is not None:
        print(f"[{track.key}] [1/3] Reusing long-form images generated for "
              f"'{script.source_script_id or script.id}' earlier this run — no image-generation cost.")
        raw_images, scene_used_fallback = reused
    else:
        if track.shares_images_with:
            print(f"[{track.key}] [1/3] No matching long-form images from '{track.shares_images_with}' "
                  f"this run yet — generating independently instead.")
        else:
            print(f"[{track.key}] [1/3] Probing image generation availability...")
        raw_images, scene_used_fallback = _generate_story_images(
            script, track, seed, images_raw_dir, resolution=LONG_FORM_RESOLUTION,
        )
        produced_images[(track.key, script.id)] = (images_raw_dir, scene_used_fallback)

    voice_desc = f"{track.tts_provider}: {'/'.join(track.tts_voices) or track.voice}"
    print(f"[{track.key}] [2/3] Synthesizing voiceover ({voice_desc})...")
    scene_audios = synthesize_script_for_track(script, run_dir / "audio", track)

    print(f"[{track.key}] Fitting images for long-form (16:9)...")
    fit_images_long = [
        fit_scene_image(raw, run_dir / "images_fit_long" / f"scene_{i:02d}.png", LONG_FORM_RESOLUTION)
        for i, raw in enumerate(raw_images)
    ]

    thumbnail_source = next(
        (raw for raw, used_fallback in zip(raw_images, scene_used_fallback) if not used_fallback),
        None,
    )
    thumbnail_path = generate_thumbnail(
        script.title, run_dir / "thumbnail.jpg", draw_text=track.burn_captions,
        source_image_path=thumbnail_source,
    )

    print(f"[{track.key}] [3/3] Assembling and uploading long-form (dry_run={dry_run})...")
    long_path = assemble_long_form(
        script, scene_audios, fit_images_long,
        work_dir=run_dir / "work_long", out_path=run_dir / "video_long.mp4",
        music_path=pick_music_track(), scene_used_fallback=scene_used_fallback,
        burn_captions=track.burn_captions,
    )
    long_upload = upload_daily_video(
        script, long_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run, is_short=False,
    )
    print(long_upload)

    mark_used(script_path, used_dir)
    if not dry_run:
        mark_produced_today(long_state_key)
    print(f"[{track.key}] Long-form done. Moved {script_path.name} to scripts_queue/used/{track.key}/long/.")
    return True


def run_math_track(track: Track, dry_run: bool = False, privacy_status: str = "private") -> bool:
    """Produces + uploads one math_explainers video: no AI images, no
    Cloudflare quota involved at all — synthesizes each narration line
    (pipeline.tts), renders the paired hand-authored canvas piece against
    those real durations (pipeline.canvas_video), then uploads as a single
    Short-style video. Same queue-consumption/state contract as run_track."""
    if not dry_run and already_produced_today(track.key, limit=track.videos_per_day):
        print(f"[{track.key}] Already produced a video today — skipping until tomorrow.")
        return True

    if not dry_run and not youtube_token_path(track.key).exists():
        print(f"[{track.key}] No YouTube OAuth token yet for this channel — skipping until it's set up.")
        return True

    pending_dir = QUEUE_PENDING_DIR / track.key
    used_dir = QUEUE_USED_DIR / track.key

    result = load_next_pending_math(pending_dir)
    if result is None:
        print(f"[{track.key}] Queue empty — refill needed. Skipping today.")
        return True

    script_path, script = result
    print(f"[{track.key}] Producing: {script.title} ({script.id})")
    run_dir = OUTPUT_DIR / track.key / script.id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{track.key}] [1/3] Synthesizing narration lines (edge: {track.voice})...")
    narration_path, line_times = synthesize_narration_lines(script.narration_lines, run_dir / "audio", track.voice)

    print(f"[{track.key}] [2/3] Rendering '{script.piece}' (Playwright + ffmpeg)...")
    piece_html = MATH_PIECES_DIR / script.piece / "piece.html"
    video_path = render_piece_video(
        piece_html, line_times, narration_path, run_dir / "video.mp4",
        music_path=pick_music_track(), params=script.params,
    )

    thumbnail_path = generate_thumbnail(script.title, run_dir / "thumbnail.jpg")

    print(f"[{track.key}] [3/3] Uploading (dry_run={dry_run})...")
    upload_result = upload_daily_video(
        script, video_path, thumbnail_path, track,
        privacy_status=privacy_status, dry_run=dry_run, is_short=True,
    )
    print(upload_result)

    mark_used(script_path, used_dir)
    if not dry_run:
        mark_produced_today(track.key)
    print(f"[{track.key}] Done. Moved {script_path.name} to scripts_queue/used/{track.key}/.")
    return True


def run_manifestation_track(track: Track, dry_run: bool = False, privacy_status: str = "private") -> bool:
    """Produces + uploads one manifestation/affirmation song video. Unlike
    every other track, there's no pre-authored queue to consume — lyrics
    and per-line scenes are written live each run by pipeline.
    manifestation_lyrics (Gemini), the song by pipeline.manifestation_video.
    synthesize_song (ACE-Step on HF's free ZeroGPU — ZeroGPUQuotaExhausted
    is this track's equivalent of CloudflareQuotaExhausted: real observed
    daily budget is small, ~5 minutes, and a single song generation can
    consume most of it, so hitting the wall here aborts the run the same
    way Cloudflare's does, not a failure), and the video from per-line AI
    scene images (Cloudflare, free, one consistent character across all of
    them via image-to-image conditioning) with Ken Burns motion held for
    each line's REAL sung duration and karaoke-style captions."""
    import hashlib
    from datetime import date

    from pipeline.ffmpeg_utils import group_words_into_captions_adaptive, probe_duration
    from pipeline.manifestation_lyrics import (
        _load_title_history,
        _save_title_history,
        extract_lyric_lines,
        generate_line_scenes,
        generate_lyrics,
        pick_theme,
    )
    from pipeline.manifestation_video import (
        DEFAULT_CHARACTER,
        SONG_SEED,
        assemble_video,
        build_line_clips,
        generate_character_reference,
        generate_scene_image,
        match_words_to_lines,
        synthesize_song,
        transcribe_song,
    )
    from pipeline.upload import set_thumbnail, upload_video

    if not dry_run and already_produced_today(track.key, limit=track.videos_per_day):
        print(f"[{track.key}] Already produced a video today — skipping until tomorrow.")
        return True
    if not dry_run and not youtube_token_path(track.key).exists():
        print(f"[{track.key}] No YouTube OAuth token yet for this channel — skipping until it's set up.")
        return True

    today = date.today()
    run_dir = OUTPUT_DIR / track.key / today.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    theme = pick_theme(today.toordinal())  # naturally alternates forever, no stored counter needed

    title_history = _load_title_history()
    print(f"[{track.key}] [1/6] Writing lyrics (Gemini, theme={theme})...")
    lyrics_result = generate_lyrics(theme, avoid_titles=title_history)
    lines = extract_lyric_lines(lyrics_result["lyrics"])
    print(f"[{track.key}] '{lyrics_result['title']}' — {len(lines)} sung lines")

    print(f"[{track.key}] [2/6] Writing per-line scene descriptions (Gemini)...")
    scenes = generate_line_scenes(lines, DEFAULT_CHARACTER)

    print(f"[{track.key}] [3/6] Synthesizing song (ACE-Step)...")
    song_path = synthesize_song(lyrics_result["lyrics"], lyrics_result["style_tags"], 165.0, run_dir / "song.mp3")
    song_duration = probe_duration(song_path)

    print(f"[{track.key}] [4/6] Transcribing real per-word timing (faster-whisper)...")
    words = transcribe_song(song_path)
    caption_lines = group_words_into_captions_adaptive(words, gap_threshold=0.45, max_words=8)
    matched_lines = match_words_to_lines(caption_lines, lines)

    print(f"[{track.key}] [5/6] Generating character-consistent scene images + assembling video...")
    ref_path = run_dir / "reference.png"
    generate_character_reference(DEFAULT_CHARACTER, ref_path)
    ref_bytes = ref_path.read_bytes()

    images_dir = run_dir / "images"
    line_image_paths: dict[str, list[Path]] = {}
    for line, scene_list in scenes.items():
        slug = hashlib.sha1(line.lower().encode()).hexdigest()[:10]
        paths = []
        for i, scene in enumerate(scene_list):
            out_path = images_dir / f"{slug}_{i}.png"
            generate_scene_image(scene, ref_bytes, out_path, seed=SONG_SEED + i)
            paths.append(out_path)
        line_image_paths[line] = paths

    clip_paths = build_line_clips(matched_lines, line_image_paths, song_duration, run_dir / "clips")
    video_path = assemble_video(clip_paths, song_path, words, run_dir / "final.mp4", run_dir / "work")

    print(f"[{track.key}] [6/6] Uploading (dry_run={dry_run})...")
    token_path = youtube_token_path(track.key)
    title = lyrics_result["title"][:90]
    description = (
        f"A daily manifestation / affirmation song to play on repeat and let the words sink in.\n\n"
        f"{lyrics_result['hook_phrase']}\n\n"
        f"New affirmation songs daily. Let the lyrics become your mindset.\n\n"
        f"#manifestation #affirmations #lawofattraction"
    )
    upload_result = upload_video(
        video_path, title, description, [*track.extra_tags, theme],
        privacy_status=privacy_status, dry_run=dry_run,
        category_id=track.category_id, made_for_kids=track.made_for_kids, token_path=token_path,
    )
    print(upload_result)
    if not dry_run and not upload_result.get("dry_run"):
        try:
            set_thumbnail(upload_result["video_id"], ref_path, dry_run=False, token_path=token_path)
        except Exception as e:
            # Same known restriction as every other track: custom
            # thumbnails need a phone-verified channel. Don't fail the run
            # over it -- YouTube's auto-generated thumbnail still works.
            print(f"[{track.key}] thumbnail not set (channel likely needs phone verification): {e}")

    if not dry_run:
        mark_produced_today(track.key)
        _save_title_history(title_history + [f"{lyrics_result['title']} ({lyrics_result['hook_phrase']})"])
    print(f"[{track.key}] Done.")
    return True


def run(dry_run: bool = False, privacy_status: str = "private", track_key: Optional[str] = None) -> int:
    tracks = [TRACKS[track_key]] if track_key else list(TRACKS.values())

    any_failed = False
    cloudflare_quota_exhausted = False
    zerogpu_quota_exhausted = False
    produced_images: ProducedImages = {}
    for track in tracks:
        if track.paused:
            print(f"[{track.key}] Paused — skipping.")
            continue
        if cloudflare_quota_exhausted:
            # Every content_type calls Cloudflare for images (including
            # "song" — see pipeline.manifestation_video), so this quota
            # skips everything, same as before.
            print(f"[{track.key}] Skipping — Cloudflare image quota already confirmed exhausted "
                  f"this run. Will retry on the next scheduled run.")
            continue
        if zerogpu_quota_exhausted and track.content_type == "song":
            # Only "song" tracks call ACE-Step; a "story"/"canvas" track
            # doesn't touch ZeroGPU at all and can still run this pass.
            print(f"[{track.key}] Skipping — HF ZeroGPU quota already confirmed exhausted "
                  f"this run. Will retry on the next scheduled run.")
            continue
        try:
            if track.content_type == "canvas":
                run_math_track(track, dry_run=dry_run, privacy_status=privacy_status)
            elif track.content_type == "song":
                run_manifestation_track(track, dry_run=dry_run, privacy_status=privacy_status)
            else:
                run_track(track, dry_run=dry_run, privacy_status=privacy_status, produced_images=produced_images)
                if track.produce_long_form:
                    # A genuinely separate production (own queue, own
                    # story, own images) — see run_long_form_track's
                    # docstring. Same try/except as the Short above: a
                    # Cloudflare quota hit here still aborts the rest of
                    # this run() call, since the quota is account-wide.
                    run_long_form_track(track, dry_run=dry_run, privacy_status=privacy_status, produced_images=produced_images)
        except CloudflareQuotaExhausted as e:
            # Account-wide, not per-track — every other track would hit the
            # same wall, so stop here rather than burning time confirming
            # that 3 more times. Not a failure: this is the pipeline
            # working as designed, and the next scheduled run retries it.
            cloudflare_quota_exhausted = True
            print(f"[{track.key}] Cloudflare image quota exhausted: {e}")
            print(f"[{track.key}] Aborting this track and skipping remaining tracks — "
                  f"script stays queued for the next scheduled run.")
        except ZeroGPUQuotaExhausted as e:
            zerogpu_quota_exhausted = True
            print(f"[{track.key}] HF ZeroGPU quota exhausted: {e}")
            print(f"[{track.key}] Aborting this track — will retry on the next scheduled run.")
        except GeminiUnavailable as e:
            # Gemini stayed unreachable through every retry -- confirmed on
            # 2026-08-25 to span many hours (09:55-20:59 UTC), well past
            # what retries can ride out. Not a code defect: same "skip this
            # track, retry next scheduled run" handling as the quota-
            # exhaustion cases above, not a hard failure worth an alert.
            print(f"[{track.key}] Gemini unavailable: {e}")
            print(f"[{track.key}] Aborting this track — will retry on the next scheduled run.")
        except Exception:
            any_failed = True
            print(f"[{track.key}] FAILED:")
            traceback.print_exc()

    return 1 if any_failed else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip the real YouTube upload")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--track", default=None, choices=list(TRACKS.keys()), help="Run only this track (default: all tracks)")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, privacy_status=args.privacy, track_key=args.track))


if __name__ == "__main__":
    main()
