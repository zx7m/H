"""
ytdownloader - A YouTube video downloader that reverse-engineers
YouTube's video delivery to extract and download video/audio streams.
"""

from __future__ import annotations

import warnings

__version__ = "1.0.0"

try:
    from .downloader import download_audio, download_video, get_video_info
except ImportError:  # pragma: no cover
    warnings.warn(
        "Optional import of .downloader failed; "
        "core functionality (download_video, download_audio, get_video_info) is unavailable.",
        ImportWarning,
        stacklevel=2,
    )

try:
    from .utils import extract_video_id, is_valid_youtube_url
except ImportError:  # pragma: no cover
    warnings.warn(
        "Optional import of .utils failed; "
        "core functionality (is_valid_youtube_url, extract_video_id) is unavailable.",
        ImportWarning,
        stacklevel=2,
    )

__all__ = [
    "download_video",
    "download_audio",
    "get_video_info",
    "is_valid_youtube_url",
    "extract_video_id",
]
