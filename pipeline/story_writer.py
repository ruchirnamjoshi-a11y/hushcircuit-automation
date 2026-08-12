"""Weekly story generation via the Gemini API (free tier) — replaces the
manual "write N scripts by hand every week" step. Generated stories are
validated against the exact same schema (pipeline.scripts.validate) as
hand-written ones before being written into scripts_queue/pending/<track>/,
so malformed model output fails loudly instead of corrupting the queue.

This is the only place in the pipeline that calls an LLM — script writing
was previously done interactively in Claude Code sessions. Daily production
(run_daily.py) still only ever *consumes* the queue; it never calls an LLM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from pipeline.config import GEMINI_API_KEY, GEMINI_MODEL, QUEUE_PENDING_DIR, QUEUE_USED_DIR, Track
from pipeline.scripts import Script, ScriptValidationError, to_dict, validate

REQUEST_TIMEOUT_SECONDS = 120

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "narration": {"type": "string"},
        "on_screen_text": {"type": "string"},
        "duration_hint": {"type": "number"},
        "visual": {"type": "string"},
    },
    "required": ["narration", "on_screen_text", "duration_hint", "visual"],
}

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "character_reference": {"type": "string"},
        "scenes": {"type": "array", "items": SCENE_SCHEMA},
    },
    "required": ["id", "title", "description", "tags", "character_reference", "scenes"],
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"stories": {"type": "array", "items": STORY_SCHEMA}},
    "required": ["stories"],
}

PROMPT_TEMPLATE = """You are writing short-form vertical video scripts for a YouTube Shorts \
channel that posts one narrated story per day for the "{label}" audience.

AUDIENCE AND TONE
{story_guidance}

STORY STRUCTURE (every story)
- 7 to 9 scenes total. Scene 1 is the hook (sets up the situation in 1-2 \
sentences, no title-card cliches). The last scene is a brief outro that \
naturally invites the viewer to follow the channel for more stories \
("follow along for more" style, not a hard sales pitch) — set its \
on_screen_text to "SUBSCRIBE".
- Each scene's "narration" is 1-4 natural spoken sentences (roughly 15-45 \
words) — this is read aloud by a text-to-speech voice, so write for the \
ear, not the page. No stage directions, no dialogue tags like "she said \
sadly" beyond simple ones.
- "on_screen_text" is a short ALL-CAPS chapter-title label (2-5 words) \
summarizing that scene, e.g. "THE STUMBLE" or "A NEW BEGINNING".
- "duration_hint" is the approximate narration length in seconds at a \
natural speaking pace (~2.3 words/second) — just estimate from the \
narration's word count.
- "visual" is a SEPARATE short, concrete visual description (10-20 words) \
for an AI image generator — NOT a copy of the narration, NOT a full \
sentence with punctuation, NOT a quoted slogan. Describe the ACTION, POSE, \
and SETTING of that specific moment only — not the character's fixed \
physical appearance (that belongs in "character_reference" below and gets \
automatically added to every scene, so repeating it here just wastes words \
and can conflict). CRITICAL: never describe any text, signs, banners, \
logos, or readable words appearing in the scene, and never describe a \
building exterior or storefront with visible signage (e.g. a school \
entrance, a shop front) — describe close-up character moments, nature, or \
interiors without signage instead.
- "character_reference": ONE detailed, reusable physical description of \
the story's main protagonist (species/build, hair or fur color, clothing, \
1-2 distinguishing features — e.g. "a small cream-colored rabbit with \
floppy brown-tipped ears and a red neckerchief"). This exact description \
gets prepended to every scene's image prompt so the same character stays \
visually recognizable across independently-generated images — write it \
once, specifically, and don't repeat it inside individual scenes' \
"visual" fields. If a story genuinely has no single recurring character \
(e.g. it's about a place or an event), leave this as an empty string.
- "id" is a unique kebab-case slug derived from the title (e.g. \
"the-tryout"). "tags" is 4-6 relevant lowercase keyword tags.

Write {count} DIFFERENT stories with distinct premises (no repeated plots). \
Do not reuse any of these existing titles: {avoid_titles}

Output must match the provided JSON schema exactly.
"""


def _build_prompt(track: Track, count: int, avoid_titles: list[str]) -> str:
    avoid = ", ".join(f'"{t}"' for t in avoid_titles) if avoid_titles else "(none yet)"
    return PROMPT_TEMPLATE.format(
        label=track.label, story_guidance=track.story_guidance, count=count, avoid_titles=avoid,
    )


def _call_gemini(prompt: str) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    response = requests.post(
        url, params={"key": GEMINI_API_KEY}, json=body, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _existing_titles(track_key: str) -> list[str]:
    titles = []
    for base_dir in (QUEUE_PENDING_DIR, QUEUE_USED_DIR):
        for path in (base_dir / track_key).glob("*.json"):
            try:
                data = json.loads(path.read_text())
                titles.append(data.get("title", ""))
            except (json.JSONDecodeError, OSError):
                continue
    return [t for t in titles if t]


def generate_stories(track: Track, count: int = 7) -> list[Script]:
    """Calls Gemini once for `count` stories, validates each against the
    real Script schema, and returns only the ones that pass. Raises if the
    API call itself fails or returns no valid stories at all."""
    avoid_titles = _existing_titles(track.key)
    prompt = _build_prompt(track, count, avoid_titles)
    raw = _call_gemini(prompt)

    scripts = []
    for i, story_data in enumerate(raw.get("stories", [])):
        try:
            scripts.append(validate(story_data))
        except ScriptValidationError as e:
            print(f"[story_writer] skipping story {i} for track '{track.key}': {e}")

    if not scripts:
        raise RuntimeError(f"Gemini returned no valid stories for track '{track.key}'")
    return scripts


def _next_file_number(track_key: str) -> int:
    max_n = 0
    for base_dir in (QUEUE_PENDING_DIR, QUEUE_USED_DIR):
        for path in (base_dir / track_key).glob("*.json"):
            match = re.match(r"^(\d+)-", path.name)
            if match:
                max_n = max(max_n, int(match.group(1)))
    return max_n + 1


def write_stories(track: Track, scripts: list[Script]) -> list[Path]:
    out_dir = QUEUE_PENDING_DIR / track.key
    out_dir.mkdir(parents=True, exist_ok=True)
    next_n = _next_file_number(track.key)

    written = []
    for i, script in enumerate(scripts):
        out_path = out_dir / f"{next_n + i:03d}-{script.id}.json"
        out_path.write_text(json.dumps(to_dict(script), indent=2))
        written.append(out_path)
    return written
