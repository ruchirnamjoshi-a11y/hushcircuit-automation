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
import time
from pathlib import Path

import requests

from pipeline.config import GEMINI_API_KEY, GEMINI_MODEL, QUEUE_PENDING_DIR, QUEUE_USED_DIR, SERIES_STATE_DIR, Track
from pipeline.scripts import Script, ScriptValidationError, to_dict, validate

REQUEST_TIMEOUT_SECONDS = 120
# gemini-flash-latest's free tier observed at ~5 requests/minute (hit a real
# 429 backfilling at 1 req/sec) — 13s keeps a big backfill batch under that.
BACKFILL_PACING_SECONDS = 13
BACKFILL_RATE_LIMIT_RETRY_SECONDS = 65  # a bit over a minute, to clear the window
BACKFILL_MAX_RETRIES = 2

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

# Shared scene-writing rules — reused verbatim by both the standalone
# batch-story prompt and the serialized-episode prompts below, so the
# important "no on-screen text/signage" image-generation guidance can't
# drift out of sync between the two paths.
SCENE_STRUCTURE_RULES = """- 7 to 9 scenes total. Scene 1 is the hook (sets up the situation in 1-2 \
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
- "visual" is a SEPARATE, DETAILED visual description (20-35 words) for an \
AI image generator — NOT a copy of the narration, NOT a full sentence with \
punctuation, NOT a quoted slogan. It must depict the SPECIFIC action \
described in THIS scene's narration, not a generic or interchangeable \
moment — if the narration says the character is striking, recoiling, \
kneeling, or looking toward something specific, the visual must show \
exactly that beat, not just "a dramatic scene". Be concrete about pose, \
camera angle, and immediate surroundings — not the character's fixed \
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
"the-tryout"). "tags" is 4-6 relevant lowercase keyword tags."""

PROMPT_TEMPLATE = """You are writing short-form vertical video scripts for a YouTube Shorts \
channel that posts one narrated story per day for the "{label}" audience.

AUDIENCE AND TONE
{story_guidance}

STORY STRUCTURE (every story)
{scene_structure_rules}

Write {count} DIFFERENT stories with distinct premises (no repeated plots). \
Do not reuse any of these existing titles: {avoid_titles}

Output must match the provided JSON schema exactly.
"""


def _build_prompt(track: Track, count: int, avoid_titles: list[str]) -> str:
    avoid = ", ".join(f'"{t}"' for t in avoid_titles) if avoid_titles else "(none yet)"
    return PROMPT_TEMPLATE.format(
        label=track.label, story_guidance=track.story_guidance, count=count, avoid_titles=avoid,
        scene_structure_rules=SCENE_STRUCTURE_RULES,
    )


GEMINI_503_MAX_RETRIES = 3
GEMINI_503_RETRY_DELAY_SECONDS = 15


def _call_gemini_raw(prompt: str, response_schema: dict) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }
    # 503 (Service Unavailable) observed repeatedly this session on large/
    # complex requests (e.g. the 32-40 scene long-form schema) -- genuinely
    # transient (a same-prompt retry succeeds), but this call had no retry
    # at all before, so it failed the whole batch on one bad moment.
    for attempt in range(GEMINI_503_MAX_RETRIES + 1):
        try:
            response = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.Timeout:
            # A read timeout raises before any response exists, so it never
            # reached the status_code check below -- the retry loop above
            # only covered 503s, not a genuinely stalled request (confirmed
            # in production via the identical pattern in
            # pipeline.manifestation_lyrics: run 32834495308 hit an uncaught
            # ReadTimeoutError from this same Gemini endpoint).
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


def _call_gemini(prompt: str) -> dict:
    return _call_gemini_raw(prompt, RESPONSE_SCHEMA)


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


def _next_file_number(track_key: str, subdir: str = "") -> int:
    max_n = 0
    for base_dir in (QUEUE_PENDING_DIR, QUEUE_USED_DIR):
        for path in (base_dir / track_key / subdir).glob("*.json"):
            match = re.match(r"^(\d+)-", path.name)
            if match:
                max_n = max(max_n, int(match.group(1)))
    return max_n + 1


def write_stories(track: Track, scripts: list[Script], subdir: str = "") -> list[Path]:
    """subdir="" (default) writes to the Short queue, scripts_queue/pending/
    <track>/ — pass subdir="long" for the long-form queue,
    scripts_queue/pending/<track>/long/ (see generate_long_form_stories)."""
    out_dir = QUEUE_PENDING_DIR / track.key / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    next_n = _next_file_number(track.key, subdir)

    written = []
    for i, script in enumerate(scripts):
        out_path = out_dir / f"{next_n + i:03d}-{script.id}.json"
        out_path.write_text(json.dumps(to_dict(script), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(out_path)
    return written


# --- Long-form (8-10 min, own queue) ------------------------------------
# A genuinely SEPARATE story from the Short, not a longer cut of one --
# see run_daily.run_long_form_track's docstring for why. minItems/maxItems
# on "scenes" structurally enforces real long-form length (JSON schema, not
# just a prompt instruction the model could round down on) -- the original
# bug here was every batch-written script capping at 7-9 scenes (~45-70s)
# even for tracks with Track.produce_long_form=True, because they all went
# through the SAME generate_stories()/SCENE_STRUCTURE_RULES built for Shorts.

SCENE_STRUCTURE_RULES_LONG = """- 30 to 38 scenes total. Scene 1 is the hook (sets up the situation in 1-2 \
sentences, no title-card cliches). This is a real multi-act story (a setup, \
a genuine complication or obstacle, a turning point, a resolution) — not a \
Short's single beat stretched out. The last scene is a brief outro that \
naturally invites the viewer to follow the channel for more stories \
("follow along for more" style, not a hard sales pitch) — set its \
on_screen_text to "SUBSCRIBE".
- Each scene's "narration" is 2-4 natural spoken sentences (roughly \
35-60 words) — this is read aloud by a text-to-speech voice, so write for \
the ear, not the page. No stage directions, no dialogue tags like "she said \
sadly" beyond simple ones.
- "on_screen_text" is a short ALL-CAPS chapter-title label (2-5 words) \
summarizing that scene, e.g. "THE STUMBLE" or "A NEW BEGINNING".
- "duration_hint" is the approximate narration length in seconds at a \
natural speaking pace (~2.3 words/second) — just estimate from the \
narration's word count.
- "visual" is a SEPARATE, DETAILED visual description (20-35 words) for an \
AI image generator — NOT a copy of the narration, NOT a full sentence with \
punctuation, NOT a quoted slogan. It must depict the SPECIFIC action \
described in THIS scene's narration, not a generic or interchangeable \
moment — if the narration says the character is striking, recoiling, \
kneeling, or looking toward something specific, the visual must show \
exactly that beat, not just "a dramatic scene". Be concrete about pose, \
camera angle, and immediate surroundings — not the character's fixed \
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
"the-tryout"). "tags" is 4-6 relevant lowercase keyword tags."""

PROMPT_TEMPLATE_LONG = """You are writing a real 8-10 minute narrated long-form video script (not a \
Short) for a YouTube channel that posts for the "{label}" audience.

AUDIENCE AND TONE
{story_guidance}

STORY STRUCTURE
{scene_structure_rules}

Write {count} DIFFERENT long-form stories with distinct premises (no repeated \
plots, and distinct from any Short-length story on this channel). Do not \
reuse any of these existing titles: {avoid_titles}

Output must match the provided JSON schema exactly.
"""

LONG_SCENE_SCHEMA = dict(SCENE_SCHEMA)  # same per-scene shape as the Short

LONG_STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "character_reference": {"type": "string"},
        "scenes": {"type": "array", "items": LONG_SCENE_SCHEMA},
    },
    "required": ["id", "title", "description", "tags", "character_reference", "scenes"],
}

LONG_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"stories": {"type": "array", "items": LONG_STORY_SCHEMA}},
    "required": ["stories"],
}


def generate_long_form_stories(track: Track, count: int = 1) -> list[Script]:
    """Same contract as generate_stories, but for the long-form (32-40
    scene) queue. count defaults to 1: a single long-form generation is
    already a large structured response (30+ scenes), and this queue only
    needs to stay a few episodes ahead, not a week's batch at once."""
    avoid_titles = _existing_titles(track.key) + _existing_titles(f"{track.key}/long")
    prompt = PROMPT_TEMPLATE_LONG.format(
        label=track.label, story_guidance=track.story_guidance, count=count,
        avoid_titles=", ".join(f'"{t}"' for t in avoid_titles) if avoid_titles else "(none yet)",
        scene_structure_rules=SCENE_STRUCTURE_RULES_LONG,
    )
    raw = _call_gemini_raw(prompt, LONG_RESPONSE_SCHEMA)

    scripts = []
    for i, story_data in enumerate(raw.get("stories", [])):
        try:
            scripts.append(validate(story_data))
        except ScriptValidationError as e:
            print(f"[story_writer] skipping long-form story {i} for track '{track.key}': {e}")

    if not scripts:
        raise RuntimeError(f"Gemini returned no valid long-form stories for track '{track.key}'")
    return scripts


EPISODE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "series_title": {"type": "string"},
        "episode_subtitle": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "character_reference": {"type": "string"},
        "scenes": {"type": "array", "items": SCENE_SCHEMA},
        "story_so_far_update": {"type": "string"},
    },
    "required": [
        "id", "series_title", "episode_subtitle", "description", "tags",
        "character_reference", "scenes", "story_so_far_update",
    ],
}

EPISODE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"episode": EPISODE_SCHEMA},
    "required": ["episode"],
}

FIRST_EPISODE_PROMPT_TEMPLATE = """You are starting a new ONGOING serialized short-form video series for a \
YouTube Shorts channel, for the "{label}" audience.

SERIES CONCEPT
{story_guidance}

This is EPISODE 1 of an ongoing series — establish a compelling ORIGINAL \
protagonist and premise that can sustain many future episodes (an \
unfolding power, a mystery, a rival, a larger threat). End this episode on \
a hook or cliffhanger that makes viewers want the next part, not a \
resolved ending.

EPISODE STRUCTURE
{scene_structure_rules}

Also write:
- "series_title": a short, punchy name for the SERIES itself (2-4 words, \
e.g. "Void Pulse") — this gets reused as the title prefix for every future \
episode, so make it distinctive and memorable, not generic.
- "episode_subtitle": a short (2-5 word) subtitle for THIS episode only \
(e.g. "The Awakening").
- "story_so_far_update": a 2-4 sentence plain-prose recap of what happened \
in this episode. It will be given back to you as context when writing \
episode 2, so make it self-contained and concrete (who, what changed, \
what's unresolved).

Output must match the provided JSON schema exactly.
"""

CONTINUING_EPISODE_PROMPT_TEMPLATE = """You are continuing an ONGOING serialized short-form video series for a \
YouTube Shorts channel, for the "{label}" audience.

SERIES CONCEPT
{story_guidance}

STORY SO FAR (everything that has happened up to now)
{story_so_far}

PROTAGONIST (already established — keep this consistent, do not \
redescribe or change any detail)
{character_reference}

This is EPISODE {episode_number} of the series "{series_title}". Continue \
directly from where the story left off — do not restart, re-explain the \
premise, or repeat earlier scenes. Escalate the story and end this episode \
on a new hook or cliffhanger.

EPISODE STRUCTURE
{scene_structure_rules}

Also write:
- "character_reference": repeat the PROTAGONIST description above EXACTLY \
as given, unchanged.
- "episode_subtitle": a short (2-5 word) subtitle for THIS episode only \
(e.g. "The Rival Emerges") — do NOT include the series name or the word \
"Episode", just the subtitle.
- "story_so_far_update": an updated 2-4 sentence plain-prose recap \
covering the FULL story through this episode (not just this episode \
alone) — this replaces the previous recap and will be given back to you \
for episode {next_episode_number}.

Output must match the provided JSON schema exactly.
"""


def _series_state_path(track_key: str) -> Path:
    return SERIES_STATE_DIR / f"{track_key}.json"


def _load_series_state(track_key: str) -> dict | None:
    path = _series_state_path(track_key)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _save_series_state(track_key: str, state: dict) -> None:
    path = _series_state_path(track_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def generate_next_episode(track: Track) -> Script:
    """Generates the next episode of an ongoing serialized track (see
    Track.serialized). Episode 1 establishes an original protagonist +
    premise; every later episode is written with the locked
    character_reference and running story-so-far recap fed back in, so
    Gemini continues the plot instead of contradicting itself. Persists the
    updated series state (scripts_queue/series_state/<track>.json) on
    success."""
    state = _load_series_state(track.key)

    if state is None:
        episode_number = 1
        prompt = FIRST_EPISODE_PROMPT_TEMPLATE.format(
            label=track.label, story_guidance=track.story_guidance,
            scene_structure_rules=SCENE_STRUCTURE_RULES,
        )
    else:
        episode_number = state["episode_number"] + 1
        prompt = CONTINUING_EPISODE_PROMPT_TEMPLATE.format(
            label=track.label, story_guidance=track.story_guidance,
            story_so_far=state["story_so_far"], character_reference=state["character_reference"],
            series_title=state["series_title"],
            episode_number=episode_number, next_episode_number=episode_number + 1,
            scene_structure_rules=SCENE_STRUCTURE_RULES,
        )

    raw = _call_gemini_raw(prompt, EPISODE_RESPONSE_SCHEMA)
    episode_data = raw["episode"]

    # Force the locked character description and series title rather than
    # trusting the model to repeat them verbatim — guarantees consistency
    # even if it paraphrases (observed drifting the series name episode to
    # episode when left to the model alone, e.g. "Void Pulse" -> "Circuit
    # Breaker" -> "Energy Pulse" for the same ongoing story).
    character_reference = (
        state["character_reference"] if state is not None else episode_data["character_reference"]
    )
    series_title = state["series_title"] if state is not None else episode_data["series_title"]
    episode_subtitle = episode_data.pop("episode_subtitle")

    episode_data["character_reference"] = character_reference
    episode_data["episode_number"] = episode_number
    episode_data["title"] = f"{series_title} — Episode {episode_number}: {episode_subtitle}"
    del episode_data["series_title"]

    story_so_far_update = episode_data.pop("story_so_far_update")
    script = validate(episode_data)

    _save_series_state(track.key, {
        "episode_number": episode_number,
        "character_reference": character_reference,
        "series_title": series_title,
        "story_so_far": story_so_far_update,
    })
    return script


CHARACTER_REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {"character_reference": {"type": "string"}},
    "required": ["character_reference"],
}

BACKFILL_PROMPT_TEMPLATE = """Read this short story, written for a narrated video, and write ONE \
detailed, reusable physical description of its main protagonist, to be \
used as a consistent reference for AI image generation across every scene.

Describe species/build, hair or fur color, clothing, and 1-2 distinguishing \
features (e.g. "a small cream-colored rabbit with floppy brown-tipped ears \
and a red neckerchief"). Keep it concise (1-2 sentences) and concrete — \
avoid abstract or emotional language, and don't describe an action or pose.

If the story genuinely has no single recurring character (e.g. it's about \
a place or an event with no protagonist), return an empty string.

STORY TITLE: {title}

SCENES:
{scenes_text}
"""


def _build_backfill_prompt(script: Script) -> str:
    scenes_text = "\n".join(f"- {s.on_screen_text}: {s.narration}" for s in script.scenes)
    return BACKFILL_PROMPT_TEMPLATE.format(title=script.title, scenes_text=scenes_text)


def generate_character_reference(script: Script) -> str:
    """Retrofits a character_reference for a story written before that
    field existed, by having Gemini read the finished story back."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": _build_backfill_prompt(script)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": CHARACTER_REFERENCE_SCHEMA,
        },
    }
    response = requests.post(
        url, params={"key": GEMINI_API_KEY}, json=body, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text).get("character_reference", "")


def backfill_character_references(track_key: str) -> list[Path]:
    """Fills in character_reference for every pending script of this track
    that doesn't have one yet (written before the field existed). Only
    touches pending/ — already-used scripts won't be produced again, so
    there's nothing to gain backfilling them. Returns the paths updated."""
    pending_dir = QUEUE_PENDING_DIR / track_key
    updated = []
    for path in sorted(pending_dir.glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("character_reference", ""):
            continue
        script = validate(data)

        for attempt in range(BACKFILL_MAX_RETRIES + 1):
            try:
                character_reference = generate_character_reference(script)
                break
            except requests.exceptions.HTTPError as e:
                is_rate_limited = e.response is not None and e.response.status_code == 429
                if is_rate_limited and attempt < BACKFILL_MAX_RETRIES:
                    print(f"[story_writer] rate limited backfilling '{script.id}', "
                          f"waiting {BACKFILL_RATE_LIMIT_RETRY_SECONDS}s...")
                    time.sleep(BACKFILL_RATE_LIMIT_RETRY_SECONDS)
                else:
                    raise
        else:
            raise RuntimeError(f"backfill failed for '{script.id}' after {BACKFILL_MAX_RETRIES} retries")

        data["character_reference"] = character_reference
        path.write_text(json.dumps(data, indent=2))
        updated.append(path)
        time.sleep(BACKFILL_PACING_SECONDS)
    return updated
