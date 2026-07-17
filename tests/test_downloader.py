"""
Real integration tests for ytdownloader.downloader using actual yt-dlp calls.

All tests use real network and subprocess calls - no mocking.
"""

from __future__ import annotations

import os

import pytest

from ytdownloader import downloader
from ytdownloader.utils import is_valid_youtube_url


REAL_PUBLIC_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestDownloadVideoReal:
    def test_download_video_real(self, tmp_download_dir, yt_dlp_check, network_check):
        filename = downloader.download_video(REAL_PUBLIC_URL, output_path=str(tmp_download_dir), quiet=True)
        assert os.path.isfile(filename), f"Downloaded video file not found: {filename}"
        assert os.path.getsize(filename) > 0, f"Downloaded video file is empty: {filename}"
        os.remove(filename)


class TestDownloadAudioReal:
    def test_download_audio_real(self, tmp_download_dir, yt_dlp_check, network_check):
        filename = downloader.download_audio(REAL_PUBLIC_URL, output_path=str(tmp_download_dir), quiet=True)
        assert os.path.isfile(filename), f"Downloaded audio file not found: {filename}"
        assert os.path.getsize(filename) > 0, f"Downloaded audio file is empty: {filename}"
        os.remove(filename)


class TestDownloadInvalidUrl:
    def test_download_invalid_url(self):
        with pytest.raises(ValueError):
            downloader.download_video("https://www.example.com/not-a-youtube-url")

        with pytest.raises(ValueError):
            downloader.download_audio("https://www.example.com/not-a-youtube-url")


class TestGetYdlOpts:
    def test_ydl_opts_audio_format(self):
        opts = downloader._get_ydl_opts(audio_only=True)
        assert opts["format"] == "bestaudio/best"

    def test_ydl_opts_video_format(self):
        import shutil
        opts = downloader._get_ydl_opts(audio_only=False)
        assert "format" in opts
        if shutil.which("ffmpeg") is not None:
            assert opts.get("merge_output_format") == "mp4"
        else:
            assert "merge_output_format" not in opts
