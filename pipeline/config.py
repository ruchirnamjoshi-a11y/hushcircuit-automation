import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
QUEUE_PENDING_DIR = ROOT_DIR / "scripts_queue" / "pending"
QUEUE_USED_DIR = ROOT_DIR / "scripts_queue" / "used"
# Persists the locked character_reference + running "story so far" recap for
# serialized tracks (see Track.serialized), one file per track key.
SERIES_STATE_DIR = ROOT_DIR / "scripts_queue" / "series_state"
MUSIC_DIR = ROOT_DIR / "assets" / "music"
ANALYTICS_REPORTS_DIR = ROOT_DIR / "analytics" / "reports"
SECRETS_DIR = ROOT_DIR / "secrets"
# Hand-authored canvas-animation "pieces" (see pipeline.canvas_video) for the
# math_explainers track — math_pieces/<piece_id>/piece.html, one directory
# per piece. Not AI-generated visuals: real code, rendered deterministically.
MATH_PIECES_DIR = ROOT_DIR / "math_pieces"

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
    # "edge" (default) uses the single `voice` above via edge-tts. "gemini"
    # uses Gemini's native TTS instead (see pipeline.tts.synthesize_script_
    # for_track) — no word-level timestamps, so only usable with
    # burn_captions=False, but noticeably more natural-sounding. `voice` is
    # ignored when this is "gemini"; tts_voices is used instead.
    tts_provider: str = "edge"
    # One narrator voice is picked per script (deterministically, so re-runs
    # are stable) and used for every scene in that script — consistent
    # within an episode, varied across episodes/days.
    tts_voices: list[str] = field(default_factory=list)
    # Tone/theme guidance for pipeline.story_writer's Gemini prompt — what
    # kind of stories this audience wants, distinct from image_style_*
    # (which only shapes the illustration prompt).
    story_guidance: str = ""
    extra_tags: list[str] = field(default_factory=list)
    # False for non-Latin-script tracks (e.g. Hindi) where we don't have a
    # matching caption font wired up — video + audio only, no captions/badge
    # burned in, rather than rendering wrong-language or missing-glyph text.
    burn_captions: bool = True
    # True for tracks that tell ONE ongoing story across daily episodes
    # (persistent character + running recap, see pipeline.story_writer.
    # generate_next_episode) rather than a fresh standalone story per video.
    serialized: bool = False
    # When set, this track is a language variant of another track's stories
    # (e.g. "kids" -> Hindi) rather than its own independent content. The
    # Hindi script (Script.source_script_id pointing at the English script's
    # id) reuses the primary track's already-generated scene images instead
    # of calling Cloudflare again — narration/captions/upload are the only
    # per-language work, so producing a second language costs no extra
    # image-generation quota. See run_daily.py.
    shares_images_with: str = ""
    # Produces both the full-length long-form video and a trimmed Shorts
    # highlight cut from the same generated images/audio (see run_daily.py).
    # False keeps the original single-Short-only behavior.
    produce_long_form: bool = False
    # "story" (default): the AI-illustrated narrated-story pipeline (Scene/
    # Script schema, pipeline.ai_image, run_daily.run_track). "canvas": a
    # hand-authored canvas-animation piece (pipeline.canvas_video,
    # run_daily.run_math_track) — no AI images at all, real code rendered
    # deterministically. image_style_prefix/suffix are unused for "canvas"
    # tracks; pass empty strings.
    content_type: str = "story"
    # How many videos/day this track's daily-limit gate (pipeline.state)
    # allows before skipping for the rest of the day. 1 for every existing
    # track; math_explainers uses 2 since each piece is short (well under
    # the Shorts cap) and there's no separate long-form cut to pad out the
    # channel's daily upload count the way kids/kids-Hindi's second upload
    # does. Sustaining N/day still requires N pieces queued that day —
    # this only raises the ceiling, it doesn't create content.
    videos_per_day: int = 1
    # True skips this track entirely in run_daily.run() (see the check at
    # the top of the track loop), before it even looks at its queue or
    # token. A deliberate, visible pause — not the same thing as "no
    # YouTube token set" (teens/adults/women/hero_saga skip that way today,
    # implicitly) — for a track whose channel is meant to keep existing but
    # not produce right now. Flip back to False to resume; nothing about
    # the track's config/content is touched.
    paused: bool = False


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
        produce_long_form=True,
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
        paused=True,
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
        paused=True,
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
        paused=True,
    ),
    "hindi_mythology": Track(
        # Key/secret filenames kept as "hindi_mythology" (retiring the old
        # mythology content, not the channel/OAuth token — see README) to
        # avoid renaming the GitHub Actions secret and local token file.
        # This channel now posts the Hindi-language version of the "kids"
        # track's stories instead of Indian mythology episodes.
        key="hindi_mythology",
        label="Kids Stories (Hindi)",
        voice="hi-IN-MadhurNeural",
        # gemini-2.5-flash-preview-tts and gemini-3.1-flash-tts-preview both
        # turned out to have a hard 10 requests/DAY free-tier cap (confirmed
        # live) — far too tight for a 30+ scene long-form script. edge-tts's
        # Hindi voice has no such limit. tts_voices kept here, dormant, in
        # case a model with a workable daily quota shows up.
        tts_voices=["Kore", "Puck", "Aoede"],
        # Unused while shares_images_with="kids" reuses the English track's
        # already-generated images (see run_daily.py) — kept matching kids'
        # style as a fallback for the rare case that reuse isn't available.
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
            "The Hindi-language version of a 'kids' track story — same "
            "plot, characters, and moral, translated (not a stiff "
            "word-for-word translation — natural spoken Hindi) into "
            "Devanagari script for title, description, tags, and every "
            "scene's narration/on_screen_text. `visual` fields must stay "
            "IDENTICAL to the English script's (word-for-word) so the "
            "shared illustrations still match this scene's narration."
        ),
        extra_tags=["hindi story", "kids story", "bedtime story", "moral story in hindi"],
        # No Devanagari-glyph caption font wired up yet — video + audio
        # only, rather than rendering missing-glyph boxes over the video.
        burn_captions=False,
        shares_images_with="kids",
        produce_long_form=True,
        # Paused: was starving math_explainers (last in TRACKS iteration
        # order) whenever this track's slow path -- independently
        # regenerating a whole story's scene images when the sibling
        # "kids" script wasn't produced in the same run -- ate the entire
        # CI job's time budget. Flip back to False to resume; nothing
        # about its content/config is touched.
        paused=True,
    ),
    "hero_saga": Track(
        key="hero_saga",
        label="Original Hero Saga",
        voice="en-US-ChristopherNeural",
        image_style_prefix=(
            "Bold cinematic comic-book illustration with absolutely no "
            "text, no letters, no words, no logos, no signage anywhere in "
            "the image. A single dramatic graphic-novel-style panel of "
        ),
        image_style_suffix=(
            ", dynamic action pose, dramatic high-contrast lighting, bold "
            "ink outlines, vibrant saturated color palette, epic "
            "cinematic composition, isolated scene on simple background"
        ),
        made_for_kids=False,
        category_id=CATEGORY_ENTERTAINMENT,
        story_guidance=(
            "An ONGOING original superhero/action-adventure saga — NOT "
            "any existing franchise or licensed character (no Marvel, DC, "
            "or any recognizable copyrighted hero/villain names, costumes, "
            "or references). Invent a wholly original protagonist with a "
            "distinctive power and a compelling mystery driving the "
            "series (their power's origin, a hidden enemy, a shadowy "
            "organization). Dramatic, high-stakes, cinematic tone — "
            "training a new ability, a tense confrontation, a betrayal, a "
            "narrow escape. Each episode is one chapter of the larger "
            "story, not a self-contained tale."
        ),
        extra_tags=["superhero story", "original story", "action adventure", "animated series", "hero saga"],
        serialized=True,
        paused=True,
    ),
    "math_explainers": Track(
        key="math_explainers",
        label="Fun By Math",
        voice="en-US-ChristopherNeural",
        image_style_prefix="",  # unused -- content_type="canvas", see pipeline.canvas_video
        image_style_suffix="",
        made_for_kids=False,
        category_id="27",  # Education
        story_guidance=(
            "Not used for automated generation yet -- each piece is a "
            "hand-authored canvas animation (math_pieces/<id>/piece.html, "
            "built on pipeline/canvas_lib/helpers.js) paired with a short "
            "narration-line script, queued the same way story scripts are. "
            "See run_daily.run_math_track."
        ),
        extra_tags=["math", "cool math facts", "math explained", "learn math"],
        produce_long_form=False,
        content_type="canvas",
        videos_per_day=2,
    ),
    "manifestation": Track(
        key="manifestation",
        label="Manifest & Money Affirmations",
        voice="",  # unused -- content_type="song", no narration TTS
        image_style_prefix="",  # unused -- see pipeline.manifestation
        image_style_suffix="",
        made_for_kids=False,
        category_id="10",  # Music
        story_guidance=(
            "Not the narrated-story pipeline -- daily AI-generated "
            "affirmation song (Gemini-written lyrics, ACE-Step music, a "
            "looped short AI DJ-dancing clip as the visual). See "
            "pipeline.manifestation. Paused until that generation path is "
            "built and end-to-end verified; the OAuth channel is being set "
            "up ahead of that."
        ),
        extra_tags=["manifestation", "affirmations", "law of attraction", "abundance", "money affirmations"],
        produce_long_form=False,
        content_type="song",
        paused=True,
    ),
}

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

# Hosted AI image generation: Cloudflare Workers AI, running FLUX.1-schnell.
# Genuinely free tier (10,000 Neurons/day, no credit card) — comfortably
# covers a daily video's ~8 scene images with huge headroom to spare.
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
# flux-2-klein-4b (not flux-1-schnell) — supports a real reference input
# image (see pipeline.ai_image), so scenes 2+ can be conditioned on scene
# 1's actual generated image instead of only a text description + shared
# seed. Verified in real testing to hold character details (armor, crown,
# skin tone) far better across dramatically different poses/settings than
# the old text-only approach. Costs about the same per image (~5.37 vs
# ~4.80 neurons/tile) — comfortably inside the free 10,000/day budget even
# with reference-image input tiles added on top.
AI_IMAGE_MODEL = os.environ.get("AI_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")

# Weekly story generation: Gemini API, free tier. "gemini-flash-latest" is an
# alias Google keeps pointed at their current recommended flash model, so it
# doesn't need updating when a specific dated model (e.g. gemini-2.5-flash)
# gets sunset for new callers.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Hugging Face account token -- unlocks the larger authenticated ZeroGPU
# quota pool (vs. anonymous) for pipeline.manifestation_video's ACE-Step
# song generation. Free account, no billing; see pipeline.manifestation_video
# for the real observed daily-quota numbers.
HF_TOKEN = os.environ.get("HF_TOKEN", "")

TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-GuyNeural")

# Gemini's native TTS (Track.tts_provider="gemini") — noticeably more
# natural-sounding than edge-tts for languages like Hindi where edge-tts
# only exposes two older Azure voices. gemini-2.5-flash-preview-tts's free
# tier has a hard 10 REQUESTS/DAY cap per project (confirmed empirically
# via a live 429's quotaValue — Google doesn't publish exact numbers, and
# this is far tighter than its separate 3/minute limit) — too tight for an
# 8-scene script with zero retry margin. gemini-3.1-flash-tts-preview uses
# an independent quota bucket (different model = different quotaId) and
# is used instead.
GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")

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

# Every story is one vertical (9:16) video — SHORT_RESOLUTION is used for
# both the Shorts cut AND the long-form cut (Track.produce_long_form) rather
# than reintroducing a separate 16:9 pipeline. YouTube treats vertical
# #shorts-tagged videos up to 3 minutes as eligible for the Shorts shelf, so
# 180s caps the trimmed highlight cut; the long-form cut has no cap (full
# story). LONG_FORM_RESOLUTION is kept for pipeline/assemble.py's legacy
# 16:9 path (retained but unused by default — see README), matching
# pipeline/broll.py.
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
    SERIES_STATE_DIR,
    *(QUEUE_PENDING_DIR / track for track in TRACKS),
    *(QUEUE_USED_DIR / track for track in TRACKS),
):
    _dir.mkdir(parents=True, exist_ok=True)
