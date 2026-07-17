"""
Core download logic using native stream resolution and HTTP downloader.

This module provides download_video, download_audio, print_video_info, and
get_video_info_wrapper functions using the native ytdownloader modules
(stream_resolver, http_downloader, metadata).  No yt-dlp dependency.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .metadata import get_video_info
from .utils import is_valid_youtube_url, normalize_youtube_url
from .exceptions import StreamResolutionError
from .http_downloader import (
    compute_output_path,
    download_audio_from_info,
    download_video_from_info,
)
from .stream_resolver import parse_streaming_data, resolve_streams

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _print_metadata(info: Dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print(f"  Title:        {info.get('title', 'N/A')}")
    print(f"  Video ID:     {info.get('id', 'N/A')}")
    print(f"  Author:       {info.get('author', 'N/A')}")
    print(f"  Channel ID:   {info.get('channel_id', 'N/A')}")
    print(f"  Duration:     {info.get('duration', 'N/A')}")
    print(f"  Upload Date:  {info.get('upload_date', 'N/A')}")
    print(f"  Views:        {info.get('view_count', 'N/A')}")
    print(f"  Live:         {info.get('live_status', 'N/A')}")
    print(f"  Private:      {info.get('is_private', 'N/A')}")
    print(f"{'='*60}")

    keywords = info.get("keywords", [])
    if keywords:
        print(f"  Keywords:     {', '.join(keywords[:10])}")

    thumbnails = info.get("thumbnail", [])
    if thumbnails:
        best_thumb = thumbnails[-1].get("url", "N/A")
        print(f"  Thumbnail:    {best_thumb}")

    streaming_data = info.get("streaming_data", {})
    formats_raw = streaming_data.get("formats", []) + streaming_data.get("adaptiveFormats", [])

    if formats_raw:
        print(f"\n  Available Formats ({len(formats_raw)}):")
        print(f"  {'ID':<10} {'Type':<12} {'Quality':<15} {'Size':<12} {'Protocol'}")
        print(f"  {'-'*60}")
        for fmt in sorted(formats_raw, key=_format_sort_key):
            fmt_id = fmt.get("itag", "N/A")
            ext = fmt.get("ext", "N/A")
            height = fmt.get("height")
            quality = f"{height}p" if height else (f"{fmt.get('abr')}kbps audio" if fmt.get("acodec", "none") != "none" else "unknown")
            size_str = _format_size(fmt.get("contentLength"))
            protocol = fmt.get("protocol", "N/A")
            print(f"  {fmt_id:<10} {ext:<12} {quality:<15} {size_str:<12} {protocol}")
    print()


def _format_sort_key(fmt: Dict[str, Any]) -> tuple:
    vcodec = fmt.get("vcodec", "")
    acodec = fmt.get("acodec", "")
    height = fmt.get("height", 0) or 0
    tbr = fmt.get("tbr", 0) or 0
    if vcodec != "none" and acodec != "none":
        return (0, height, tbr)
    if vcodec != "none":
        return (1, height, tbr)
    return (2, height, tbr)


def _format_size(content_length) -> str:
    if not content_length:
        return "unknown"
    try:
        size_bytes = int(content_length)
        if size_bytes > 1024 * 1024 * 1024:
            return f"{size_bytes / (1024**3):.1f} GB"
        elif size_bytes > 1024 * 1024:
            return f"{size_bytes / (1024**2):.1f} MB"
        elif size_bytes > 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"
    except (ValueError, TypeError):
        return "unknown"


def get_video_info_wrapper(url: str) -> Dict[str, Any]:
    if not is_valid_youtube_url(url):
        raise ValueError(
            f"Invalid YouTube URL: {url}\n"
            "Supported formats:\n"
            "  - https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  - https://youtu.be/VIDEO_ID\n"
            "  - https://www.youtube.com/shorts/VIDEO_ID\n"
            "  - https://www.youtube.com/embed/VIDEO_ID"
        )

    normalized_url = normalize_youtube_url(url)
    return get_video_info(normalized_url)


def download_video(
    url: str,
    output_path: str = ".",
    quiet: bool = False,
) -> str:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    info = get_video_info(normalized_url)

    if not quiet:
        print(f"Downloading video: {info.get('title', 'Unknown')}")

    filename = download_video_from_info(info, output_path=output_path)
    if not quiet:
        print(f"Saved to: {filename}")
    return filename


def download_audio(
    url: str,
    output_path: str = ".",
    quiet: bool = False,
) -> str:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    info = get_video_info(normalized_url)

    if not quiet:
        print(f"Downloading audio: {info.get('title', 'Unknown')}")

    filename = download_audio_from_info(info, output_path=output_path)
    if not quiet:
        print(f"Saved to: {filename}")
    return filename


def print_video_info(url: str) -> None:
    info = get_video_info_wrapper(url)
    _print_metadata(info)
