"""
High-level public API for the ytdownloader package.
"""

from .downloader import download_audio, download_video, print_video_info
from .utils import extract_video_id, is_valid_youtube_url, normalize_youtube_url

__version__ = "1.0.0"
__all__ = [
    "download_video",
    "download_audio",
    "print_video_info",
    "is_valid_youtube_url",
    "normalize_youtube_url",
    "extract_video_id",
]
