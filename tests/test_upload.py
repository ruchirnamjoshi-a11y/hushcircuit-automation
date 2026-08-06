from pathlib import Path
from unittest.mock import patch

from googleapiclient.errors import HttpError

from pipeline.scripts import validate
from pipeline.upload import build_upload_body, set_thumbnail, upload_daily_video, upload_video

SAMPLE = {
    "id": "upload-test",
    "title": "Test Video Title",
    "description": "Test description with #hashtags",
    "tags": ["ai", "tools"],
    "scenes": [
        {"narration": "hook", "visual_keyword": "a", "duration_hint": 8, "short_worthy": True},
        {"narration": "outro", "visual_keyword": "b", "duration_hint": 8, "short_worthy": True},
    ],
}


def test_build_upload_body_truncates_and_structures_fields():
    body = build_upload_body("x" * 200, "y" * 6000, ["tag"] * 600, privacy_status="private")
    assert len(body["snippet"]["title"]) == 100
    assert len(body["snippet"]["description"]) == 5000
    assert len(body["snippet"]["tags"]) == 500
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False


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


def test_upload_daily_video_dry_run_orchestrates_long_short_and_thumbnail(tmp_path):
    script = validate(SAMPLE)
    long_video = tmp_path / "long.mp4"
    short_video = tmp_path / "short.mp4"
    thumb = tmp_path / "thumb.jpg"
    for p in (long_video, short_video, thumb):
        p.touch()

    result = upload_daily_video(script, long_video, short_video, thumb, dry_run=True)

    assert result["long_form"]["body"]["snippet"]["title"] == "Test Video Title"
    assert result["short"]["body"]["snippet"]["title"] == "Test Video Title #shorts"
    assert "shorts" in result["short"]["body"]["snippet"]["tags"]
    assert result["thumbnail"]["dry_run"] is True


def test_upload_daily_video_survives_thumbnail_permission_failure(tmp_path):
    # Custom thumbnails require a phone-verified channel; a 403 here should
    # not abort the run since both videos already uploaded successfully.
    script = validate(SAMPLE)
    long_video = tmp_path / "long.mp4"
    short_video = tmp_path / "short.mp4"
    thumb = tmp_path / "thumb.jpg"
    for p in (long_video, short_video, thumb):
        p.touch()

    fake_resp = type("Resp", (), {"status": 403, "reason": "Forbidden"})()
    forbidden = HttpError(fake_resp, b'{"error": "forbidden"}')

    with patch("pipeline.upload.upload_video") as mock_upload, \
         patch("pipeline.upload.set_thumbnail", side_effect=forbidden):
        mock_upload.side_effect = [
            {"dry_run": False, "video_id": "long123"},
            {"dry_run": False, "video_id": "short456"},
        ]
        result = upload_daily_video(script, long_video, short_video, thumb, dry_run=False)

    assert result["long_form"]["video_id"] == "long123"
    assert result["short"]["video_id"] == "short456"
    assert "error" in result["thumbnail"]
    assert result["thumbnail"]["video_id"] == "long123"
