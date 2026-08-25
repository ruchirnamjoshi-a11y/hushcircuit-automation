"""Gemini-based content generation for the manifestation/affirmation song
track (pipeline.manifestation_video does the audio/video generation and
assembly). Two things are generated per video, both via Gemini so this
scales to a new song every day without hand-authoring:

1. Lyrics + a music-style prompt (generate_lyrics) -- alternates between two
   themes (general motivation vs. money/abundance affirmations) daily.
2. A visual scene description per UNIQUE lyric line, with as many distinct
   pose/angle variants as that line repeats in the song (generate_line_scenes)
   -- so a 4x-repeated chorus gets 4 different shots instead of the same
   photo shown 4 times. This replaces hand-typing a line->image mapping
   (which doesn't scale past one specific, already-known song).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter

import requests

from pipeline.config import GEMINI_API_KEY, GEMINI_MODEL

REQUEST_TIMEOUT_SECONDS = 120

# 503 (Service Unavailable) observed repeatedly in production CI runs (every
# scheduled run failed on 2026-08-23 from this alone) -- genuinely transient,
# but this call had no retry at all, so it failed the whole job on one bad
# moment even though every other track had already succeeded or correctly
# skipped. Same pattern as pipeline.story_writer's _call_gemini_raw.
GEMINI_503_MAX_RETRIES = 3
GEMINI_503_RETRY_DELAY_SECONDS = 15

THEMES = {
    "motivation": (
        "General motivational/manifestation affirmations: resilience, "
        "confidence, unstoppable energy, turning struggle into strength. "
        "Not about money specifically."
    ),
    "money": (
        "Money/abundance affirmations: attracting wealth and financial "
        "freedom, e.g. 'money comes to me easily', 'I am a magnet for "
        "abundance', 'I am wealthy'. Confident, positive, never desperate "
        "or grasping in tone -- the abundance is already assumed, not "
        "begged for."
    ),
}


def pick_theme(day_index: int) -> str:
    """Deterministic daily alternation (not random) so two consecutive runs
    on the same day index always agree, and coverage of both themes is even
    over time rather than left to chance."""
    return "money" if day_index % 2 == 1 else "motivation"


LYRICS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style_tags": {"type": "string"},
        "lyrics": {"type": "string"},
        "hook_phrase": {"type": "string"},
    },
    "required": ["title", "style_tags", "lyrics", "hook_phrase"],
}

LYRICS_PROMPT_TEMPLATE = """You are writing lyrics for a short (~165 second) daily affirmation/\
manifestation song for a YouTube channel. The song is sung, not spoken -- \
simple, rhyming, highly repetitive, built around one short hook phrase \
repeated as the chorus (so listeners absorb it on repeat, like real pop \
hooks). Confident and uplifting throughout -- even a "things were hard" \
opening line must resolve to hope within that same line, never stay bleak.

THEME
{theme_guidance}

STRUCTURE (use these exact section tags on their own line, matching ACE-Step's \
lyric format):
[verse]
4 short lines
[verse]
4 short lines
[chorus]
4 short lines built around ONE hook phrase, repeated with slight variation
[verse]
4 short lines
[chorus]
(repeat the same chorus)
[bridge]
2 short lines
[chorus]
(repeat the same chorus)
[outro]
2 short lines, echoing the hook phrase

OUTPUT
- "title": short video title.
- "style_tags": comma-separated simple descriptive tags for the music style \
(genre, instruments, mood, tempo feel) -- plain descriptive words only, \
NOT genre-jargon like "four-on-the-floor", and do NOT specify an exact BPM \
number (the model doesn't reliably follow requested tempo).
- "lyrics": the full lyrics text with [section] tags as shown above.
- "hook_phrase": the short repeated hook phrase on its own, e.g. "I'm unstoppable".
"""


def _call_gemini(prompt: str, schema: dict) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    for attempt in range(GEMINI_503_MAX_RETRIES + 1):
        try:
            response = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.Timeout:
            # A read timeout raises before any response exists, so it never
            # reaches the status_code check below and skipped this retry
            # loop entirely (confirmed in production: run 32834495308 hit
            # this exact ReadTimeoutError, uncaught, right after the 503
            # retry logic above was added for a *different* failure mode).
            if attempt < GEMINI_503_MAX_RETRIES:
                time.sleep(GEMINI_503_RETRY_DELAY_SECONDS)
                continue
            raise
        if response.status_code == 503 and attempt < GEMINI_503_MAX_RETRIES:
            time.sleep(GEMINI_503_RETRY_DELAY_SECONDS)
            continue
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


def generate_lyrics(theme: str) -> dict:
    """Returns {title, style_tags, lyrics, hook_phrase}. `theme` must be a
    key in THEMES (see pick_theme for the daily-alternation policy)."""
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}, expected one of {list(THEMES)}")
    prompt = LYRICS_PROMPT_TEMPLATE.format(theme_guidance=THEMES[theme])
    return _call_gemini(prompt, LYRICS_SCHEMA)


def extract_lyric_lines(lyrics_text: str) -> list[str]:
    """Strips [section] tags and blank lines, returning the sung lines in
    order (duplicates kept -- a repeated chorus appears multiple times,
    which is exactly what count_line_repeats/generate_line_scenes need)."""
    lines = []
    for raw in lyrics_text.splitlines():
        line = raw.strip()
        if not line or re.match(r"^\[.*\]$", line):
            continue
        lines.append(line)
    return lines


SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "string"},
                    "scenes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["line", "scenes"],
            },
        }
    },
    "required": ["lines"],
}

SCENE_PROMPT_TEMPLATE = """You are describing visual scenes for a manifestation/affirmation music \
video. There is ONE consistent on-screen character throughout the whole \
video: {character_description}

For each lyric line below, write {repeat_note} distinct visual scene \
description(s) -- setting, the character's pose/action, camera framing -- \
that match that line's meaning and emotional tone. Tone must stay \
confident, resilient, and uplifting even for lines about a hard moment \
(e.g. calm/composed, NEVER genuinely sad or depressed-looking). Each scene \
description should be ONE sentence, concrete and filmable, and should NOT \
repeat the character description (that's added separately) -- just the \
setting/pose/action/expression. Where a line repeats multiple times in the \
song, give that many DIFFERENT scenes (different angle, pose, or setting) \
so repeats don't look identical on screen.

LINES (each with its repeat count in the song):
{line_list}

Return one entry per UNIQUE line, with exactly as many "scenes" as its \
repeat count.
"""


def count_line_repeats(lines: list[str]) -> dict[str, int]:
    """Case/whitespace-insensitive repeat counts, keyed by the ORIGINAL
    (first-seen) casing/text so results plug straight back into matching
    against the original lyrics."""
    counts: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for line in lines:
        key = line.lower().strip()
        counts[key] += 1
        first_seen.setdefault(key, line)
    return {first_seen[key]: n for key, n in counts.items()}


def generate_line_scenes(lines: list[str], character_description: str) -> dict[str, list[str]]:
    """Returns {line_text: [scene_description, ...]} with one entry per
    UNIQUE line and len(scenes) == that line's repeat count in the song."""
    repeats = count_line_repeats(lines)
    line_list = "\n".join(f'- "{line}" (repeats {n}x)' for line, n in repeats.items())
    repeat_note = "as many" if len(repeats) > 1 else "that many"
    prompt = SCENE_PROMPT_TEMPLATE.format(
        character_description=character_description, repeat_note=repeat_note, line_list=line_list,
    )
    raw = _call_gemini(prompt, SCENE_SCHEMA)

    result: dict[str, list[str]] = {}
    returned_by_key = {entry["line"].lower().strip(): entry["scenes"] for entry in raw.get("lines", [])}
    for line, n in repeats.items():
        scenes = returned_by_key.get(line.lower().strip(), [])
        if len(scenes) < n:
            # Gemini under-returned variants for this line -- pad by cycling
            # what it did give us rather than failing the whole run; still
            # better than one hand-typed default bucket for everything.
            if not scenes:
                scenes = [f"{line} -- a confident, uplifting moment"]
            scenes = [scenes[i % len(scenes)] for i in range(n)]
        result[line] = scenes[:n]
    return result
