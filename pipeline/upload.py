"""Stage 6: YouTube Data API v3 upload + OAuth.

One-time setup: `python -m pipeline.upload --auth-setup` (needs
secrets/client_secret.json from Google Cloud Console). Caches a refresh
token to YOUTUBE_TOKEN_FILE, reused headlessly afterward (including in CI,
where the token is restored from a base64 repo secret before the run).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from pipeline.config import YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_SCOPES, YOUTUBE_TOKEN_FILE
from pipeline.scripts import Script

API_SERVICE_NAME = "youtube"
API_VERSION = "v3"
CATEGORY_SCIENCE_TECH = "28"


def load_credentials() -> Credentials:
    token_path = Path(YOUTUBE_TOKEN_FILE)
    if not token_path.exists():
        raise RuntimeError(
            "No cached YouTube OAuth token found. Run "
            "`python -m pipeline.upload --auth-setup` once locally first."
        )
    creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds


def run_oauth_setup() -> None:
    client_secret_path = Path(YOUTUBE_CLIENT_SECRET_FILE)
    if not client_secret_path.exists():
        raise RuntimeError(
            f"Missing OAuth client file at {client_secret_path}. Create an OAuth Desktop "
            "client in Google Cloud Console and download it there first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=0)
    token_path = Path(YOUTUBE_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Saved YouTube OAuth token to {token_path}")


def get_service():
    return build(API_SERVICE_NAME, API_VERSION, credentials=load_credentials())


def build_upload_body(title: str, description: str, tags: list[str], privacy_status: str = "private") -> dict:
    return {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": CATEGORY_SCIENCE_TECH,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = "private",
    dry_run: bool = False,
) -> dict:
    body = build_upload_body(title, description, tags, privacy_status)
    if dry_run:
        return {"dry_run": True, "video_path": str(video_path), "body": body}

    youtube = get_service()
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return {"dry_run": False, "video_id": response["id"]}


def set_thumbnail(video_id: str, thumbnail_path: Path, dry_run: bool = False) -> dict:
    if dry_run:
        return {"dry_run": True, "video_id": video_id, "thumbnail_path": str(thumbnail_path)}
    youtube = get_service()
    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
    return {"dry_run": False, "video_id": video_id}


def upload_daily_video(
    script: Script,
    long_form_path: Path,
    short_path: Path,
    thumbnail_path: Path,
    privacy_status: str = "private",
    dry_run: bool = False,
) -> dict:
    long_result = upload_video(
        long_form_path, script.title, script.description, script.tags, privacy_status, dry_run
    )

    short_title = script.title if len(script.title) <= 90 else script.title[:87] + "..."
    short_result = upload_video(
        short_path, f"{short_title} #shorts", script.description, [*script.tags, "shorts"],
        privacy_status, dry_run,
    )

    if dry_run:
        thumb_result = set_thumbnail("DRY_RUN_VIDEO_ID", thumbnail_path, dry_run=True)
    else:
        try:
            thumb_result = set_thumbnail(long_result["video_id"], thumbnail_path, dry_run=False)
        except HttpError as e:
            # Custom thumbnails require a phone-verified channel (a YouTube
            # account restriction, not something the API key controls). Don't
            # let this abort the run — the video itself uploaded fine and
            # falls back to YouTube's auto-generated thumbnail.
            thumb_result = {
                "dry_run": False, "video_id": long_result["video_id"],
                "error": f"thumbnail not set (channel likely needs phone verification at youtube.com/verify): {e}",
            }

    return {"long_form": long_result, "short": short_result, "thumbnail": thumb_result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-setup", action="store_true", help="Run the one-time OAuth consent flow")
    args = parser.parse_args()
    if args.auth_setup:
        run_oauth_setup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
