from PIL import Image

from pipeline.thumbnail import generate_thumbnail


def test_generate_thumbnail_produces_correct_size_jpeg(tmp_path):
    out = generate_thumbnail("5 ChatGPT Prompt Tricks Nobody Tells You About", tmp_path / "thumb.jpg")

    assert out.exists()
    img = Image.open(out)
    assert img.format == "JPEG"
    assert img.size == (1280, 720)


def test_generate_thumbnail_handles_short_text(tmp_path):
    out = generate_thumbnail("AI Tools", tmp_path / "thumb2.jpg")
    assert out.exists()
    assert Image.open(out).size == (1280, 720)


def test_generate_thumbnail_cleans_up_intermediate_gradient(tmp_path):
    out_path = tmp_path / "thumb3.jpg"
    generate_thumbnail("Test", out_path)
    leftover = out_path.parent / f"_{out_path.stem}_gradient.png"
    assert not leftover.exists()


def test_generate_thumbnail_draw_text_false_skips_overlay(tmp_path):
    out = generate_thumbnail("बाल हनुमान और सूर्य", tmp_path / "thumb4.jpg", draw_text=False)
    assert out.exists()
    assert Image.open(out).size == (1280, 720)
