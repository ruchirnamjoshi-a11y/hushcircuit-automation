"""Stage 3: AI scene illustrations via Cloudflare Workers AI (FLUX.1-schnell).
Genuinely free tier — 10,000 Neurons/day, no credit card, ~170+ images/day
worth of headroom for an ~8-scene daily video. Falls back to the plain brand
gradient (pipeline.textcard) if the API call fails for any reason — missing
credentials, rate limit, network hiccup — so a transient issue never breaks
a whole day's video.
"""

from __future__ import annotations

import base64
import re
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps

from pipeline.config import AI_IMAGE_MODEL, CF_ACCOUNT_ID, CF_API_TOKEN, Track
from pipeline.scripts import Scene
from pipeline.textcard import make_gradient_image

# FLUX.1-schnell has no negative-prompt/CFG support on Workers AI, so "no text"
# instructions only reduce (not eliminate) on-image text — and it reliably
# renders garbled text when the prompt reads like ad copy/a banner headline.
# Front-loading the instruction and describing the scene as a concept (never
# quoting on_screen_text verbatim, never including full narration sentences)
# empirically produced clean output; feeding it a full sentence brought the
# text back even with substitutions applied. Each Track supplies its own
# image_style_prefix/suffix (see pipeline.config) so illustration style
# varies by audience while this safety technique stays shared.

# These specific words strongly anchor FLUX toward badge/button training
# images ("FREE" stickers, "SUBSCRIBE" buttons) and it renders them as text
# even with the no-text instruction. Swapping in synonyms sidesteps that.
TRIGGER_WORD_SUBSTITUTIONS = {
    "free": "no-cost",
    "subscribe": "follow",
    "subscribing": "following",
    "subscribed": "followed",
}

RETRY_DELAY_SECONDS = 2
RATE_LIMIT_RETRY_DELAY_SECONDS = 20
INTER_REQUEST_DELAY_SECONDS = 3  # paces successive calls so a 4-track run
# (~32 images) doesn't burst past Cloudflare's per-minute rate limit even
# though the 10k Neurons/day budget has plenty of headroom left.
REQUEST_TIMEOUT_SECONDS = 60


def _sanitize_concept(text: str) -> str:
    concept = text.lower()
    for bad, good in TRIGGER_WORD_SUBSTITUTIONS.items():
        concept = re.sub(rf"\b{bad}\b", good, concept)
    return concept


def build_prompt(scene: Scene, track: Track) -> str:
    concept = _sanitize_concept(scene.image_concept)
    return f"{track.image_style_prefix}{concept}{track.image_style_suffix}"


def _fit_to_resolution(image: Image.Image, resolution: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), resolution, Image.LANCZOS)


class CloudflareQuotaExhausted(RuntimeError):
    """Today's free 10,000 Neurons/day allocation is used up (Cloudflare
    error code 4006) — distinct from a transient rate limit: retrying won't
    help until the daily reset, so callers should fall back immediately
    instead of burning time on backoff retries."""


def _call_cloudflare(prompt: str) -> Image.Image:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{AI_IMAGE_MODEL}"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
        json={"prompt": prompt},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code == 429:
        try:
            errors = response.json().get("errors") or []
        except ValueError:
            errors = []
        if any(err.get("code") == 4006 for err in errors):
            raise CloudflareQuotaExhausted(f"daily free Neurons allocation exhausted: {errors}")
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare Workers AI error: {data.get('errors')}")
    image_b64 = data["result"]["image"]
    return Image.open(BytesIO(base64.b64decode(image_b64)))


def generate_scene_image_raw(
    scene: Scene,
    out_path: Path,
    track: Track,
    retries: int = 2,
    raise_on_quota_exhausted: bool = False,
) -> tuple[Path, bool]:
    """Generates one illustration for a scene via Cloudflare Workers AI, saved
    at its native resolution (no cropping yet) — call fit_scene_image() per
    output format (long-form 16:9, Short 9:16) afterward so a scene only
    costs one API call regardless of how many formats use it. Falls back to
    the brand gradient if credentials are unset or every attempt fails.

    `track` selects the illustration style (kids/teens/adults/women — see
    pipeline.config.TRACKS).

    `raise_on_quota_exhausted`: when True, a CloudflareQuotaExhausted lets
    the exception propagate instead of falling back to the gradient.
    run_daily.py sets this for a track's first scene only, using it as a
    real-work "probe" — if quota is exhausted, the whole video would end up
    all-gradient anyway, and the caller would rather abort the track (leave
    its script in the queue) and let a later scheduled run retry once
    quota resets than publish a placeholder-only video.

    Returns (path, used_fallback) — callers use used_fallback to decide
    whether this scene should get the gradient's drifting orb treatment
    (generate_background_clip) instead of the plain AI-image zoom
    (generate_image_background_clip), which would otherwise leave a fallback
    scene looking flatter than a real AI illustration or the original
    all-gradient design.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if CF_ACCOUNT_ID and CF_API_TOKEN:
        prompt = build_prompt(scene, track)
        for attempt in range(retries + 1):
            try:
                image = _call_cloudflare(prompt)
                image.convert("RGB").save(out_path)
                time.sleep(INTER_REQUEST_DELAY_SECONDS)
                return out_path, False
            except CloudflareQuotaExhausted as e:
                if raise_on_quota_exhausted:
                    raise
                # No point retrying or pacing further calls this run —
                # every remaining scene (and track) will hit the same wall
                # until Cloudflare's daily reset.
                print(f"[ai_image] quota exhausted for scene '{scene.on_screen_text}': {e}")
                break
            except Exception as e:
                is_rate_limited = getattr(e, "response", None) is not None and e.response.status_code == 429
                print(f"[ai_image] attempt {attempt + 1}/{retries + 1} failed for scene "
                      f"'{scene.on_screen_text}': {e}")
                if attempt < retries:
                    if is_rate_limited:
                        retry_after = e.response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else RATE_LIMIT_RETRY_DELAY_SECONDS
                    else:
                        delay = RETRY_DELAY_SECONDS
                    time.sleep(delay)
        else:
            print(f"[ai_image] all attempts failed for scene '{scene.on_screen_text}', "
                  "falling back to brand gradient")
            time.sleep(INTER_REQUEST_DELAY_SECONDS)
    else:
        print("[ai_image] CF_ACCOUNT_ID/CF_API_TOKEN not set, using brand gradient")

    # Neutral square-ish fallback canvas; fit_scene_image crops it per format same as a real image.
    make_gradient_image(out_path, (1024, 1024))
    return out_path, True


def fit_scene_image(raw_path: Path, out_path: Path, resolution: tuple[int, int]) -> Path:
    """Crops/scales an already-generated raw scene image to a specific
    output resolution — no API call, just local image processing."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(raw_path)
    _fit_to_resolution(image, resolution).save(out_path)
    return out_path
