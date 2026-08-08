import os
from unittest.mock import patch

from pipeline.config import SECRETS_DIR, youtube_token_path


def test_youtube_token_path_defaults_per_track():
    assert youtube_token_path("kids") == SECRETS_DIR / "youtube_token_kids.json"
    assert youtube_token_path("women") == SECRETS_DIR / "youtube_token_women.json"


def test_youtube_token_path_is_distinct_per_track():
    paths = {youtube_token_path(k) for k in ("kids", "teens", "adults", "women")}
    assert len(paths) == 4


def test_youtube_token_path_respects_env_override():
    with patch.dict(os.environ, {"YOUTUBE_TOKEN_FILE_KIDS": "/custom/path/kids_token.json"}):
        assert str(youtube_token_path("kids")) == "/custom/path/kids_token.json"
