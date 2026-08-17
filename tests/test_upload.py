from pathlib import Path
from unittest.mock import patch

from googleapiclient.errors import HttpError

from pipeline.config import TRACKS
from pipeline.scripts import validate
from pipeline.upload import build_upload_body, set_thumbnail, upload_daily_video, upload_video

KIDS_TRACK = TRACKS["kids"]

SAMPLE = {
    "id": "upload-test",
    "title": "Test Video Title",
    "description": "Test description with #hashtags",
    "tags": ["ai", "tools"],
    "scenes": [
        {"narration": "hook", "on_screen_text": "HOOK", "duration_hint": 8},
        {"narration": "outro", "on_screen_text": "OUTRO", "duration_hint": 8},
    ],
}


def test_build_upload_body_truncates_and_structures_fields():
    body = build_upload_body("x" * 200, "y" * 6000, ["tag"] * 600, privacy_status="private")
    assert len(body["snippet"]["title"]) == 100
    assert len(body["snippet"]["description"]) == 5000
    assert len(body["snippet"]["tags"]) == 500
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False


def test_build_upload_body_respects_made_for_kids_and_category():
    body = build_upload_body("Title", "Desc", ["tag"], category_id="27", made_for_kids=True)
    assert body["snippet"]["categoryId"] == "27"
    assert body["status"]["selfDeclaredMadeForKids"] is True


def test_upload_video_dry_run_does_not_require_credentials(tmp_path):
    video = tmp_path / "video.mp4"
    video.touch()
    result = upload_video(video, "Title", "Description", ["tag1"], dry_run=True)
    assert result["dry_run"] is True
    assert result["video_path"] == str(video)
    assert result["body"]["snippet"]["title"] == "Title"


def test_set_thumbnail_dry_run_does_not_require_credentials(tmp_path):
    thumb = tmp_path / "thumb.jpg"
    thumb.touch()
    result = set_thumbnail("abc123", thumb, dry_run=True)
    assert result == {"dry_run": True, "video_id": "abc123", "thumbnail_path": str(thumb)}


def test_upload_daily_video_dry_run_orchestrates_video_and_thumbnail(tmp_path):
    script = validate(SAMPLE)
    video = tmp_path / "video.mp4"
    thumb = tmp_path / "thumb.jpg"
    for p in (video, thumb):
        p.touch()

    result = upload_daily_video(script, video, thumb, KIDS_TRACK, dry_run=True)

    assert result["video"]["body"]["snippet"]["title"] == "Test Video Title #shorts"
    assert result["video"]["body"]["status"]["selfDeclaredMadeForKids"] is True
    assert "shorts" in result["video"]["body"]["snippet"]["tags"]
    for extra_tag in KIDS_TRACK.extra_tags:
        assert extra_tag in result["video"]["body"]["snippet"]["tags"]
    assert result["thumbnail"]["dry_run"] is True


def test_upload_daily_video_is_short_false_omits_shorts_tag_and_suffix(tmp_path):
    script = validate(SAMPLE)
    video = tmp_path / "video.mp4"
    thumb = tmp_path / "thumb.jpg"
    for p in (video, thumb):
        p.touch()

    result = upload_daily_video(script, video, thumb, KIDS_TRACK, dry_run=True, is_short=False)

    assert result["video"]["body"]["snippet"]["title"] == "Test Video Title"
    assert "shorts" not in result["video"]["body"]["snippet"]["tags"]


def test_upload_daily_video_survives_thumbnail_permission_failure(tmp_path):
    # Custom thumbnails require a phone-verified channel; a 403 here should
    # not abort the run since the video already uploaded successfully.
    script = validate(SAMPLE)
    video = tmp_path / "video.mp4"
    thumb = tmp_path / "thumb.jpg"
    for p in (video, thumb):
        p.touch()

    fake_resp = type("Resp", (), {"status": 403, "reason": "Forbidden"})()
    forbidden = HttpError(fake_resp, b'{"error": "forbidden"}')

    with patch("pipeline.upload.upload_video") as mock_upload, \
         patch("pipeline.upload.set_thumbnail", side_effect=forbidden):
        mock_upload.return_value = {"dry_run": False, "video_id": "video123"}
        result = upload_daily_video(script, video, thumb, KIDS_TRACK, dry_run=False)

    assert result["video"]["video_id"] == "video123"
    assert "error" in result["thumbnail"]
    assert result["thumbnail"]["video_id"] == "video123"
