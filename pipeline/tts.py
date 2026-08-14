"""Stage 2: voiceover synthesis, one file per scene — edge-tts by default,
or Gemini's native TTS for tracks that opt in (see Track.tts_provider).

Synthesizing per-scene (not the whole script at once) keeps each scene's audio
aligned 1:1 with its own b-roll clip in assemble.py.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import subprocess
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

import edge_tts
import requests

from pipeline.config import GEMINI_API_KEY, GEMINI_TTS_MODEL, TTS_VOICE, Track
from pipeline.scripts import Script

HUNDRED_NS_PER_SECOND = 1e7
GEMINI_TTS_SAMPLE_RATE = 24000
# Retries are cheap here (a background daily job, generous time budget) —
# a real 8-scene run exhausted 3 retries under genuine sustained
# rate-limiting (quotaValue: 3/minute), so this is generous on purpose
# rather than optimistic.
GEMINI_TTS_MAX_RETRIES = 8
GEMINI_TTS_DEFAULT_RETRY_DELAY_SECONDS = 20.0
# Floor under Google's own reported retryDelay: retrying right at the
# reported boundary can still land inside the same quota window (observed
# live — a short reported delay was sometimes immediately followed by
# another 429), so always wait at least this long.
GEMINI_TTS_MIN_RETRY_DELAY_SECONDS = 15.0
GEMINI_TTS_REQUEST_TIMEOUT_SECONDS = 60


@dataclass
class WordTiming:
    word: str
    start: float
    end: float
    ends_sentence: bool = False


def _sentence_ending_word_indices(text: str) -> set[int]:
    """0-based indices (by whitespace split of `text`) of words that end a
    sentence. edge-tts's WordBoundary events strip punctuation entirely, so
    sentence breaks can't be detected from the synthesized word text itself —
    this reconstructs them from the original narration instead."""
    indices = set()
    word_count = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        n = len(sentence.split())
        if n == 0:
            continue
        word_count += n
        indices.add(word_count - 1)
    return indices


@dataclass
class SceneAudio:
    scene_index: int
    audio_path: Path
    timings_path: Path
    duration: float
    word_timings: list[WordTiming]


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


async def _synthesize(text: str, out_audio: Path, voice: str) -> list[WordTiming]:
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    word_timings: list[WordTiming] = []
    with open(out_audio, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / HUNDRED_NS_PER_SECOND
                duration = chunk["duration"] / HUNDRED_NS_PER_SECOND
                word_timings.append(WordTiming(word=chunk["text"], start=start, end=start + duration))

    # Only trust the reconstructed sentence boundaries if edge-tts emitted
    # exactly one WordBoundary per whitespace-split word — otherwise indices
    # would silently point at the wrong words, so leave everything False.
    if len(word_timings) == len(text.split()):
        sentence_end_indices = _sentence_ending_word_indices(text)
        for i, w in enumerate(word_timings):
            w.ends_sentence = i in sentence_end_indices

    return word_timings


def synthesize_scene(text: str, out_dir: Path, name: str, voice: str = TTS_VOICE) -> SceneAudio:
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"{name}.mp3"
    timings_path = out_dir / f"{name}.words.json"

    word_timings = asyncio.run(_synthesize(text, audio_path, voice))
    if audio_path.stat().st_size == 0:
        raise RuntimeError(f"edge-tts produced an empty audio file for scene '{name}'")

    duration = _probe_duration(audio_path)
    timings_path.write_text(json.dumps([asdict(w) for w in word_timings], indent=2))

    return SceneAudio(
        scene_index=-1,
        audio_path=audio_path,
        timings_path=timings_path,
        duration=duration,
        word_timings=word_timings,
    )


def synthesize_script(script: Script, out_dir: Path, voice: str = TTS_VOICE) -> list[SceneAudio]:
    results = []
    for i, sc in enumerate(script.scenes):
        scene_audio = synthesize_scene(sc.narration, out_dir, f"scene_{i:02d}", voice)
        scene_audio.scene_index = i
        results.append(scene_audio)
    return results


class GeminiTTSError(RuntimeError):
    pass


def _parse_retry_delay(response: requests.Response) -> float | None:
    try:
        for detail in response.json()["error"]["details"]:
            if detail.get("@type", "").endswith("RetryInfo"):
                return float(detail["retryDelay"].rstrip("s"))
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def _call_gemini_tts(text: str, voice: str) -> bytes:
    """Returns raw 16-bit PCM audio (24kHz mono) from Gemini's native TTS.
    Retries on 429 using Google's own suggested retryDelay from the error
    body rather than a guessed backoff — the free tier's real per-project
    limit (confirmed via a live 429's quotaValue, since Google doesn't
    publish exact numbers) is tight enough — 3 requests/minute — that an
    8-scene script needs real retry handling, not just optimistic pacing.
    Also retries on network timeouts/connection errors: a real production
    run hit a bare ReadTimeout on an otherwise-healthy connection (observed
    live, not hypothetical) with no retry at all before this was added."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    for attempt in range(GEMINI_TTS_MAX_RETRIES + 1):
        try:
            response = requests.post(
                url, params={"key": GEMINI_API_KEY}, json=body, timeout=GEMINI_TTS_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as e:
            if attempt < GEMINI_TTS_MAX_RETRIES:
                time.sleep(GEMINI_TTS_DEFAULT_RETRY_DELAY_SECONDS)
                continue
            raise GeminiTTSError(f"Gemini TTS network error after {GEMINI_TTS_MAX_RETRIES} retries: {e}") from e

        if response.status_code == 429:
            if attempt < GEMINI_TTS_MAX_RETRIES:
                delay = _parse_retry_delay(response) or GEMINI_TTS_DEFAULT_RETRY_DELAY_SECONDS
                delay = max(delay, GEMINI_TTS_MIN_RETRY_DELAY_SECONDS)
                time.sleep(delay + 1.0)  # +1s margin over Google's own estimate
                continue
            raise GeminiTTSError(f"Gemini TTS rate-limited after {GEMINI_TTS_MAX_RETRIES} retries")
        response.raise_for_status()
        data = response.json()
        b64_audio = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        return base64.b64decode(b64_audio)


def synthesize_scene_gemini(text: str, out_dir: Path, name: str, voice: str) -> SceneAudio:
    """Gemini's TTS returns raw audio only, no word-level boundaries like
    edge-tts's WordBoundary events — word_timings is always empty. Only
    safe for tracks with burn_captions=False, since word_timings is unused
    there (see pipeline.shorts.assemble_short)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"{name}.mp3"
    timings_path = out_dir / f"{name}.words.json"
    raw_path = out_dir / f"{name}.pcm"

    pcm_bytes = _call_gemini_tts(text, voice)
    raw_path.write_bytes(pcm_bytes)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "s16le", "-ar", str(GEMINI_TTS_SAMPLE_RATE), "-ac", "1",
            "-i", str(raw_path), str(audio_path),
        ],
        check=True, capture_output=True,
    )
    raw_path.unlink()

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError(f"Gemini TTS produced an empty audio file for scene '{name}'")

    duration = _probe_duration(audio_path)
    timings_path.write_text(json.dumps([], indent=2))

    return SceneAudio(
        scene_index=-1,
        audio_path=audio_path,
        timings_path=timings_path,
        duration=duration,
        word_timings=[],
    )


def _pick_voice(track: Track, script: Script) -> str:
    """One narrator voice per script, chosen deterministically (not
    randomly) so re-running production for the same script is stable —
    same pattern as run_daily._story_seed for illustration consistency."""
    if not track.tts_voices:
        return track.voice
    index = zlib.crc32(script.id.encode()) % len(track.tts_voices)
    return track.tts_voices[index]


def synthesize_script_for_track(script: Script, out_dir: Path, track: Track) -> list[SceneAudio]:
    """Dispatches to edge-tts or Gemini TTS per Track.tts_provider, with a
    single narrator voice picked once per script and reused for every
    scene in it."""
    voice = _pick_voice(track, script)
    results = []
    for i, sc in enumerate(script.scenes):
        if track.tts_provider == "gemini":
            scene_audio = synthesize_scene_gemini(sc.narration, out_dir, f"scene_{i:02d}", voice)
        else:
            scene_audio = synthesize_scene(sc.narration, out_dir, f"scene_{i:02d}", voice)
        scene_audio.scene_index = i
        results.append(scene_audio)
    return results
