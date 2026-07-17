"""
Core download logic using pure Python requests for stream downloading.

This module fetches video metadata via the existing metadata extraction pipeline
and downloads the best available stream directly with requests, removing the
yt-dlp dependency entirely.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

from .metadata import MetadataExtractionError, get_video_info
from .utils import extract_video_id, is_valid_youtube_url, normalize_youtube_url

_HAS_FFMPEG = shutil.which("ffmpeg") is not None

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.youtube.com/",
    "Origin": "https://www.youtube.com",
}

_FS_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(title: str, video_id: str, ext: str) -> str:
    clean_title = _FS_UNSAFE_RE.sub("_", title) if title else video_id
    clean_title = clean_title.strip(".")
    return f"{clean_title} [{video_id}].{ext}"


def _select_audio_format(
    formats: List[Dict[str, Any]],
    adaptive: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    all_formats = formats + adaptive
    audio_formats = [
        f for f in all_formats
        if f.get("acodec", "") != "none" and f.get("vcodec", "") == "none"
    ]
    if not audio_formats:
        return None

    def _audio_sort_key(fmt: Dict[str, Any]) -> Tuple[int, int]:
        abr = fmt.get("averageBitrate") or fmt.get("abr") or fmt.get("bitrate") or 0
        return (-abr,)

    audio_formats.sort(key=_audio_sort_key)
    return audio_formats[0]


def _select_video_format(
    formats: List[Dict[str, Any]],
    adaptive: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], bool]:
    all_formats = formats + adaptive
    progressive = [
        f for f in all_formats
        if f.get("acodec", "") != "none" and f.get("vcodec", "") != "none"
    ]
    if progressive:
        progressive.sort(key=lambda f: (-(f.get("height") or 0), -(f.get("tbr") or 0)))
        return progressive[0], True

    video_only = [f for f in all_formats if f.get("vcodec", "") != "none"]
    if video_only:
        video_only.sort(key=lambda f: (-(f.get("height") or 0), -(f.get("tbr") or 0)))
        return video_only[0], False
    return None, False


def _download_stream(url: str, dest: str, quiet: bool) -> None:
    with requests.get(url, headers=_HEADERS, stream=True, timeout=30) as response:
        response.raise_for_status()
        total_str = response.headers.get("Content-Length", "0")
        try:
            total = int(total_str)
        except ValueError:
            total = 0

        downloaded = 0
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                if not quiet and total > 0:
                    mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    pct = downloaded / total * 100
                    sys.stderr.write(
                        f"\r  Downloading: {mb:.1f} MB / {total_mb:.1f} MB ({pct:.0f}%)"
                    )
                    sys.stderr.flush()
        if not quiet:
            sys.stderr.write("\r  Download complete.                  \n")
            sys.stderr.flush()


def _convert_to_mp3(source: str, dest: str, quiet: bool) -> None:
    if not quiet:
        sys.stderr.write("  Converting to MP3 with ffmpeg...\n")
    result = subprocess.run(
        ["ffmpeg", "-i", source, "-vn", "-acodec", "libmp3lame", "-q:a", "2", dest],
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError("ffmpeg conversion to MP3 failed.")
    os.remove(source)


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
        for fmt in sorted(all_formats, key=_format_sort_key):
            fmt_id = fmt.get("itag", "N/A")
            ext = fmt.get("ext", "N/A")
            quality = _format_quality_label(fmt)
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
) -> str:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    info = get_video_info(normalized_url)

    formats = info.get("formats", [])
    adaptive = info.get("adaptive_formats", [])
    if not formats and not adaptive:
        raise ValueError("No downloadable formats found for this video.")

    selected, is_progressive = _select_video_format(formats, adaptive)
    if selected is None:
        raise ValueError("No downloadable video format found for this video.")

    if not is_progressive and not quiet:
        print(
            "Warning: No progressive format available. "
            "Downloaded stream contains video only; audio is not included.",
            file=sys.stderr,
        )

    stream_url = selected.get("url")
    if not stream_url:
        raise ValueError("Selected format has no downloadable URL.")

    video_id = info.get("id") or extract_video_id(url) or "unknown"
    title = info.get("title") or video_id
    ext = selected.get("ext") or "mp4"
    filename = _safe_filename(title, video_id, ext)
    dest = os.path.join(output_path, filename)

    if not quiet:
        quality = _format_quality_label(selected)
        print(f"  Downloading video ({quality})...")

    _download_stream(stream_url, dest, quiet)
    return dest


def download_audio(
    url: str,
    output_path: str = ".",
    quiet: bool = False,
) -> str:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    info = get_video_info(normalized_url)

    formats = info.get("formats", [])
    adaptive = info.get("adaptive_formats", [])
    if not formats and not adaptive:
        raise ValueError("No downloadable formats found for this video.")

    selected = _select_audio_format(formats, adaptive)
    if selected is None:
        raise ValueError("No downloadable audio format found for this video.")

    stream_url = selected.get("url")
    if not stream_url:
        raise ValueError("Selected format has no downloadable URL.")

    video_id = info.get("id") or extract_video_id(url) or "unknown"
    title = info.get("title") or video_id
    source_ext = selected.get("ext") or "m4a"
    source_filename = _safe_filename(title, video_id, source_ext)
    source_dest = os.path.join(output_path, source_filename)

    if not quiet:
        abr = selected.get("averageBitrate") or selected.get("abr") or "?"
        print(f"  Downloading audio (~{abr}kbps)...")

    _download_stream(stream_url, source_dest, quiet)

    if _HAS_FFMPEG:
        mp3_filename = _safe_filename(title, video_id, "mp3")
        mp3_dest = os.path.join(output_path, mp3_filename)
        _convert_to_mp3(source_dest, mp3_dest, quiet)
        return mp3_dest

    return source_dest


def print_video_info(url: str) -> None:
    info = get_video_info_wrapper(url)
    _print_metadata(info)
