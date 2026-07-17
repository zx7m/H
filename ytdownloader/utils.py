"""
Utility functions for ytdownloader.

This module provides a collection of helper functions used throughout the
ytdownloader package for URL parsing, validation, filename handling, response
inspection, encoding/decoding, and general-purpose data manipulation.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from functools import wraps
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse, unquote


# ---------------------------------------------------------------------------
# YouTube URL constants
# ---------------------------------------------------------------------------

VALID_DOMAINS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

URL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]{11}(?:&|$|\?)"),
    re.compile(r"https?://(?:www\.)?youtube\.com/embed/[A-Za-z0-9_-]{11}(?:[/?]|$)"),
    re.compile(r"https?://(?:www\.)?youtube\.com/v/[A-Za-z0-9_-]{11}(?:[/?]|$)"),
    re.compile(r"https?://youtu\.be/[A-Za-z0-9_-]{11}(?:[/?]|$)"),
    re.compile(r"https?://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]{11}(?:[/?]|$)"),
    re.compile(r"https?://(?:www\.)?youtube\.com/live/[A-Za-z0-9_-]{11}(?:[/?]|$)"),
]

VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def extract_video_id(url: str) -> str | None:
    """Extract a YouTube video ID from a URL.

    Supports standard watch URLs, embed URLs, short URLs, shorts, and live URLs.

    Args:
        url: A YouTube video URL.

    Returns:
        The 11-character video ID if found, otherwise None.
    """
    url = url.strip()
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    v = params.get("v")
    if v and len(v[0]) == 11:
        return v[0]
    match = re.search(r"/(?:embed|v|shorts|live)/([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        candidate = parsed.path.lstrip("/")
        if VIDEO_ID_PATTERN.fullmatch(candidate):
            return candidate
    return None


def is_valid_youtube_url(url: str) -> bool:
    """Validate that a URL is a recognized YouTube URL.

    Args:
        url: The URL string to validate.

    Returns:
        True if the URL is a valid YouTube URL, False otherwise.
    """
    url = url.strip()
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc.lower() not in VALID_DOMAINS:
        return False
    for pattern in URL_PATTERNS:
        if pattern.match(url):
            return True
    return False


def normalize_youtube_url(url: str) -> str:
    """Normalize a YouTube URL to the canonical watch format.

    Converts youtu.be short links, /shorts/, and /live/ paths into the
    standard https://www.youtube.com/watch?v=<VIDEO_ID> format.

    Args:
        url: A YouTube URL in any supported format.

    Returns:
        A normalized YouTube watch URL. If the URL cannot be normalized,
        the original string is returned unchanged.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/")
        if VIDEO_ID_PATTERN.fullmatch(video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
    if "/shorts/" in url or "/live/" in url:
        video_id = extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return url


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------


def sanitize_filename(filename: str) -> str:
    """Remove filesystem-invalid characters from a filename.

    Replaces characters that are invalid in common filesystems with
    underscores and collapses runs of separators.

    Args:
        filename: The raw filename to sanitize.

    Returns:
        A filesystem-safe filename string.
    """
    filename = unicodedata.normalize("NFKD", filename)
    filename = filename.encode("ascii", "ignore").decode("ascii")
    invalid_chars = r'<>:"/\\|?*\x00-\x1f'
    for ch in invalid_chars:
        filename = filename.replace(ch, "_")
    filename = re.sub(r"_+", "_", filename)
    filename = filename.strip("._ ")
    if not filename:
        filename = "untitled"
    return filename


def generate_filename(title: str, video_id: str, ext: str) -> str:
    """Generate a safe, descriptive filename for a downloaded file.

    Args:
        title: The video title string.
        video_id: The YouTube video ID.
        ext: The desired file extension (without a leading dot).

    Returns:
        A sanitized filename in the form '<title>_<video_id>.<ext>'.
    """
    safe_title = sanitize_filename(title)
    if not safe_title:
        safe_title = video_id
    safe_title = safe_title[:100]
    return f"{safe_title}_{video_id}.{ext}"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_file_size(num_bytes: int) -> str:
    """Format a byte count into a human-readable string.

    Args:
        num_bytes: The number of bytes to format.

    Returns:
        A human-readable size string such as '3.2 MB' or '1.1 GB'.
    """
    if num_bytes < 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            if unit == "B":
                return f"{int(num_bytes)} {unit}"
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def format_duration(seconds: int) -> str:
    """Format an integer number of seconds as HH:MM:SS.

    Args:
        seconds: The duration in seconds.

    Returns:
        A zero-padded 'HH:MM:SS' string. Values longer than 24 hours are
        represented without wrapping.
    """
    if seconds < 0:
        seconds = 0
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------------------
# Response inspection helpers
# ---------------------------------------------------------------------------


def is_age_gated(response: dict) -> bool:
    """Determine whether a player response indicates age gating.

    Args:
        response: The ytInitialPlayerResponse dictionary.

    Returns:
        True if the video is age-gated, False otherwise.
    """
    playability = response.get("videoDetails", {})
    return bool(playability.get("isAgeRestricted"))


def is_geo_restricted(response: dict) -> bool:
    """Determine whether a player response indicates geo restriction.

    Args:
        response: The ytInitialPlayerResponse dictionary.

    Returns:
        True if the video is geo-restricted, False otherwise.
    """
    playability = response.get("playabilityStatus", {})
    status = playability.get("status", "")
    if status in ("AGE_CHECK_REQUIRED", "AGE_VERIFICATION_REQUIRED"):
        return False
    if "LIVE_STATUS_OFFLINE" in status:
        return False
    reason = (playability.get("reason") or "").lower()
    geo_indicators = [
        "not available in your country",
        "not available in your region",
    ]
    return any(indicator in reason for indicator in geo_indicators)


def is_live_stream(response: dict) -> bool:
    """Determine whether a player response indicates a live stream.

    Args:
        response: The ytInitialPlayerResponse dictionary.

    Returns:
        True if the video is currently live, False otherwise.
    """
    details = response.get("videoDetails", {})
    return bool(details.get("isLive"))


def is_private_video(response: dict) -> bool:
    """Determine whether a player response indicates a private video.

    Args:
        response: The ytInitialPlayerResponse dictionary.

    Returns:
        True if the video is private, False otherwise.
    """
    details = response.get("videoDetails", {})
    return bool(details.get("isPrivate"))


# ---------------------------------------------------------------------------
# URL encoding / decoding helpers
# ---------------------------------------------------------------------------


def url_encode_params(params: dict) -> str:
    """URL-encode a dictionary into a query string.

    Args:
        params: A dictionary of parameter names to values.

    Returns:
        A URL-encoded query string (without a leading '?').
    """
    return urlencode(params, doseq=True)


def decode_url_params(params_str: str) -> dict[str, list[str]]:
    """Decode a URL query string into a dictionary of parameter lists.

    Args:
        params_str: A raw query string such as 'v=abc&list=xyz'.

    Returns:
        A dictionary mapping parameter names to lists of values.
    """
    params_str = params_str.lstrip("?")
    return dict(parse_qs(params_str, keep_blank_values=True))


# ---------------------------------------------------------------------------
# MIME type helpers
# ---------------------------------------------------------------------------


def parse_mime_type(mime: str) -> dict[str, str | None]:
    """Parse a MIME type string into its component parts.

    Args:
        mime: A MIME type string such as 'video/webm; codecs="vp9"'.

    Returns:
        A dictionary with keys:
        - 'mime': the full MIME type (e.g. 'video/webm')
        - 'container': the top-level type (e.g. 'video')
        - 'subtype': the subtype (e.g. 'webm')
        - 'codecs': the codecs parameter if present, otherwise None
        - 'vcodec': video codec if detected, otherwise None
        - 'acodec': audio codec if detected, otherwise None
    """
    mime = mime.strip()
    parts = mime.split(";")
    main = parts[0].strip().lower()
    main_parts = main.split("/", 1)
    container = main_parts[0] if len(main_parts) > 0 else ""
    subtype = main_parts[1] if len(main_parts) > 1 else ""
    codecs = None
    vcodec = None
    acodec = None
    for part in parts[1:]:
        part = part.strip()
        if part.startswith('codecs="') and part.endswith('"'):
            codecs = part[8:-1]
            codec_list = [c.strip() for c in codecs.split(",")]
            for codec in codec_list:
                if codec.startswith("avc1") or codec.startswith("vp9") or codec.startswith("vp8") or codec == "av01":
                    vcodec = codec
                elif codec.startswith("mp4a") or codec.startswith("opus") or codec.startswith("vorbis"):
                    acodec = codec
    return {
        "mime": main,
        "container": container,
        "subtype": subtype,
        "codecs": codecs,
        "vcodec": vcodec,
        "acodec": acodec,
    }


# ---------------------------------------------------------------------------
# Bitrate and size helpers
# ---------------------------------------------------------------------------


def calculate_bitrate(content_length: int, duration: int) -> float:
    """Calculate the average bitrate in kilobits per second.

    Args:
        content_length: The expected file size in bytes.
        duration: The duration of the stream in seconds.

    Returns:
        The average bitrate in kbps (kilobits per second). Returns 0.0 if
        duration is zero or negative.
    """
    if duration <= 0:
        return 0.0
    bits = content_length * 8
    return bits / duration / 1000.0


# ---------------------------------------------------------------------------
# Data manipulation helpers
# ---------------------------------------------------------------------------


def chunk_list(lst: list[Any], chunk_size: int) -> list[list[Any]]:
    """Split a list into chunks of a given size.

    Args:
        lst: The input list to split.
        chunk_size: The maximum size of each chunk. Must be a positive integer.

    Returns:
        A list of chunks. The last chunk may be smaller than chunk_size.

    Raises:
        ValueError: If chunk_size is not a positive integer.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Safely retrieve a nested value from a dictionary.

    Traverses the dictionary by each key in order. If any intermediate key
    is missing or the current value is not a dictionary, returns the default.

    Args:
        d: The root dictionary to traverse.
        *keys: A sequence of keys representing the nested path.
        default: The value to return if the path does not exist.

    Returns:
        The value at the nested path, or the default if not found.
    """
    current: Any = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
        if current is None:
            return default
    return current


def _normalize_cache_arg(arg: Any) -> str:
    if isinstance(arg, dict):
        return json.dumps(arg, sort_keys=True, default=str)
    if isinstance(arg, (list, tuple)):
        return json.dumps(list(arg), sort_keys=True, default=str)
    return str(arg)


def generate_cache_key(*args: Any) -> str:
    """Generate a deterministic cache key from arbitrary arguments.

    Args:
        *args: Any number of positional arguments to include in the key.

    Returns:
        A hexadecimal MD5 digest string representing the cache key.
    """
    raw = "\x00".join(_normalize_cache_arg(arg) for arg in args)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def retry(
    func=None,
    *,
    max_retries: int = 5,
    delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
):
    """Decorator that retries a function on failure with exponential backoff.

    Can be used with or without arguments::

        @retry
        def unstable(): ...

        @retry(max_retries=3, delay=2.0)
        def unstable(): ...

    Args:
        func: The function to decorate (when used without parentheses).
        max_retries: The maximum number of attempts.
        delay: The initial delay in seconds between retries. The delay doubles
            after each failure (exponential backoff).
        exceptions: A tuple of exception types that should trigger a retry.

    Returns:
        The decorated function.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*fn_args, **fn_kwargs):
            if max_retries < 1:
                raise ValueError("max_retries must be a positive integer")
            last_exception = None
            current_delay = delay
            for attempt in range(1, max_retries + 1):
                try:
                    return fn(*fn_args, **fn_kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt == max_retries:
                        break
                    time.sleep(current_delay)
                    current_delay *= 2
            raise last_exception  # type: ignore[misc]

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
