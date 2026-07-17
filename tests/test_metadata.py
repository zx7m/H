"""
Real integration tests for ytdownloader.metadata using actual network calls.

All tests use real network - no mocking.
"""

from __future__ import annotations

import pytest

from ytdownloader import metadata
from ytdownloader.metadata import MetadataExtractionError


REAL_PUBLIC_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestGetVideoInfoReal:
    def test_get_video_info_real(self, network_check):
        info = metadata.get_video_info(REAL_PUBLIC_URL)

        assert isinstance(info, dict)
        assert "title" in info
        assert "id" in info
        assert "author" in info
        assert "duration" in info
        assert "view_count" in info
        assert "thumbnail" in info
        assert "streaming_data" in info

        assert info["id"] == "dQw4w9WgXcQ"
        assert isinstance(info["title"], str)
        assert len(info["title"]) > 0
        assert isinstance(info["author"], str)
        assert isinstance(info["duration"], str)
        assert isinstance(info["thumbnail"], list)
        assert isinstance(info["streaming_data"], dict)

    def test_get_video_info_unavailable(self, network_check):
        with pytest.raises(MetadataExtractionError):
            metadata.get_video_info("https://www.youtube.com/watch?v=ZZZZZZZZZZZZ")


class TestFetchPageReal:
    def test_fetch_page_real(self, network_check):
        html = metadata._fetch_page(REAL_PUBLIC_URL)

        assert isinstance(html, str)
        assert len(html) > 0
        assert "youtube.com" in html


class TestExtractJsonObject:
    def test_extract_json_object(self):
        sample = 'var data = {"key": "value", "nested": {"a": 1}};'
        start = sample.index("{")
        result = metadata._extract_json_object(sample, start)

        assert result is not None
        assert result == '{"key": "value", "nested": {"a": 1}}'

    def test_extract_json_object_nested_braces(self):
        sample = 'ytInitialPlayerResponse = {"videoId": "abc", "player_response": {"status": "OK"}};'
        start = sample.index("{")
        result = metadata._extract_json_object(sample, start)

        assert result is not None
        import json
        parsed = json.loads(result)
        assert parsed["videoId"] == "abc"
        assert parsed["player_response"]["status"] == "OK"

    def test_extract_json_object_no_match(self):
        sample = "no json here"
        result = metadata._extract_json_object(sample, 0)
        assert result is None

    def test_extract_json_object_string_with_braces(self):
        sample = 'var x = {"desc": "has { and } inside", "ok": true};'
        start = sample.index("{")
        result = metadata._extract_json_object(sample, start)

        assert result is not None
        import json
        parsed = json.loads(result)
        assert parsed["desc"] == "has { and } inside"
        assert parsed["ok"] is True


class TestCheckPlayability:
    def test_check_playability_ok(self):
        status = metadata._check_playability({"status": "OK"})
        assert status == "OK"

    def test_check_playability_login_required(self):
        status = metadata._check_playability({"status": "LOGIN_REQUIRED"})
        assert status == "LOGIN_REQUIRED"

    def test_check_playability_unplayable(self):
        status = metadata._check_playability({"status": "UNPLAYABLE"})
        assert status == "UNPLAYABLE"

    def test_check_playability_age_check_required(self):
        status = metadata._check_playability({"status": "AGE_CHECK_REQUIRED"})
        assert status == "AGE_CHECK_REQUIRED"

    def test_check_playability_empty_status(self):
        status = metadata._check_playability({})
        assert status == ""

    def test_check_playability_default(self):
        status = metadata._check_playability({"status": "LIVE_STREAM_OFFLINE"})
        assert status == "LIVE_STREAM_OFFLINE"
