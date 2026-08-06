import subprocess

import pytest

from pipeline.config import PEXELS_API_KEY, PIXABAY_API_KEY
from pipeline.broll import fetch_broll, BrollNotFoundError

pytestmark = pytest.mark.skipif(
    not PEXELS_API_KEY and not PIXABAY_API_KEY,
    reason="PEXELS_API_KEY or PIXABAY_API_KEY required for broll tests",
)


def _is_valid_video(path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and float(result.stdout.strip()) > 0


def test_fetch_broll_downloads_valid_video(tmp_path):
    clip = fetch_broll("technology office", min_duration=5.0, out_dir=tmp_path)
    assert clip.path.exists()
    assert clip.path.stat().st_size > 0
    assert clip.source in ("pexels", "pixabay")
    assert _is_valid_video(clip.path)


def test_fetch_broll_uses_cache(tmp_path):
    cache = {}
    clip1 = fetch_broll("nature landscape", min_duration=5.0, out_dir=tmp_path, cache=cache)
    clip2 = fetch_broll("nature landscape", min_duration=5.0, out_dir=tmp_path, cache=cache)
    assert clip1.path == clip2.path


def test_fetch_broll_raises_when_no_provider_has_a_key(tmp_path, monkeypatch):
    # Pexels/Pixabay both return generic fallback results even for nonsense
    # queries rather than 404ing, so the only realistic "not found" case is
    # having no usable API key for either provider at all.
    monkeypatch.setattr("pipeline.broll.PEXELS_API_KEY", "")
    monkeypatch.setattr("pipeline.broll.PIXABAY_API_KEY", "")
    with pytest.raises(BrollNotFoundError):
        fetch_broll("technology office", min_duration=5.0, out_dir=tmp_path)
