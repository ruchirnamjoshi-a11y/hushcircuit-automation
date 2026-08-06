import os
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

NICHE = os.environ.get("NICHE", "AI tools & tips")

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-GuyNeural")

YOUTUBE_CLIENT_SECRET_FILE = os.environ.get(
    "YOUTUBE_CLIENT_SECRET_FILE", str(SECRETS_DIR / "client_secret.json")
)
YOUTUBE_TOKEN_FILE = os.environ.get(
    "YOUTUBE_TOKEN_FILE", str(SECRETS_DIR / "youtube_token.json")
)
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# Long-form target ~7 min; Shorts must stay under 60s.
LONG_FORM_RESOLUTION = (1920, 1080)
SHORT_RESOLUTION = (1080, 1920)
SHORT_MAX_SECONDS = 58

THUMBNAIL_RESOLUTION = (1280, 720)

MUSIC_VOLUME_DB = -22

for _dir in (
    OUTPUT_DIR,
    QUEUE_PENDING_DIR,
    QUEUE_USED_DIR,
    MUSIC_DIR,
    ANALYTICS_REPORTS_DIR,
    SECRETS_DIR,
):
    _dir.mkdir(parents=True, exist_ok=True)
