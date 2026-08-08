import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
QUEUE_PENDING_DIR = ROOT_DIR / "scripts_queue" / "pending"
QUEUE_USED_DIR = ROOT_DIR / "scripts_queue" / "used"
MUSIC_DIR = ROOT_DIR / "assets" / "music"
ANALYTICS_REPORTS_DIR = ROOT_DIR / "analytics" / "reports"
SECRETS_DIR = ROOT_DIR / "secrets"

NICHE = os.environ.get("NICHE", "Daily stories for every age")


@dataclass
class Track:
    """One of the channel's daily story tracks. Each track gets its own
    queue subdirectory (scripts_queue/pending/<key>/), TTS voice, AI
    illustration style, and YouTube upload settings — one video per track,
    per day, rather than one channel-wide "niche"."""

    key: str
    label: str
    voice: str
    image_style_prefix: str
    image_style_suffix: str
    made_for_kids: bool
    category_id: str
    # Tone/theme guidance for pipeline.story_writer's Gemini prompt — what
    # kind of stories this audience wants, distinct from image_style_*
    # (which only shapes the illustration prompt).
    story_guidance: str = ""
    extra_tags: list[str] = field(default_factory=list)


# Category IDs are YouTube's fixed video categories (24 = Entertainment,
# a safe generic fit for narrated story content across all four tracks).
CATEGORY_ENTERTAINMENT = "24"

TRACKS: dict[str, Track] = {
    "kids": Track(
        key="kids",
        label="Kids",
        voice="en-GB-SoniaNeural",
        image_style_prefix=(
            "Children's picture book illustration with absolutely no text, "
            "no letters, no words anywhere in the image. A single warm, "
            "soft watercolor-style illustration of "
        ),
        image_style_suffix=(
            ", gentle pastel color palette, whimsical storybook art style, "
            "soft rounded shapes, cheerful and cozy mood, isolated scene on "
            "simple background"
        ),
        made_for_kids=True,
        category_id=CATEGORY_ENTERTAINMENT,
        story_guidance=(
            "Gentle bedtime/moral stories for young children, ages roughly "
            "4-9. A small, relatable animal or child protagonist faces a "
            "simple, low-stakes problem (fear, feeling small/left out, a "
            "mistake) and resolves it through kindness, courage, or a "
            "small act of help — never violence or real danger. Warm, "
            "simple language. End on a clear, comforting moral."
        ),
        extra_tags=["bedtime story", "kids story", "story for kids"],
    ),
    "teens": Track(
        key="teens",
        label="Teens & Young Adults",
        voice="en-US-AriaNeural",
        # NOTE when writing this track's Scene.visual descriptions: avoid
        # building exteriors/institutional signage (school entrances, gym
        # doors, storefronts) — FLUX reliably renders garbled signage text
        # for those even with the no-text instruction. Character-focused
        # close-ups and nature/park/interior-without-signage scenes render
        # clean every time (validated).
        image_style_prefix=(
            "Illustration with absolutely no text, no letters, no words, "
            "no signs, no banners anywhere in the image. A single "
            "close-up character-focused vibrant modern flat-illustration "
            "of "
        ),
        image_style_suffix=(
            ", bold saturated colors, contemporary digital illustration "
            "style, shallow depth of field with a softly blurred "
            "background, dynamic composition"
        ),
        made_for_kids=False,
        category_id=CATEGORY_ENTERTAINMENT,
        story_guidance=(
            "Relatable coming-of-age drama for teens/young adults: first "
            "attempts, social anxiety, friendship, a first "
            "failure-then-comeback, identity, ambition. A single teen or "
            "young-adult protagonist, present-day setting, emotionally "
            "honest but not heavy-handed. End on genuine, earned "
            "confidence or connection — not a moralizing lecture."
        ),
        extra_tags=["short story", "ya fiction", "relatable story"],
    ),
    "adults": Track(
        key="adults",
        label="Adults & Middle-Aged",
        voice="en-US-GuyNeural",
        image_style_prefix=(
            "Warm cinematic illustration with absolutely no text, no "
            "letters, no words anywhere in the image. A single evocative "
            "scene of "
        ),
        image_style_suffix=(
            ", muted warm color palette, painterly editorial illustration "
            "style, reflective mood, isolated scene on simple background"
        ),
        made_for_kids=False,
        category_id=CATEGORY_ENTERTAINMENT,
        story_guidance=(
            "Reflective life-crossroads stories for adults/middle-aged "
            "viewers: career changes, parenthood, loss, second chances, "
            "quiet regrets, unexpected new beginnings. A single adult "
            "protagonist facing a real, grounded decision or realization "
            "— no fantasy elements. Emotionally resonant, understated, "
            "hopeful without being saccharine."
        ),
        extra_tags=["short story", "life story", "story time"],
    ),
    "women": Track(
        key="women",
        label="Women",
        voice="en-US-JennyNeural",
        image_style_prefix=(
            "Elegant soft illustration with absolutely no text, no "
            "letters, no words anywhere in the image. A single graceful "
            "scene of "
        ),
        image_style_suffix=(
            ", soft rose-gold color palette, refined modern illustration "
            "style, warm empowering mood, isolated scene on simple "
            "background"
        ),
        made_for_kids=False,
        category_id=CATEGORY_ENTERTAINMENT,
        story_guidance=(
            "Empowerment/resilience stories centered on a woman "
            "protagonist: choosing herself, setting a boundary, pursuing "
            "a suppressed dream, quiet strength through a hard moment, "
            "self-worth outside others' expectations. Grounded, "
            "real-world settings — no fantasy elements. Warm and "
            "affirming without being preachy."
        ),
        extra_tags=["short story", "women's story", "inspiring story"],
    ),
}

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

# Hosted AI image generation: Cloudflare Workers AI, running FLUX.1-schnell.
# Genuinely free tier (10,000 Neurons/day, no credit card) — comfortably
# covers a daily video's ~8 scene images with huge headroom to spare.
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
AI_IMAGE_MODEL = os.environ.get("AI_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")

# Weekly story generation: Gemini API, free tier. "gemini-flash-latest" is an
# alias Google keeps pointed at their current recommended flash model, so it
# doesn't need updating when a specific dated model (e.g. gemini-2.5-flash)
# gets sunset for new callers.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-GuyNeural")

# One OAuth Desktop client (Google Cloud Console) is shared across all
# channels — it's the OAuth token per channel that differs, not the client.
YOUTUBE_CLIENT_SECRET_FILE = os.environ.get(
    "YOUTUBE_CLIENT_SECRET_FILE", str(SECRETS_DIR / "client_secret.json")
)
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def youtube_token_path(track_key: str) -> Path:
    """Each track uploads to its own YouTube channel, so each needs its own
    cached OAuth token — see README's multi-channel setup section.
    Override per-track via YOUTUBE_TOKEN_FILE_<TRACK> (e.g.
    YOUTUBE_TOKEN_FILE_KIDS, used in GitHub Actions); defaults to
    secrets/youtube_token_<track>.json for local one-time OAuth setup via
    `python -m pipeline.upload --auth-setup --track <track>`."""
    override = os.environ.get(f"YOUTUBE_TOKEN_FILE_{track_key.upper()}")
    if override:
        return Path(override)
    return SECRETS_DIR / f"youtube_token_{track_key}.json"

# Shorts-only pipeline: every story is one vertical video. YouTube treats
# vertical #shorts-tagged videos up to 3 minutes as eligible for the Shorts
# shelf, and our ~8-scene stories run ~70-90s, so 180s is a generous safety
# ceiling that only trims content on an unusually long script — normal
# stories are never cut. LONG_FORM_RESOLUTION is kept for pipeline/assemble.py
# (retained but unused by default — see README), matching pipeline/broll.py.
LONG_FORM_RESOLUTION = (1920, 1080)
SHORT_RESOLUTION = (1080, 1920)
SHORT_MAX_SECONDS = 180

THUMBNAIL_RESOLUTION = (1280, 720)

MUSIC_VOLUME_DB = -22

for _dir in (
    OUTPUT_DIR,
    QUEUE_PENDING_DIR,
    QUEUE_USED_DIR,
    MUSIC_DIR,
    ANALYTICS_REPORTS_DIR,
    SECRETS_DIR,
    *(QUEUE_PENDING_DIR / track for track in TRACKS),
    *(QUEUE_USED_DIR / track for track in TRACKS),
):
    _dir.mkdir(parents=True, exist_ok=True)
