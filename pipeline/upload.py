"""Stage 6: YouTube Data API v3 upload + OAuth.

Multi-channel: each track uploads to its own YouTube channel, so each has
its own cached OAuth token (see pipeline.config.youtube_token_path and the
README's multi-channel setup section). One-time setup per channel:
`python -m pipeline.upload --auth-setup --track <track>` (needs
secrets/client_secret.json from Google Cloud Console — the OAuth client
itself is shared across channels, only the resulting token differs). In CI,
each track's token is restored from its own base64 repo secret before the
run.
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

from pipeline.config import (
    CATEGORY_ENTERTAINMENT,
    TRACKS,
    YOUTUBE_CLIENT_SECRET_FILE,
    YOUTUBE_SCOPES,
    Track,
    youtube_token_path,
)
from pipeline.scripts import Script

API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


def load_credentials(token_path: Path) -> Credentials:
    if not token_path.exists():
        raise RuntimeError(
            f"No cached YouTube OAuth token found at {token_path}. Run "
            "`python -m pipeline.upload --auth-setup --track <track>` once locally first."
        )
    creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds


def run_oauth_setup(token_path: Path) -> None:
    client_secret_path = Path(YOUTUBE_CLIENT_SECRET_FILE)
    if not client_secret_path.exists():
        raise RuntimeError(
            f"Missing OAuth client file at {client_secret_path}. Create an OAuth Desktop "
            "client in Google Cloud Console and download it there first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Saved YouTube OAuth token to {token_path}")


def get_service(token_path: Path):
    return build(API_SERVICE_NAME, API_VERSION, credentials=load_credentials(token_path))


def build_upload_body(
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = "private",
    category_id: str = CATEGORY_ENTERTAINMENT,
    made_for_kids: bool = False,
) -> dict:
    return {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = "private",
    dry_run: bool = False,
    category_id: str = CATEGORY_ENTERTAINMENT,
    made_for_kids: bool = False,
    token_path: Path | None = None,
) -> dict:
    body = build_upload_body(title, description, tags, privacy_status, category_id, made_for_kids)
    if dry_run:
        return {"dry_run": True, "video_path": str(video_path), "body": body}

    youtube = get_service(token_path)
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return {"dry_run": False, "video_id": response["id"]}


def set_thumbnail(
    video_id: str, thumbnail_path: Path, dry_run: bool = False, token_path: Path | None = None,
) -> dict:
    if dry_run:
        return {"dry_run": True, "video_id": video_id, "thumbnail_path": str(thumbnail_path)}
    youtube = get_service(token_path)
    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
    return {"dry_run": False, "video_id": video_id}


def upload_daily_video(
    script: Script,
    video_path: Path,
    thumbnail_path: Path,
    track: Track,
    privacy_status: str = "private",
    dry_run: bool = False,
    is_short: bool = True,
) -> dict:
    """Uploads a vertical story video to `track`'s own YouTube channel.
    is_short=True (default) tags/titles it for the Shorts shelf; pass False
    for the full-length long-form cut of the same story (see
    Track.produce_long_form) — no "#shorts"/"shorts" tag, since that would
    make YouTube treat even a multi-minute upload as a Short. Tracks with
    produce_long_form=True get two uploads/day (short + long), still well
    inside the default 10,000 units/day free quota per channel's project."""
    token_path = youtube_token_path(track.key)
    tags = [*script.tags, *track.extra_tags, *(["shorts"] if is_short else [])]
    title = script.title if len(script.title) <= 90 else script.title[:87] + "..."
    if is_short:
        title = f"{title} #shorts"
    video_result = upload_video(
        video_path, title, script.description, tags, privacy_status, dry_run,
        category_id=track.category_id, made_for_kids=track.made_for_kids, token_path=token_path,
    )

    if dry_run:
        thumb_result = set_thumbnail("DRY_RUN_VIDEO_ID", thumbnail_path, dry_run=True)
    else:
        try:
            thumb_result = set_thumbnail(
                video_result["video_id"], thumbnail_path, dry_run=False, token_path=token_path,
            )
        except HttpError as e:
            # Custom thumbnails require a phone-verified channel (a YouTube
            # account restriction, not something the API key controls). Don't
            # let this abort the run — the video itself uploaded fine and
            # falls back to YouTube's auto-generated thumbnail.
            thumb_result = {
                "dry_run": False, "video_id": video_result["video_id"],
                "error": f"thumbnail not set (channel likely needs phone verification at youtube.com/verify): {e}",
            }

    return {"video": video_result, "thumbnail": thumb_result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-setup", action="store_true", help="Run the one-time OAuth consent flow")
    parser.add_argument(
        "--track", choices=list(TRACKS.keys()),
        help="Which track's channel to authorize (required with --auth-setup). "
             "Switch your active YouTube channel in studio.youtube.com to the "
             "matching channel BEFORE running this, so the consent screen "
             "authorizes the right one.",
    )
    args = parser.parse_args()
    if args.auth_setup:
        if not args.track:
            parser.error("--auth-setup requires --track <kids|teens|adults|women>")
        run_oauth_setup(youtube_token_path(args.track))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
