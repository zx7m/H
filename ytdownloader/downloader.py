"""
Core download logic using yt-dlp for format extraction and downloading.

This module leverages yt-dlp (the actively maintained fork of youtube-dl)
which handles the complex reverse-engineering of YouTube's video delivery,
format negotiation, and stream decryption.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .metadata import MetadataExtractionError, get_video_info
from .utils import extract_video_id, is_valid_youtube_url, normalize_youtube_url

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _get_ydl_opts(
    output_path: str = ".",
    audio_only: bool = False,
    quiet: bool = False,
    quality: str = "best",
) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": quiet,
        "no_warnings": not quiet,
        "outtmpl": os.path.join(output_path, "%(title)s [%(id)s].%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
    }

    if audio_only:
        opts["format"] = "bestaudio/best"
        if _HAS_FFMPEG:
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
    else:
        if quality == "best":
            fmt = "bestvideo+bestaudio/best"
        else:
            height = quality.replace("p", "")
            fmt = (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]"
            )
        if _HAS_FFMPEG:
            opts["format"] = fmt
            opts["merge_output_format"] = "mp4"
        else:
            opts["format"] = (
                f"{fmt.split('/')[0]}[ext=mp4]+bestaudio[ext=m4a]"
                f"/{fmt.split('/')[-1]}[ext=mp4]"
            )

    return opts


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

    formats = info.get("streaming_data", {}).get("formats", [])
    adaptive = info.get("streaming_data", {}).get("adaptiveFormats", [])
    all_formats = formats + adaptive

    if all_formats:
        print(f"\n  Available Formats ({len(all_formats)}):")
        print(f"  {'ID':<10} {'Type':<12} {'Quality':<15} {'Size':<12} {'Protocol'}")
        print(f"  {'-'*60}")
        for fmt in sorted(all_formats, key=lambda f: _format_sort_key(f)):
            fmt_id = fmt.get("itag", "N/A")
            ext = fmt.get("ext", "N/A")
            mime = fmt.get("mimeType", "")
            quality = _format_quality_label(fmt)
            size_str = _format_size(fmt.get("contentLength"))
            protocol = fmt.get("protocol", "N/A")
            print(f"  {fmt_id:<10} {ext:<12} {quality:<15} {size_str:<12} {protocol}")
    print()


def _format_sort_key(fmt: Dict[str, Any]) -> tuple[int, int, int]:
    vcodec = fmt.get("vcodec", "")
    acodec = fmt.get("acodec", "")
    height = fmt.get("height", 0) or 0
    tbr = fmt.get("tbr", 0) or 0
    if vcodec != "none" and acodec != "none":
        return (0, height, tbr)
    if vcodec != "none":
        return (1, height, tbr)
    return (2, height, tbr)


def _format_quality_label(fmt: Dict[str, Any]) -> str:
    width = fmt.get("width")
    height = fmt.get("height")
    if height:
        return f"{width}x{height}" if width else f"{height}p"
    abr = fmt.get("abr")
    if abr:
        return f"{abr}kbps audio"
    return "unknown"


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
    quality: str = "best",
) -> str:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    opts = _get_ydl_opts(output_path=output_path, audio_only=False, quiet=quiet, quality=quality)

    try:
        import yt_dlp
    except ImportError:
        raise ImportError(
            "yt-dlp is required for downloading. Install it with: pip install yt-dlp"
        )

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(normalized_url, download=True)

    filename = ydl.prepare_filename(info)
    if _HAS_FFMPEG and opts.get("merge_output_format") and not filename.endswith(".mp4"):
        base, _ = os.path.splitext(filename)
        filename = base + ".mp4"
    return filename


def download_audio(
    url: str,
    output_path: str = ".",
    quiet: bool = False,
) -> str:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    opts = _get_ydl_opts(output_path=output_path, audio_only=True, quiet=quiet)

    try:
        import yt_dlp
    except ImportError:
        raise ImportError(
            "yt-dlp is required for downloading. Install it with: pip install yt-dlp"
        )

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(normalized_url, download=True)

    filename = ydl.prepare_filename(info)
    if _HAS_FFMPEG:
        base, _ = os.path.splitext(filename)
        return base + ".mp3"
    return filename


def print_video_info(url: str) -> None:
    info = get_video_info_wrapper(url)
    _print_metadata(info)
