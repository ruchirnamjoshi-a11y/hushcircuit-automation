import subprocess

import pytest

from pipeline.ffmpeg_utils import probe_duration
from pipeline.textcard import generate_background_clip, generate_image_background_clip, make_gradient_image


def probe_resolution(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def test_generate_background_clip_matches_duration_and_resolution(tmp_path):
    out = generate_background_clip((640, 360), duration=3.0, out_path=tmp_path / "bg.mp4")
    assert out.exists()
    assert probe_duration(out) == pytest.approx(3.0, abs=0.3)
    assert probe_resolution(out) == (640, 360)


def test_generate_background_clip_cleans_up_intermediate_gradient(tmp_path):
    out_path = tmp_path / "bg2.mp4"
    generate_background_clip((640, 360), duration=1.5, out_path=out_path)
    leftover = out_path.parent / f"_{out_path.stem}_gradient.png"
    assert not leftover.exists()


def test_generate_background_clip_handles_vertical_resolution(tmp_path):
    out = generate_background_clip((1080, 1920), duration=2.0, out_path=tmp_path / "short_bg.mp4")
    assert probe_resolution(out) == (1080, 1920)


def test_generate_image_background_clip_zooms_a_supplied_image(tmp_path):
    source = make_gradient_image(tmp_path / "source.png", (640, 360))
    out = generate_image_background_clip(source, (640, 360), duration=2.0, out_path=tmp_path / "img_bg.mp4")
    assert out.exists()
    assert probe_duration(out) == pytest.approx(2.0, abs=0.3)
    assert probe_resolution(out) == (640, 360)


def test_generate_image_background_clip_has_no_orb_files_to_clean_up(tmp_path):
    source = make_gradient_image(tmp_path / "source2.png", (640, 360))
    out_path = tmp_path / "img_bg2.mp4"
    generate_image_background_clip(source, (640, 360), duration=1.0, out_path=out_path)
    assert not (out_path.parent / f"_{out_path.stem}_orb_gold.png").exists()
    assert not (out_path.parent / f"_{out_path.stem}_orb_violet.png").exists()
