"""
ytdownloader - A YouTube video downloader that reverse-engineers
YouTube's video delivery to extract and download video/audio streams.
"""

from .downloader import download_video, download_audio, get_video_info, select_format
from .utils import is_valid_youtube_url, extract_video_id

__version__ = "1.0.0"
__all__ = [
    "download_video",
    "download_audio",
    "get_video_info",
    "select_format",
    "is_valid_youtube_url",
    "extract_video_id",
]
