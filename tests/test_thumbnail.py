import subprocess

from PIL import Image

from pipeline.thumbnail import generate_thumbnail


def make_color_clip(path, color, duration=3.0, size="1920x1080"):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s={size}:d={duration}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def test_generate_thumbnail_produces_correct_size_jpeg(tmp_path):
    video = make_color_clip(tmp_path / "broll.mp4", "teal")
    out = generate_thumbnail(video, "5 ChatGPT Prompt Tricks Nobody Tells You About", tmp_path / "thumb.jpg")

    assert out.exists()
    img = Image.open(out)
    assert img.format == "JPEG"
    assert img.size == (1280, 720)


def test_generate_thumbnail_handles_short_text(tmp_path):
    video = make_color_clip(tmp_path / "broll.mp4", "orange")
    out = generate_thumbnail(video, "AI Tools", tmp_path / "thumb2.jpg")
    assert out.exists()
    assert Image.open(out).size == (1280, 720)


def test_generate_thumbnail_cleans_up_intermediate_frame(tmp_path):
    video = make_color_clip(tmp_path / "broll.mp4", "purple")
    out_path = tmp_path / "thumb3.jpg"
    generate_thumbnail(video, "Test", out_path)
    leftover = out_path.parent / f"_{out_path.stem}_frame.jpg"
    assert not leftover.exists()
