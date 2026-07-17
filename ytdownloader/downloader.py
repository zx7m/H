"""
Core download logic using only requests and urllib (no yt-dlp).

Fetches video metadata via metadata.py, resolves stream URLs via
stream_resolver.py, selects the best format for a given quality, and
downloads stream bytes directly with progress indication.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .metadata import MetadataExtractionError, get_video_info
from .stream_resolver import StreamResolutionError, resolve_streams
from .utils import extract_video_id, is_valid_youtube_url, normalize_youtube_url

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _safe_int(value: Any) -> Optional[int]:
    """Safely convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _resolve_stream_url(fmt: Dict[str, Any]) -> Optional[str]:
    """Resolve a direct URL from a format dict, handling signatureCipher."""
    from .stream_resolver import _resolve_cipher_url

    url = fmt.get("url")
    if url:
        return str(url)
    cipher = fmt.get("signatureCipher")
    if cipher:
        try:
            return _resolve_cipher_url(str(cipher))
        except StreamResolutionError:
            return None
    return None


def select_format(streams: List[Dict[str, Any]], quality: Optional[str] = None) -> Dict[str, Any]:
    """Select the best stream matching the requested quality constraint.

    Args:
        streams: A list of stream dicts as returned by ``resolve_streams``.
        quality: Quality string such as ``'best'`` (default), ``'480p'``,
            ``'720p'``, ``'1080p'``, etc.  When a resolution is given the
            highest-quality stream whose ``height`` is **at or below** the
            target is returned.

    Returns:
        The best matching stream dict.

    Raises:
        ValueError: If no streams are provided or no stream matches the
            requested quality constraint.
    """
    if not streams:
        raise ValueError("No streams available to select from.")

    quality = (quality or "best").strip().lower()

    if quality == "best":
        best = max(streams, key=_stream_sort_key)
        return best

    target_height = _parse_quality_to_height(quality)
    if target_height is None:
        best = max(streams, key=_stream_sort_key)
        return best

    candidates = [s for s in streams if _stream_sort_key(s)[1] <= target_height]
    if not candidates:
        raise ValueError(
            f"No stream found with height <= {target_height}p. "
            "Try a lower quality or 'best'."
        )

    best = max(candidates, key=_stream_sort_key)
    return best


def _parse_quality_to_height(quality: str) -> Optional[int]:
    """Convert a quality string like '480p' or '1080p' to an integer height."""
    m = re.match(r"^(\d+)p$", quality)
    if m:
        return int(m.group(1))
    return None


def _stream_sort_key(stream: Dict[str, Any]) -> Tuple[int, int, int]:
    """Sort key that prefers combined formats, then higher resolution, then higher bitrate."""
    height = stream.get("height") or 0
    bitrate = stream.get("bitrate") or 0

    vcodec = (stream.get("vcodec") or "").lower()
    acodec = (stream.get("acodec") or "").lower()

    has_video = vcodec not in ("none", "")
    has_audio = acodec not in ("none", "")

    if has_video and has_audio:
        category = 0
    elif has_video:
        category = 1
    else:
        category = 2

    return (category, height, bitrate)


def _format_size(content_length: Any) -> str:
    """Format a byte count as a human-readable string."""
    if content_length is None:
        return "unknown"
    try:
        size_bytes = int(content_length)
        if size_bytes > 1024 * 1024 * 1024:
            return f"{size_bytes / (1024**3):.1f} GB"
        if size_bytes > 1024 * 1024:
            return f"{size_bytes / (1024**2):.1f} MB"
        if size_bytes > 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"
    except (ValueError, TypeError):
        return "unknown"


def _build_output_filename(
    info: Dict[str, Any],
    stream: Dict[str, Any],
    output_path: str,
) -> str:
    """Build the output file path for a downloaded stream.

    If ``output_path`` is an existing directory, the file is named
    ``<title> [<id>].<ext>`` inside it.  If ``output_path`` points to a
    non-existent path or has a file extension, it is treated as the
    explicit target file path.
    """
    title = info.get("title") or "Unknown"
    video_id = info.get("id") or "unknown"
    ext = stream.get("ext") or "mp4"

    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip() or "Unknown"

    if os.path.isdir(output_path) or not os.path.splitext(output_path)[1]:
        filename = f"{safe_title} [{video_id}].{ext}"
        return os.path.join(output_path, filename)

    return output_path


def _download_stream(
    url: str,
    output_path: str,
    content_length: Optional[int],
    quiet: bool,
    desc: str = "Downloading",
) -> str:
    """Download a single stream URL to output_path using range-based chunked reads."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    temp_path = output_path + ".part"
    downloaded = 0

    if content_length and os.path.exists(temp_path):
        downloaded = os.path.getsize(temp_path)
        if downloaded >= content_length:
            os.replace(temp_path, output_path)
            return output_path

    headers: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    if content_length and downloaded > 0 and downloaded < content_length:
        headers["Range"] = f"bytes={downloaded}-"

    try:
        with requests.get(url, headers=headers, stream=True, timeout=30) as response:
            response.raise_for_status()

            if content_length is None:
                cl = response.headers.get("Content-Length")
                content_length = _safe_int(cl) if cl else None

            mode = "ab" if downloaded > 0 else "wb"
            start_time = time.monotonic()

            with open(temp_path, mode) as fh:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)

                    if not quiet:
                        _print_progress(
                            downloaded,
                            content_length,
                            start_time,
                            desc,
                        )

    except KeyboardInterrupt:
        if os.path.exists(temp_path):
            pass
        raise

    if os.path.exists(temp_path):
        os.replace(temp_path, output_path)

    return output_path


def _print_progress(
    downloaded: int,
    total: Optional[int],
    start_time: float,
    desc: str,
) -> None:
    """Print a single-line progress indicator to stdout."""
    elapsed = time.monotonic() - start_time
    if elapsed <= 0:
        return

    speed = downloaded / elapsed
    speed_str = _format_speed(speed)

    if total:
        pct = min(downloaded / total * 100, 100.0)
        done_str = _format_size(downloaded)
        total_str = _format_size(total)
        eta = _calc_eta(downloaded, total, start_time)
        line = (
            f"\r{desc}: {pct:5.1f}% "
            f"[{done_str}/{total_str}] "
            f"at {speed_str} "
            f"ETA {eta}"
        )
    else:
        done_str = _format_size(downloaded)
        line = f"\r{desc}: {done_str} at {speed_str}"

    sys.stdout.write(line)
    sys.stdout.flush()


def _format_speed(bytes_per_sec: float) -> str:
    """Format a byte-per-second rate as a human-readable string."""
    if bytes_per_sec <= 0:
        return "? B/s"
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024**2):.1f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _calc_eta(downloaded: int, total: int, start_time: float) -> str:
    """Calculate and format ETA from current progress."""
    if downloaded <= 0 or total <= 0:
        return "?"
    elapsed = time.monotonic() - start_time
    if elapsed <= 0:
        return "?"
    speed = downloaded / elapsed
    remaining = (total - downloaded) / speed
    if remaining >= 3600:
        return f"{int(remaining // 3600)}h{int((remaining % 3600) // 60)}m"
    if remaining >= 60:
        return f"{int(remaining // 60)}m{int(remaining % 60)}s"
    return f"{int(remaining)}s"


def _print_newline(quiet: bool) -> None:
    """Print a newline to stdout if not in quiet mode."""
    if not quiet:
        print()


def get_video_info_wrapper(url: str) -> Dict[str, Any]:
    """Fetch and return video info dict after URL validation."""
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
    """Download the best video stream for a YouTube URL.

    Args:
        url: A valid YouTube watch/shorts/embed URL.
        output_path: Directory or file path for the output.  If a directory
            is given the file is named ``<title> [<id>].<ext>``.
        quiet: Suppress progress output when ``True``.

    Returns:
        The path to the downloaded file.

    Raises:
        ValueError: If the URL is not a valid YouTube URL.
        MetadataExtractionError: If video metadata cannot be fetched.
        StreamResolutionError: If no compatible stream URL can be resolved.
    """
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    info = get_video_info(normalized_url)

    streaming_data = info.get("streamingData") or info.get("streaming_data") or {}
    resolved = resolve_streams(streaming_data)

    video_streams = [s for s in resolved if s.get("has_video")]
    if not video_streams:
        raise StreamResolutionError(
            "No video streams found for this video. It may be unavailable."
        )

    stream = select_format(video_streams, quality="best")
    output_file = _build_output_filename(info, stream, output_path)
    stream_url = _resolve_stream_url(stream)

    if not stream_url:
        raise StreamResolutionError(
            f"Could not resolve download URL for itag={stream.get('itag')}."
        )

    expected_size = stream.get("content_length")
    desc = "Downloading video"

    _download_stream(stream_url, output_file, expected_size, quiet, desc)
    _print_newline(quiet)

    return output_file


def download_audio(
    url: str,
    output_path: str = ".",
    quiet: bool = False,
) -> str:
    """Download the best audio-only stream for a YouTube URL.

    Args:
        url: A valid YouTube watch/shorts/embed URL.
        output_path: Directory or file path for the output.  If a directory
            is given the file is named ``<title> [<id>].m4a``.
        quiet: Suppress progress output when ``True``.

    Returns:
        The path to the downloaded file.

    Raises:
        ValueError: If the URL is not a valid YouTube URL.
        MetadataExtractionError: If video metadata cannot be fetched.
        StreamResolutionError: If no compatible audio stream can be resolved.
    """
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
    info = get_video_info(normalized_url)

    streaming_data = info.get("streamingData") or info.get("streaming_data") or {}
    resolved = resolve_streams(streaming_data)

    audio_streams = [s for s in resolved if s.get("has_audio") and not s.get("has_video")]
    if not audio_streams:
        audio_streams = [s for s in resolved if s.get("has_audio")]

    if not audio_streams:
        raise StreamResolutionError(
            "No audio streams found for this video. It may be unavailable."
        )

    stream = select_format(audio_streams, quality="best")
    output_file = _build_output_filename(info, stream, output_path)

    stream_url = _resolve_stream_url(stream)

    if not stream_url:
        raise StreamResolutionError(
            f"Could not resolve download URL for itag={stream.get('itag')}."
        )

    expected_size = stream.get("content_length")
    desc = "Downloading audio"

    _download_stream(stream_url, output_file, expected_size, quiet, desc)
    _print_newline(quiet)

    return output_file


def print_video_info(url: str) -> None:
    """Fetch and print video metadata to stdout.

    Args:
        url: A valid YouTube watch/shorts/embed URL.
    """
    info = get_video_info_wrapper(url)
    _print_metadata(info)


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

    streaming_data = info.get("streamingData") or info.get("streaming_data") or {}
    try:
        all_formats = resolve_streams(streaming_data)
    except StreamResolutionError:
        all_formats = []

    if all_formats:
        print(f"\n  Available Formats ({len(all_formats)}):")
        print(f"  {'ID':<10} {'Type':<12} {'Quality':<15} {'Size':<12} {'Protocol'}")
        print(f"  {'-'*60}")
        for fmt in sorted(all_formats, key=_fmt_sort_key):
            fmt_id = str(fmt.get("itag", "N/A"))
            ext = fmt.get("ext", "N/A")
            quality = _format_quality_label(fmt)
            size_str = _format_size(fmt.get("content_length"))
            protocol = fmt.get("protocol", "N/A")
            print(f"  {fmt_id:<10} {ext:<12} {quality:<15} {size_str:<12} {protocol}")
    print()


def _fmt_sort_key(fmt: Dict[str, Any]) -> Tuple[int, int, int]:
    vcodec = (fmt.get("vcodec") or "").lower()
    acodec = (fmt.get("acodec") or "").lower()
    height = fmt.get("height") or 0
    tbr = fmt.get("tbr") or 0

    if vcodec not in ("none", "") and acodec not in ("none", ""):
        return (0, height, tbr)
    if vcodec not in ("none", ""):
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
