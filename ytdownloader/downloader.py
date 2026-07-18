"""
<<<<<<< HEAD
Core download logic using pure Python requests for stream downloading.

This module fetches video metadata via the existing metadata extraction pipeline
and downloads the best available stream directly with requests, removing the
yt-dlp dependency entirely.
Core download logic for ytdownloader - native YouTube video and audio downloader.

This module provides the main public API for downloading YouTube videos and
audio streams entirely without yt-dlp or any other external download library.
It uses the package's own modules for all operations:

- :func:`get_video_info` - Fetch and parse video metadata via
  :mod:`ytdownloader.html_extractor` and :mod:`ytdownloader.player_response`
- :func:`download_video` - Download the best video stream, merging separate
  audio and video tracks when needed via :mod:`ytdownloader.merger`
- :func:`download_audio` - Download audio-only streams
- :func:`print_video_info` - Display comprehensive video metadata

Stream URL resolution, format selection, and chunked downloading are all
implemented natively within this module using :mod:`ytdownloader.http_client`.

Typical usage::

    from ytdownloader.downloader import download_video, download_audio, get_video_info

    info = get_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    path = download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", quality="720p")
    path = download_audio("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
<<<<<<< HEAD
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .config import YTConfig, get_default_config
from .exceptions import (
    AgeRestrictedError,
    DownloadError,
    FormatSelectionError,
    GeoRestrictedError,
    InvalidURLError,
    MergeError,
    MetadataExtractionError,
    NetworkError,
    StreamResolutionError,
    VideoUnavailableError,
    YTDLException,
)
from .html_extractor import (
    extract_player_response_with_retry,
    extract_streaming_data,
    get_playability_reason,
    get_playability_status,
    get_video_details,
    is_age_restricted,
    is_geo_restricted,
    is_video_playable,
)
from .http_client import HttpClient, ProgressCallback, build_client
from .logger import (
    debug_log_request,
    debug_log_response,
    get_logger,
    log_critical,
    log_download_complete,
    log_download_progress,
    log_download_start,
    log_error,
    log_extract_start,
    log_extract_success,
    log_format_found,
    log_warning,
)
from .merger import merge_audio_video
from .player_response import parse_player_response
from .utils import is_valid_youtube_url, normalize_youtube_url

logger = get_logger(__name__)

_HAS_FFMPEG: Optional[bool] = None

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

<<<<<<< HEAD
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
def _check_ffmpeg() -> bool:
    global _HAS_FFMPEG
    if _HAS_FFMPEG is None:
        _HAS_FFMPEG = shutil.which("ffmpeg") is not None
    return _HAS_FFMPEG


# ---------------------------------------------------------------------------
# VideoInfo dataclass
# ---------------------------------------------------------------------------


@dataclass
class VideoInfo:
    """Structured container for YouTube video metadata and streaming data.

    Attributes:
        video_id: 11-character YouTube video identifier.
        title: Video title string.
        author: Channel name.
        channel_id: Channel ID string.
        duration: Duration in seconds as integer.
        view_count: View count as integer.
        like_count: Like count as integer.
        description: Video description text.
        thumbnail_urls: List of thumbnail URL dicts.
        keywords: List of keyword strings.
        is_live: Whether the video is a live broadcast.
        is_private: Whether the video is private.
        upload_date: ISO date string (YYYY-MM-DD) or empty.
        formats: List of combined audio+video format dicts.
        adaptive_formats: List of separate audio/video format dicts.
        captions: List of caption track dicts.
        playability_status: Dict with 'status' and 'reason' keys.
        live_broadcast_details: Dict with live broadcast metadata.
        availability: Availability status string.
    """

    video_id: str = ""
    title: str = ""
    author: str = ""
    channel_id: str = ""
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    description: str = ""
    thumbnail_urls: List[Dict[str, Any]] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    is_live: bool = False
    is_private: bool = False
    upload_date: str = ""
    formats: List[Dict[str, Any]] = field(default_factory=list)
    adaptive_formats: List[Dict[str, Any]] = field(default_factory=list)
    captions: List[Dict[str, Any]] = field(default_factory=list)
    playability_status: Dict[str, Any] = field(default_factory=dict)
    live_broadcast_details: Dict[str, Any] = field(default_factory=dict)
    availability: str = ""

    @property
    def all_formats(self) -> List[Dict[str, Any]]:
        return self.formats + self.adaptive_formats

    @property
    def duration_str(self) -> str:
        hours, remainder = divmod(self.duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def view_count_str(self) -> str:
        if self.view_count is None or self.view_count == 0:
            return "N/A"
        return f"{self.view_count:,}"

    def get_best_format(self, quality: str = "best") -> Optional[Dict[str, Any]]:
        result = _select_format_by_quality(self.all_formats, quality)
        return result[0] if result else None

    def get_audio_only(self) -> Optional[Dict[str, Any]]:
        audio_formats = [f for f in self.adaptive_formats if _is_audio_only_format(f)]
        if not audio_formats:
            return None
        return max(audio_formats, key=_audio_format_rank)

    def get_video_only(self, quality: str = "best") -> Optional[Dict[str, Any]]:
        video_formats = [f for f in self.adaptive_formats if _is_video_only_format(f)]
        if not video_formats:
            return None
        selected, _ = _select_format_by_quality(video_formats, quality)
        return selected

    def get_thumbnail_url(self, quality: str = "default") -> str:
        if not self.thumbnail_urls:
            return ""
        quality_priority = {
            "maxres": 5, "sddefault": 4, "hqdefault": 3,
            "mqdefault": 2, "default": 1,
        }
        priority = quality_priority.get(quality, 0)
        candidates = [
            t for t in self.thumbnail_urls
            if t.get("quality", "").lower() == quality.lower()
        ]
        if candidates:
            return candidates[-1].get("url", "")
        if self.thumbnail_urls:
            return self.thumbnail_urls[-1].get("url", "")
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "channel_id": self.channel_id,
            "duration": self.duration,
            "duration_str": self.duration_str,
            "view_count": self.view_count,
            "view_count_str": self.view_count_str,
            "like_count": self.like_count,
            "description": self.description,
            "thumbnail_urls": self.thumbnail_urls,
            "keywords": self.keywords,
            "is_live": self.is_live,
            "is_private": self.is_private,
            "upload_date": self.upload_date,
            "formats": self.formats,
            "adaptive_formats": self.adaptive_formats,
            "captions": self.captions,
            "playability_status": self.playability_status,
            "live_broadcast_details": self.live_broadcast_details,
            "availability": self.availability,
        }

    def __repr__(self) -> str:
        return (
            f"VideoInfo(video_id={self.video_id!r}, title={self.title!r}, "
            f"author={self.author!r}, duration={self.duration_str!r}, "
            f"quality={self.view_count_str!r} views)"
        )


# ---------------------------------------------------------------------------
# VideoInfo factory
# ---------------------------------------------------------------------------


def _build_video_info(player_response: Dict[str, Any]) -> VideoInfo:
    video_details = get_video_details(player_response)
    streaming_raw = extract_streaming_data(player_response)
    microformat_raw = player_response.get("microformat", {})
    if isinstance(microformat_raw, dict):
        mf_renderer = microformat_raw.get("playerMicroformatRenderer", {})
    else:
        mf_renderer = {}

    def _get_detail(key: str, default: Any = None) -> Any:
        val = video_details.get(key)
        return val if val is not None else default

    def _safe_int(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    thumbnails: List[Dict[str, Any]] = []
    raw_thumbs = _get_detail("thumbnail", {})
    if isinstance(raw_thumbs, dict):
        thumbnails = raw_thumbs.get("thumbnails", [])

    keywords = _get_detail("keywords", [])
    if not isinstance(keywords, list):
        keywords = []

    length_secs = _safe_int(_get_detail("lengthSeconds"), 0)
    view_count = _safe_int(_get_detail("viewCount"), 0)
    like_count = _safe_int(_get_detail("likeCount"), 0)

    upload_date = ""
    if isinstance(mf_renderer, dict):
        upload_date = mf_renderer.get("publishDate", "") or ""

    captions: List[Dict[str, Any]] = []
    captions_node = (
        player_response.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    if isinstance(captions_node, list):
        for track in captions_node:
            if isinstance(track, dict):
                captions.append({
                    "url": track.get("baseUrl", "") or track.get("url", ""),
                    "lang_code": track.get("languageCode", ""),
                    "lang": track.get("name", {}).get("simpleText", "") if isinstance(track.get("name"), dict) else track.get("name", ""),
                    "is_auto": track.get("kind", "") == "asr",
                    "is_translated": track.get("is_translatable", False),
                })

    playability = get_playability_status(player_response)

    live_details: Dict[str, Any] = {}
    if _get_detail("isLive"):
        live_details = {
            "is_live": True,
            "is_live_now": _get_detail("isLiveNow"),
            "scheduled_start_time": _safe_int(
                video_details.get("liveBroadcastDetails", {}).get("scheduledStartTime")
            ),
            "scheduled_end_time": _safe_int(
                video_details.get("liveBroadcastDetails", {}).get("scheduledEndTime")
            ),
            "concurrent_viewers": _safe_int(
                video_details.get("liveBroadcastDetails", {}).get("concurrentViewers")
            ),
        }

    availability = playability.get("status", "")

    return VideoInfo(
        video_id=_get_detail("videoId", ""),
        title=_get_detail("title", ""),
        author=_get_detail("author", ""),
        channel_id=_get_detail("channelId", ""),
        duration=length_secs,
        view_count=view_count,
        like_count=like_count,
        description=_get_detail("shortDescription", ""),
        thumbnail_urls=thumbnails,
        keywords=keywords,
        is_live=bool(_get_detail("isLive", False)),
        is_private=bool(_get_detail("isPrivate", False)),
        upload_date=upload_date,
        formats=list(streaming_raw.get("formats", []) or []),
        adaptive_formats=list(streaming_raw.get("adaptiveFormats", []) or []),
        captions=captions,
        playability_status=dict(playability),
        live_broadcast_details=live_details,
        availability=availability,
    )


# ---------------------------------------------------------------------------
# Format selection (replaces format_selector.select_format)
# ---------------------------------------------------------------------------


def _is_audio_only_format(fmt: Dict[str, Any]) -> bool:
    vcodec = (fmt.get("vcodec") or "").lower()
    acodec = (fmt.get("acodec") or "").lower()
    return vcodec in ("none", "") and acodec not in ("none", "")


def _is_video_only_format(fmt: Dict[str, Any]) -> bool:
    vcodec = (fmt.get("vcodec") or "").lower()
    acodec = (fmt.get("acodec") or "").lower()
    return vcodec not in ("none", "") and acodec in ("none", "")


def _is_combined_format(fmt: Dict[str, Any]) -> bool:
    vcodec = (fmt.get("vcodec") or "").lower()
    acodec = (fmt.get("acodec") or "").lower()
    return vcodec not in ("none", "") and acodec not in ("none", "")


def _audio_format_rank(fmt: Dict[str, Any]) -> Tuple[int, int]:
    abr = fmt.get("abr") or 0
    try:
        abr = int(abr)
    except (TypeError, ValueError):
        abr = 0
    content_length = fmt.get("contentLength") or 0
    try:
        content_length = int(content_length)
    except (TypeError, ValueError):
        content_length = 0
    return (-abr, -content_length)


def _video_format_rank(fmt: Dict[str, Any]) -> Tuple[int, int, int]:
    height = fmt.get("height") or 0
    try:
        height = int(height)
    except (TypeError, ValueError):
        height = 0
    tbr = fmt.get("tbr") or 0
    try:
        tbr = int(tbr)
    except (TypeError, ValueError):
        tbr = 0
    content_length = fmt.get("contentLength") or 0
    try:
        content_length = int(content_length)
    except (TypeError, ValueError):
        content_length = 0
    return (-height, -tbr, -content_length)


def _select_format_by_quality(
    streams: List[Dict[str, Any]],
    quality: str = "best",
    allow_audio_only: bool = False,
    allow_video_only: bool = False,
) -> Tuple[Optional[Dict[str, Any]], str]:
    if not streams:
        return None, "no_streams_available"

    combined = [s for s in streams if _is_combined_format(s)]
    video_only = [s for s in streams if _is_video_only_format(s)]
    audio_only = [s for s in streams if _is_audio_only_format(s)]

    quality_normalized = quality.strip().lower()
    quality_heights = {
        "best": float("inf"), "4k": 2160, "1440p": 1440, "1080p": 1080,
        "720p": 720, "480p": 480, "360p": 360, "240p": 240, "144p": 144,
    }
    max_height = quality_heights.get(quality_normalized, float("inf"))

    if quality_normalized == "best":
        if combined:
            best = max(combined, key=_video_format_rank)
            return best, "best_combined"
        if video_only and allow_video_only:
            best = max(video_only, key=_video_format_rank)
            return best, "best_video_only"
        if streams:
            best = max(streams, key=_video_format_rank)
            return best, "best_available"

    matching_combined = [
        s for s in combined
        if (s.get("height") or 0) <= max_height
    ]
    if matching_combined:
        best = max(matching_combined, key=_video_format_rank)
        return best, f"combined_{quality}"

    matching_video = [
        s for s in video_only
        if (s.get("height") or 0) <= max_height
    ]
    if matching_video and allow_video_only:
        best = max(matching_video, key=_video_format_rank)
        return best, f"video_only_{quality}"

    if combined:
        best = min(
            combined,
            key=lambda s: abs((s.get("height") or 0) - max_height)
        )
        return best, f"combined_fallback_{s.get('height', '?')}p"

    if streams:
        return streams[0], "first_available"

    return None, "no_match"


def select_format(
    streams: List[Dict[str, Any]],
    quality: str = "best",
    allow_audio_only: bool = False,
    allow_video_only: bool = False,
) -> Dict[str, Any]:
    selected, reason = _select_format_by_quality(
        streams, quality, allow_audio_only, allow_video_only
    )
    if selected is None:
        raise FormatSelectionError(
            f"No suitable format found for quality='{quality}'. "
            f"Available streams: {len(streams)}."
        )
    height = selected.get("height") or 0
    itag = selected.get("itag", "?")
    size_str = _format_size(selected.get("contentLength"))
    logger.info("Selected itag=%d (%s, %sp, %s) - reason: %s",
                itag, selected.get("ext", "?"), height, size_str, reason)
    log_format_found(itag, f"{height}p", _safe_content_length(selected.get("contentLength")))
    return selected


def _safe_content_length(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_size(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "unknown"
    try:
        size_bytes = int(num_bytes)
        if size_bytes > 1024 * 1024 * 1024:
            return f"{size_bytes / (1024**3):.1f} GB"
        elif size_bytes > 1024 * 1024:
            return f"{size_bytes / (1024**2):.1f} MB"
        elif size_bytes > 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"
    except (TypeError, ValueError):
        return "unknown"


def list_available_formats(streams: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for fmt in streams:
        itag = fmt.get("itag", "?")
        vcodec = fmt.get("vcodec", "") or ""
        acodec = fmt.get("acodec", "") or ""
        height = fmt.get("height") or 0
        width = fmt.get("width") or 0
        ext = fmt.get("ext", "?")
        size_str = _format_size(fmt.get("contentLength"))
        protocol = fmt.get("protocol", "?")
        if vcodec == "none" and acodec != "none":
            fmt_type = f"audio {fmt.get('abr', '?')}kbps"
        elif vcodec != "none" and acodec == "none":
            fmt_type = f"video {width}x{height}"
        elif vcodec != "none" and acodec != "none":
            fmt_type = f"combined {width}x{height}"
        else:
            fmt_type = "unknown"
        lines.append(f"  itag={itag:<5} {fmt_type:<30} {ext:<6} {size_str:<12} {protocol}")
    return lines


# ---------------------------------------------------------------------------
# Stream URL resolution (replaces url_builder + stream_resolver)
# ---------------------------------------------------------------------------


def _build_stream_url(
    base_url: str,
    signature: Optional[str] = None,
    sp: Optional[str] = None,
    n_value: Optional[str] = None,
    extra_params: Optional[Dict[str, str]] = None,
) -> str:
    if not base_url:
        raise StreamResolutionError("Cannot build stream URL: base_url is empty.")

    url = base_url

    if signature and sp:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{sp}={signature}"

    if n_value:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}n={n_value}"

    if extra_params:
        for key, value in extra_params.items():
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{key}={value}"

    return url


def _sanitize_url(url: str) -> str:
    if not url:
        return url
    tracking_params = {
        "noclen", "n", "ns", "nwid", "aia", "alr", "vprv",
        "source", "ref", " Feature", "feature",
    }
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    pairs = query.split("&")
    clean_pairs = [
        p for p in pairs
        if "=" in p and p.split("=", 1)[0].lower() not in tracking_params
    ]
    if not clean_pairs:
        return base
    return f"{base}?{'&'.join(clean_pairs)}"


def _validate_stream_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    return url.startswith("http://") or url.startswith("https://")


def _is_youtube_stream_url(url: str) -> bool:
    if not url:
        return False
    hostname = _extract_host(url)
    youtube_hosts = {
        "googlevideo.com", "www.googlevideo.com",
        "youtube.com", "www.youtube.com",
        "ytimg.com", "i.ytimg.com",
    }
    return hostname in youtube_hosts or "googlevideo" in hostname


def _extract_host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def _resolve_stream_url(fmt: Dict[str, Any]) -> str:
    url = fmt.get("url")
    if url:
        return _sanitize_url(str(url))

    cipher = fmt.get("signatureCipher")
    if not cipher:
        raise StreamResolutionError(
            f"Format itag={fmt.get('itag', '?')} has neither 'url' nor 'signatureCipher'."
        )

    try:
        from urllib.parse import parse_qs, urlencode, urlparse
        params = parse_qs(cipher)
        resolved = {}
        for key, values in params.items():
            resolved[key] = values[0] if values else ""

        sig = resolved.get("s", "")
        sp = resolved.get("sp", "signature")
        base_url = resolved.get("url", "")
        n_value = resolved.get("n", "")

        if not base_url:
            raise StreamResolutionError(
                f"Cipher for itag={fmt.get('itag', '?')} missing 'url' parameter."
            )

        return _build_stream_url(base_url, signature=sig, sp=sp, n_value=n_value or None)
    except StreamResolutionError:
        raise
    except Exception as exc:
        raise StreamResolutionError(
            f"Failed to decode signatureCipher for itag={fmt.get('itag', '?')}: {exc}"
        ) from exc


def _resolve_streams(
    formats: List[Dict[str, Any]],
    adaptive_formats: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    resolved_formats: List[Dict[str, Any]] = []
    resolved_adaptive: List[Dict[str, Any]] = []

    for fmt in formats:
        try:
            url = _resolve_stream_url(fmt)
            resolved = dict(fmt)
            resolved["url"] = url
            resolved_formats.append(resolved)
        except StreamResolutionError as exc:
            logger.warning("Skipping format itag=%s: %s", fmt.get("itag", "?"), exc)

    for fmt in adaptive_formats:
        try:
            url = _resolve_stream_url(fmt)
            resolved = dict(fmt)
            resolved["url"] = url
            resolved_adaptive.append(resolved)
        except StreamResolutionError as exc:
            logger.warning("Skipping adaptive format itag=%s: %s", fmt.get("itag", "?"), exc)

    return resolved_formats, resolved_adaptive


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------


def _compute_output_path(
    output_path: str,
    info: Dict[str, Any],
    is_audio: bool = False,
    audio_format: str = "mp3",
) -> str:
    title = info.get("title", "") or "download"
    video_id = info.get("id", "") or info.get("video_id", "") or "unknown"
    safe_title = "".join(
        c if c.isalnum() or c in " -_." else "_" for c in title
    ).strip()
    safe_title = safe_title[:100] or "download"

    if is_audio:
        ext = audio_format or "mp3"
    else:
        ext = "mp4"

    filename = f"{safe_title} [{video_id}].{ext}"
    return str(Path(output_path) / filename)


def _make_output_path(output_path: str, extension: str) -> str:
    output_abs = os.path.abspath(output_path)
    output_dir = os.path.dirname(output_abs)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    if not output_abs.lower().endswith(f".{extension}"):
        output_abs = f"{output_abs}.{extension}"
    return output_abs


# ---------------------------------------------------------------------------
# get_video_info
# ---------------------------------------------------------------------------


def get_video_info(url: str) -> VideoInfo:
    if not is_valid_youtube_url(url):
        raise InvalidURLError(
            f"Invalid YouTube URL: {url}\n"
            "Supported formats:\n"
            "  - https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  - https://youtu.be/VIDEO_ID\n"
            "  - https://www.youtube.com/shorts/VIDEO_ID\n"
            "  - https://www.youtube.com/embed/VIDEO_ID"
        )

    normalized_url = normalize_youtube_url(url)
    log_extract_start(normalized_url)
    logger.info("Fetching player response for video info.")

    try:
        player_response = extract_player_response_with_retry(normalized_url)
    except Exception as exc:
        logger.error("Failed to extract player response: %s", exc)
        raise MetadataExtractionError(
            f"Could not extract video data from {normalized_url}: {exc}",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc

    if not isinstance(player_response, dict):
        raise MetadataExtractionError(
            "Player response is not a valid JSON object. The video may be unavailable."
        )

    try:
        parsed = parse_player_response(player_response)
    except Exception as exc:
        logger.error("Player response validation failed: %s", exc)
        raise

    playability = get_playability_status(parsed)
    status = playability.get("status", "")

    if status in ("AGE_CHECK_REQUIRED", "AGE_VERIFICATION_REQUIRED", "AGE_GATE"):
        raise AgeRestrictedError(
            f"Age-restricted video: {get_playability_reason(parsed) or 'Age verification required.'}"
        )
    if status == "LOGIN_REQUIRED":
        raise VideoUnavailableError(
            "This video requires login. It may be private or age-restricted."
        )
    if status == "UNPLAYABLE":
        raise VideoUnavailableError(
            f"Video is unplayable: {get_playability_reason(parsed)}"
        )
    if status in ("GEO_RESTRICTED", "AGE_CHECK_REQUIRED_OR_AGE_VERIFICATION_REQUIRED"):
        raise GeoRestrictedError(
            f"Geo-restricted video: {get_playability_reason(parsed)}"
        )

    info = _build_video_info(parsed)
    log_extract_success(info.video_id)
    logger.info("Video info built: title=%r, duration=%ds, formats=%d",
                info.title, info.duration, len(info.all_formats))
    return info


# ---------------------------------------------------------------------------
# print_video_info
# ---------------------------------------------------------------------------


def print_video_info(url: str) -> None:
    if not is_valid_youtube_url(url):
        raise InvalidURLError(f"Invalid YouTube URL: {url}")

    info = get_video_info(url)
    all_formats = info.all_formats

    print(f"\n{'=' * 64}")
    print(f"  Title:        {info.title or 'N/A'}")
    print(f"  Video ID:     {info.video_id or 'N/A'}")
    print(f"  Author:       {info.author or 'N/A'}")
    print(f"  Channel ID:   {info.channel_id or 'N/A'}")
    print(f"  Duration:     {info.duration_str}")
    print(f"  Upload Date:  {info.upload_date or 'N/A'}")
    print(f"  Views:        {info.view_count_str}")
    print(f"  Live:         {'Yes' if info.is_live else 'No'}")
    print(f"  Private:      {'Yes' if info.is_private else 'No'}")
    print(f"  Availability: {info.availability or 'N/A'}")
    print(f"{'=' * 64}")

    if info.keywords:
        print(f"  Keywords:     {', '.join(info.keywords[:10])}")

    if info.thumbnail_urls:
        best_thumb = info.thumbnail_urls[-1].get("url", "N/A")
        print(f"  Thumbnail:    {best_thumb}")

    if all_formats:
        print(f"\n  Available Formats ({len(all_formats)}):")
        print(f"  {'ID':<8} {'Type':<16} {'Quality':<12} {'Size':<12} {'Protocol'}")
        print(f"  {'-' * 64}")
        sorted_formats = sorted(
            all_formats,
            key=lambda f: (
                0 if _is_combined_format(f) else
                1 if _is_video_only_format(f) else 2,
                -(f.get("height") or 0),
                -(f.get("tbr") or 0),
            ),
        )
        for fmt in sorted_formats:
            _print_format_row(fmt)
    print()


def _print_format_row(fmt: Dict[str, Any]) -> None:
    itag = str(fmt.get("itag", "?"))
    vcodec = (fmt.get("vcodec") or "").lower()
    acodec = (fmt.get("acodec") or "").lower()

    if vcodec == "none" and acodec != "none":
        fmt_type = "audio"
        quality = f"{fmt.get('abr', '?')}kbps"
    elif vcodec != "none" and acodec == "none":
        fmt_type = "video"
        height = fmt.get("height") or 0
        width = fmt.get("width") or 0
        quality = f"{width}x{height}" if width else f"{height}p"
    elif vcodec != "none" and acodec != "none":
        fmt_type = "audio+video"
        height = fmt.get("height") or 0
        width = fmt.get("width") or 0
        quality = f"{width}x{height}" if width else f"{height}p"
    else:
        fmt_type = "unknown"
        quality = "?"

    size_str = _format_size(fmt.get("contentLength"))
    protocol = fmt.get("protocol", "?") or "?"
    print(f"  {itag:<8} {fmt_type:<16} {quality:<12} {size_str:<12} {protocol}")


# ---------------------------------------------------------------------------
# download_video
# ---------------------------------------------------------------------------


def download_video(
    url: str,
    output_path: str = ".",
    quality: str = "best",
    quiet: bool = False,
    resume: bool = False,
    proxy: Optional[str] = None,
    cookies: Optional[str] = None,
    audio_format: str = "mp3",
    format_itag: Optional[int] = None,
    no_playlist: bool = False,
    subtitle_lang: Optional[str] = None,
) -> str:
    if not is_valid_youtube_url(url):
        raise InvalidURLError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
<<<<<<< HEAD
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
    logger.info("download_video called: url=%s, quality=%s, output=%s, resume=%s",
                normalized_url, quality, output_path, resume)

    info = get_video_info(normalized_url)

    if not is_video_playable({"playabilityStatus": info.playability_status}):
        reason = info.playability_status.get("reason", "Unknown reason")
        raise VideoUnavailableError(f"Video is not playable: {reason}")

    output_file = _compute_output_path(output_path, info.to_dict(), is_audio=False)

    client = _build_http_client(proxy=proxy, cookies=cookies)
    try:
        if format_itag is not None:
            fmt = _find_format_by_itag(info.all_formats, format_itag)
            if fmt is None:
                raise FormatSelectionError(f"No format found with itag={format_itag}.")
            selected_fmt = fmt
            selection_reason = f"explicit_itag_{format_itag}"
        else:
            selected_fmt, selection_reason = _select_format_by_quality(
                info.all_formats, quality, allow_video_only=True
            )
            if selected_fmt is None:
                raise FormatSelectionError(
                    f"No suitable video format found for quality='{quality}'."
                )

        log_format_found(
            selected_fmt.get("itag", 0),
            f"{selected_fmt.get('height', '?')}p",
            _safe_content_length(selected_fmt.get("contentLength")),
        )
        logger.info("Selected format (reason=%s): itag=%s, height=%sp, ext=%s",
                    selection_reason,
                    selected_fmt.get("itag", "?"),
                    selected_fmt.get("height", "?"),
                    selected_fmt.get("ext", "?"))

        vcodec = (selected_fmt.get("vcodec") or "").lower()
        acodec = (selected_fmt.get("acodec") or "").lower()
        needs_merge = vcodec != "none" and acodec == "none"

        if needs_merge:
            return _download_and_merge(
                client, selected_fmt, info, output_file, audio_format, quiet
            )
        else:
            stream_url = _resolve_stream_url(selected_fmt)
            return _download_stream_to_file(
                client, stream_url, output_file,
                selected_fmt, quiet, resume,
            )
    finally:
        client.close()


def _find_format_by_itag(
    streams: List[Dict[str, Any]], itag: int
) -> Optional[Dict[str, Any]]:
    for fmt in streams:
        try:
            if int(fmt.get("itag", -1)) == itag:
                return fmt
        except (TypeError, ValueError):
            continue
    return None


def _download_and_merge(
    client: HttpClient,
    video_fmt: Dict[str, Any],
    info: VideoInfo,
    output_file: str,
    audio_format: str,
    quiet: bool,
) -> str:
    video_itag = video_fmt.get("itag", "?")
    logger.info("Download requires merge: video itag=%s", video_itag)

    audio_fmt = _find_matching_audio_format(info.adaptive_formats, video_fmt)
    if audio_fmt is None:
        audio_only_formats = [f for f in info.adaptive_formats if _is_audio_only_format(f)]
        if audio_only_formats:
            audio_fmt = max(audio_only_formats, key=_audio_format_rank)
            logger.info("No matching audio for itag=%s; using best audio itag=%s",
                        video_itag, audio_fmt.get("itag", "?"))
        else:
            raise DownloadError(
                f"Cannot merge video itag={video_itag}: no audio formats available."
            )

    tmp_dir = os.path.dirname(os.path.abspath(output_file)) or "."
    base_name = Path(output_file).stem
    video_tmp = os.path.join(tmp_dir, f"{base_name}_video_tmp.mp4")
    audio_tmp = os.path.join(tmp_dir, f"{base_name}_audio_tmp.{audio_format}")

    try:
        video_url = _resolve_stream_url(video_fmt)
        logger.info("Downloading video-only stream to: %s", video_tmp)
        _download_stream_to_file(client, video_url, video_tmp, video_fmt, quiet, False)

        audio_url = _resolve_stream_url(audio_fmt)
        logger.info("Downloading audio-only stream to: %s", audio_tmp)
        _download_stream_to_file(client, audio_url, audio_tmp, audio_fmt, quiet, False)

        logger.info("Merging video and audio streams.")
        merged = merge_audio_video(video_tmp, audio_tmp, output_file)
        logger.info("Merge complete: %s", merged)
        return merged
    finally:
        _cleanup_temp(video_tmp)
        _cleanup_temp(audio_tmp)


def _find_matching_audio_format(
    adaptive_formats: List[Dict[str, Any]], video_fmt: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    audio_formats = [f for f in adaptive_formats if _is_audio_only_format(f)]
    if not audio_formats:
        return None
    video_height = video_fmt.get("height") or 0
    video_tbr = video_fmt.get("tbr") or 0
    best: Optional[Dict[str, Any]] = None
    best_score = float("inf")
    for af in audio_formats:
        height_diff = abs((af.get("height") or 0) - video_height)
        tbr = af.get("tbr") or 0
        score = height_diff + abs(tbr - video_tbr) * 0.01
        if score < best_score:
            best_score = score
            best = af
    return best


def _download_stream_to_file(
    client: HttpClient,
    stream_url: str,
    output_file: str,
    fmt: Dict[str, Any],
    quiet: bool,
    resume: bool,
) -> str:
    expected_size = _safe_content_length(fmt.get("contentLength"))
    output_abs = os.path.abspath(output_file)

    if not quiet:
        size_str = f" ({_format_size(expected_size)})" if expected_size else ""
        print(f"Downloading{size_str} -> {output_abs}")

    logger.info("Stream URL: %s", stream_url[:120])

    headers: Dict[str, str] = {
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com",
    }

    progress_callback: Optional[ProgressCallback] = None
    if not quiet:
        def _progress(downloaded: int, total: Optional[int], speed: float) -> None:
            log_download_progress(downloaded, total, speed)
        progress_callback = _progress

    log_download_start(stream_url, output_abs, expected_size)

    try:
        total = client.download_stream(
            url=stream_url,
            output_path=output_abs,
            expected_size=expected_size,
            progress_callback=progress_callback,
            resume=resume,
            headers=headers,
        )
    except Exception as exc:
        logger.error("Download failed for %s: %s", stream_url, exc)
        raise DownloadError(
            f"Failed to download stream (itag={fmt.get('itag', '?')}): {exc}",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc

<<<<<<< HEAD
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
    log_download_complete(output_abs, total)
    logger.info("Download complete: %s (%d bytes)", output_abs, total)
    return output_abs


def _cleanup_temp(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
            logger.debug("Removed temp file: %s", path)
    except OSError as exc:
        logger.warning("Failed to remove temp file '%s': %s", path, exc)


# ---------------------------------------------------------------------------
# download_audio
# ---------------------------------------------------------------------------


def download_audio(
    url: str,
    output_path: str = ".",
    quiet: bool = False,
    resume: bool = False,
    proxy: Optional[str] = None,
    cookies: Optional[str] = None,
    audio_format: str = "mp3",
) -> str:
    if not is_valid_youtube_url(url):
        raise InvalidURLError(f"Invalid YouTube URL: {url}")

    normalized_url = normalize_youtube_url(url)
<<<<<<< HEAD
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
    logger.info("download_audio called: url=%s, output=%s, format=%s, resume=%s",
                normalized_url, output_path, audio_format, resume)

    info = get_video_info(normalized_url)

    if not is_video_playable({"playabilityStatus": info.playability_status}):
        reason = info.playability_status.get("reason", "Unknown reason")
        raise VideoUnavailableError(f"Video is not playable: {reason}")

    client = _build_http_client(proxy=proxy, cookies=cookies)
    try:
        selected_fmt, selection_reason = _select_format_by_quality(
            info.all_formats, "best", allow_audio_only=True
        )
        if selected_fmt is None:
            raise FormatSelectionError("No audio format available for this video.")

        if not _is_audio_only_format(selected_fmt):
            video_only = [f for f in info.adaptive_formats if _is_video_only_format(f)]
            if video_only:
                raise DownloadError(
                    "Only video-only formats were available. "
                    "Audio-only download requires an audio-only stream."
                )

        log_format_found(
            selected_fmt.get("itag", 0),
            f"{selected_fmt.get('abr', '?')}kbps",
            _safe_content_length(selected_fmt.get("contentLength")),
        )
        logger.info("Selected audio format (reason=%s): itag=%s, abr=%skbps, ext=%s",
                    selection_reason,
                    selected_fmt.get("itag", "?"),
                    selected_fmt.get("abr", "?"),
                    selected_fmt.get("ext", "?"))

        stream_url = _resolve_stream_url(selected_fmt)
        ext = audio_format or "mp3"
        output_file = _compute_output_path(output_path, info.to_dict(), is_audio=True)
        if not output_file.lower().endswith(f".{ext}"):
            output_file = str(Path(output_file).with_suffix(f".{ext}"))

        final_path = _download_stream_to_file(
            client, stream_url, output_file, selected_fmt, quiet, resume
        )

        if audio_format == "mp3" and _check_ffmpeg():
            final_path = _convert_to_mp3(final_path, quiet)
        elif audio_format == "mp3" and not _check_ffmpeg() and not quiet:
            logger.warning("ffmpeg not available; keeping native format.")
            if not quiet:
                print("Warning: ffmpeg not found; audio saved in native format.")

        return final_path
    finally:
        client.close()


def _convert_to_mp3(input_path: str, quiet: bool = False) -> str:
    output_path = str(Path(input_path).with_suffix(".mp3"))
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        return input_path

    try:
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [ffmpeg_bin, "-y", "-i", input_path, "-vn", "-acodec", "libmp3lame",
               "-q:a", "2", output_path]
        if quiet:
            subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        else:
            subprocess.run(cmd, timeout=120, check=True)
        os.remove(input_path)
        logger.info("Converted to MP3: %s", output_path)
        return output_path
    except FileNotFoundError:
        logger.warning("ffmpeg not found; cannot convert to MP3.")
        return input_path
    except subprocess.CalledProcessError as exc:
        logger.warning("ffmpeg conversion failed: %s", exc)
        return input_path
    except Exception as exc:
        logger.warning("Unexpected error during MP3 conversion: %s", exc)
        return input_path


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------


def _build_http_client(
    proxy: Optional[str] = None,
    cookies: Optional[str] = None,
) -> HttpClient:
    config = get_default_config()
    if proxy:
        config.proxy = proxy
    if cookies:
        config.cookies_file = cookies

    try:
        _validate_config(config)
    except Exception:
        pass

    client = build_client(config=config)
    if cookies and not client.cookies_loaded:
        try:
            client.load_cookies_from_file(cookies)
        except Exception as exc:
            logger.warning("Could not load cookies file '%s': %s", cookies, exc)

    return client


def _validate_config(config: YTConfig) -> None:
    if config.timeout <= 0:
        raise ValueError(f"timeout must be > 0 (got {config.timeout}).")
    if config.chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0 (got {config.chunk_size}).")
    if config.max_retries < 0:
        raise ValueError(f"max_retries must be >= 0 (got {config.max_retries}).")


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "VideoInfo",
    "download_video",
    "download_audio",
    "get_video_info",
    "print_video_info",
    "select_format",
    "list_available_formats",
    "merge_audio_video",
    "VideoInfo",
]
