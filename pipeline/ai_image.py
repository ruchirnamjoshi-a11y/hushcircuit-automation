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

from pipeline.config import AI_IMAGE_MODEL, CF_ACCOUNT_ID, CF_API_TOKEN
from pipeline.scripts import Scene
from pipeline.textcard import make_gradient_image

# FLUX.1-schnell has no negative-prompt/CFG support on Workers AI, so "no text"
# instructions only reduce (not eliminate) on-image text — and it reliably
# renders garbled text when the prompt reads like ad copy/a banner headline.
# Front-loading the instruction and describing on_screen_text as a concept
# (never quoting it verbatim, never including narration) empirically produced
# clean icon-only output; feeding it a full sentence brought the text back
# even with substitutions applied.
STYLE_PREFIX = (
    "Icon design with absolutely no text, no letters, no words anywhere in "
    "the image. A single flat-vector icon symbolizing "
)
STYLE_SUFFIX = (
    ", flat vector illustration, clean geometric shapes, warm gold and deep "
    "navy color palette, minimalist tech aesthetic, isolated icon "
    "composition on plain background"
)

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
REQUEST_TIMEOUT_SECONDS = 60


def _sanitize_concept(text: str) -> str:
    concept = text.lower()
    for bad, good in TRIGGER_WORD_SUBSTITUTIONS.items():
        concept = re.sub(rf"\b{bad}\b", good, concept)
    return concept


def build_prompt(scene: Scene) -> str:
    concept = _sanitize_concept(scene.on_screen_text)
    return f"{STYLE_PREFIX}{concept}{STYLE_SUFFIX}"


def _fit_to_resolution(image: Image.Image, resolution: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), resolution, Image.LANCZOS)


def _call_cloudflare(prompt: str) -> Image.Image:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{AI_IMAGE_MODEL}"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
        json={"prompt": prompt},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare Workers AI error: {data.get('errors')}")
    image_b64 = data["result"]["image"]
    return Image.open(BytesIO(base64.b64decode(image_b64)))


def generate_scene_image_raw(
    scene: Scene,
    out_path: Path,
    retries: int = 2,
) -> tuple[Path, bool]:
    """Generates one illustration for a scene via Cloudflare Workers AI, saved
    at its native resolution (no cropping yet) — call fit_scene_image() per
    output format (long-form 16:9, Short 9:16) afterward so a scene only
    costs one API call regardless of how many formats use it. Falls back to
    the brand gradient if credentials are unset or every attempt fails.

    Returns (path, used_fallback) — callers use used_fallback to decide
    whether this scene should get the gradient's drifting orb treatment
    (generate_background_clip) instead of the plain AI-image zoom
    (generate_image_background_clip), which would otherwise leave a fallback
    scene looking flatter than a real AI illustration or the original
    all-gradient design.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if CF_ACCOUNT_ID and CF_API_TOKEN:
        prompt = build_prompt(scene)
        for attempt in range(retries + 1):
            try:
                image = _call_cloudflare(prompt)
                image.convert("RGB").save(out_path)
                return out_path, False
            except Exception as e:
                print(f"[ai_image] attempt {attempt + 1}/{retries + 1} failed for scene "
                      f"'{scene.on_screen_text}': {e}")
                if attempt < retries:
                    time.sleep(RETRY_DELAY_SECONDS)

        print(f"[ai_image] all attempts failed for scene '{scene.on_screen_text}', "
              "falling back to brand gradient")
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
