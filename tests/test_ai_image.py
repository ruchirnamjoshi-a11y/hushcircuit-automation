import base64
from io import BytesIO
from unittest.mock import MagicMock, patch

from PIL import Image

from pipeline.ai_image import build_prompt, fit_scene_image, generate_scene_image_raw
from pipeline.scripts import Scene

SAMPLE_SCENE = Scene(
    narration="Trick one: give it a role. Instead of asking a plain question, tell ChatGPT who to be.",
    on_screen_text="GIVE IT A ROLE",
    duration_hint=45,
    short_worthy=False,
)


def _fake_cf_response(image: Image.Image, success: bool = True) -> MagicMock:
    buf = BytesIO()
    image.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"success": success, "result": {"image": image_b64}, "errors": []}
    return mock_response


def test_build_prompt_includes_on_screen_text_and_style():
    prompt = build_prompt(SAMPLE_SCENE)
    assert "give it a role" in prompt
    assert "flat vector illustration" in prompt
    assert "no text" in prompt


def test_build_prompt_substitutes_trigger_words():
    scene = Scene(
        narration="...",
        on_screen_text="SUBSCRIBE FOR FREE TIPS",
        duration_hint=10,
        short_worthy=False,
    )
    prompt = build_prompt(scene)
    assert "follow" in prompt
    assert "no-cost" in prompt
    assert "subscribe" not in prompt.lower()
    assert "free" not in prompt.lower()


def test_generate_scene_image_raw_falls_back_to_gradient_when_no_credentials(tmp_path):
    with patch("pipeline.ai_image.CF_ACCOUNT_ID", ""), patch("pipeline.ai_image.CF_API_TOKEN", ""):
        out, used_fallback = generate_scene_image_raw(SAMPLE_SCENE, tmp_path / "raw.png")
    assert out.exists()
    assert used_fallback is True
    assert Image.open(out).mode in ("RGB", "RGBA")


def test_generate_scene_image_raw_falls_back_after_all_retries_fail(tmp_path):
    with patch("pipeline.ai_image.CF_ACCOUNT_ID", "fake-account"), \
         patch("pipeline.ai_image.CF_API_TOKEN", "fake-token"), \
         patch("pipeline.ai_image.requests.post", side_effect=RuntimeError("simulated API failure")), \
         patch("pipeline.ai_image.time.sleep"):
        out, used_fallback = generate_scene_image_raw(SAMPLE_SCENE, tmp_path / "raw.png", retries=2)

    assert out.exists()
    assert used_fallback is True


def test_generate_scene_image_raw_succeeds_saves_native_resolution(tmp_path):
    fake_image = Image.new("RGB", (1024, 768), (10, 20, 30))
    mock_response = _fake_cf_response(fake_image)

    with patch("pipeline.ai_image.CF_ACCOUNT_ID", "fake-account"), \
         patch("pipeline.ai_image.CF_API_TOKEN", "fake-token"), \
         patch("pipeline.ai_image.requests.post", return_value=mock_response) as mock_post:
        out, used_fallback = generate_scene_image_raw(SAMPLE_SCENE, tmp_path / "raw.png")

    assert used_fallback is False
    assert Image.open(out).size == (1024, 768)  # untouched, no crop yet
    assert mock_post.call_count == 1
    # sanity-check the request shape
    _, kwargs = mock_post.call_args
    assert "Authorization" in kwargs["headers"]
    assert "prompt" in kwargs["json"]


def test_generate_scene_image_raw_treats_success_false_as_failure(tmp_path):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"success": False, "errors": [{"message": "quota exceeded"}]}

    with patch("pipeline.ai_image.CF_ACCOUNT_ID", "fake-account"), \
         patch("pipeline.ai_image.CF_API_TOKEN", "fake-token"), \
         patch("pipeline.ai_image.requests.post", return_value=mock_response), \
         patch("pipeline.ai_image.time.sleep"):
        out, used_fallback = generate_scene_image_raw(SAMPLE_SCENE, tmp_path / "raw.png", retries=1)

    assert used_fallback is True


def test_fit_scene_image_crops_to_requested_resolution_no_api_call(tmp_path):
    raw = tmp_path / "raw.png"
    Image.new("RGB", (1024, 768), (1, 2, 3)).save(raw)

    long_form = fit_scene_image(raw, tmp_path / "long.png", (1920, 1080))
    short = fit_scene_image(raw, tmp_path / "short.png", (1080, 1920))

    assert Image.open(long_form).size == (1920, 1080)
    assert Image.open(short).size == (1080, 1920)
