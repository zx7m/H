"""
Shared pytest fixtures for ytdownloader integration tests.
"""

from __future__ import annotations

import socket

import pytest

SAMPLE_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
]


@pytest.fixture
def sample_urls():
    """List of real public YouTube URLs suitable for live network tests."""
    return list(SAMPLE_URLS)


@pytest.fixture
def tmp_download_dir(tmp_path):
    """pytest tmp_path fixture providing a real temporary directory for downloads."""
    return tmp_path


@pytest.fixture(scope="session")
def yt_dlp_check():
    """Skip tests if yt-dlp is not installed."""
    import shutil
    if shutil.which("yt-dlp") is None:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            pytest.skip("yt-dlp is not installed")


@pytest.fixture(scope="session")
def network_check():
    """Skip network tests if there is no internet connection."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        pytest.skip("No internet connection available")
