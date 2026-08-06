"""Stage 2: voiceover synthesis via edge-tts, one file per scene.

Synthesizing per-scene (not the whole script at once) keeps each scene's audio
aligned 1:1 with its own b-roll clip in assemble.py.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import edge_tts

from pipeline.config import TTS_VOICE
from pipeline.scripts import Script

HUNDRED_NS_PER_SECOND = 1e7


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


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
