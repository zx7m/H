"""
Pure unit tests for ytdownloader.utils (no network required).
"""

from __future__ import annotations

import pytest

from ytdownloader.utils import (
    extract_video_id,
    is_valid_youtube_url,
    normalize_youtube_url,
)


class TestIsValidYoutubeUrl:
    def test_watch_url_valid(self):
        assert is_valid_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_youtu_be_valid(self):
        assert is_valid_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True

    def test_shorts_url_valid(self):
        assert is_valid_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") is True

    def test_embed_url_valid(self):
        assert is_valid_youtube_url("https://www.youtube.com/embed/dQw4w9WgXcQ") is True

    def test_live_url_valid(self):
        assert is_valid_youtube_url("https://www.youtube.com/live/dQw4w9WgXcQ") is True

    def test_no_scheme(self):
        assert is_valid_youtube_url("www.youtube.com/watch?v=dQw4w9WgXcQ") is False

    def test_wrong_domain(self):
        assert is_valid_youtube_url("https://www.example.com/watch?v=dQw4w9WgXcQ") is False

    def test_http_scheme_accepted(self):
        assert is_valid_youtube_url("http://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_whitespace_stripped(self):
        assert is_valid_youtube_url("  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ") is True


class TestNormalizeYoutubeUrl:
    def test_normalize_youtu_be(self):
        result = normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        assert result == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_normalize_shorts(self):
        result = normalize_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert result == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_normalize_live(self):
        result = normalize_youtube_url("https://www.youtube.com/live/dQw4w9WgXcQ")
        assert result == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_watch_url_unchanged(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert normalize_youtube_url(url) == url

    def test_whitespace_stripped(self):
        result = normalize_youtube_url("  https://youtu.be/dQw4w9WgXcQ  ")
        assert result == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestExtractVideoId:
    def test_extract_from_watch(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_youtu_be(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_shorts(self):
        assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_embed(self):
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_v(self):
        assert extract_video_id("https://www.youtube.com/v/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_live(self):
        assert extract_video_id("https://www.youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_invalid_url_returns_none(self):
        assert extract_video_id("https://www.youtube.com/") is None

    def test_non_youtube_returns_none(self):
        assert extract_video_id("https://www.example.com/page") is None
