"""
ytdownloader - A YouTube video downloader that reverse-engineers
YouTube's video delivery to extract and download video/audio streams.

This package provides a fully native implementation with no yt-dlp dependency.
All stream resolution, format selection, and downloading is handled internally
using the package's own modules.

Public API:
    download_video  - Download best video quality (merges audio+video if needed)
    download_audio  - Download audio-only stream
    get_video_info  - Fetch and return VideoInfo metadata object
    print_video_info - Print comprehensive video metadata to stdout
    VideoInfo       - Structured video metadata dataclass
    select_format   - Choose the best stream format for a given quality
"""

from .downloader import (
    VideoInfo,
    download_audio,
    download_video,
    get_video_info,
    list_available_formats,
    print_video_info,
    select_format,
)
from .utils import extract_video_id, is_valid_youtube_url

__version__ = "2.0.0"

__all__ = [
    "VideoInfo",
    "download_video",
    "download_audio",
    "get_video_info",
    "print_video_info",
    "select_format",
    "list_available_formats",
    "is_valid_youtube_url",
    "extract_video_id",
]
