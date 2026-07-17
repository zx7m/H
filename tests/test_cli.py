"""
Real integration tests for ytdownloader CLI using subprocess invocation.

All tests invoke python -m ytdownloader with real network calls - no mocking.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

REAL_PUBLIC_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def run_cli(args: list[str], cwd: str = PROJECT_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ytdownloader"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class TestCliDownloadVideo:
    def test_cli_download_video(self, tmp_download_dir, yt_dlp_check, network_check):
        output_path = os.path.join(str(tmp_download_dir), "cli_test_video.mp4")
        result = run_cli([REAL_PUBLIC_URL, "--output", str(tmp_download_dir)])

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        files = [f for f in os.listdir(str(tmp_download_dir)) if f.endswith(".mp4")]
        assert len(files) > 0, "No .mp4 file found after CLI download"
        video_file = os.path.join(str(tmp_download_dir), files[0])
        assert os.path.getsize(video_file) > 0


class TestCliDownloadAudio:
    def test_cli_download_audio(self, tmp_download_dir, yt_dlp_check, network_check):
        result = run_cli([REAL_PUBLIC_URL, "--audio", "--output", str(tmp_download_dir)])

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        audio_extensions = {".mp3", ".m4a", ".webm", ".opus", ".ogg"}
        files = [
            f for f in os.listdir(str(tmp_download_dir))
            if os.path.splitext(f)[1].lower() in audio_extensions
        ]
        assert len(files) > 0, "No audio file found after CLI audio download"
        audio_file = os.path.join(str(tmp_download_dir), files[0])
        assert os.path.getsize(audio_file) > 0


class TestCliInfo:
    def test_cli_info(self, network_check):
        result = run_cli([REAL_PUBLIC_URL, "--info"])

        assert result.returncode == 0, f"CLI info failed: {result.stderr}"
        assert "dQw4w9WgXcQ" in result.stdout or "Rick Astley" in result.stdout or "Rick" in result.stdout


class TestCliInvalidUrl:
    def test_cli_invalid_url(self):
        result = run_cli(["https://www.example.com/not-a-youtube-url"])

        assert result.returncode != 0

    def test_cli_invalid_url_stderr_message(self):
        result = run_cli(["https://www.example.com/not-a-youtube-url"])

        assert result.returncode != 0
        assert "Error" in result.stderr or "Invalid" in result.stderr
