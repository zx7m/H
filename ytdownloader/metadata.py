"""
Metadata extraction from YouTube videos via direct HTML parsing.

Fetches the YouTube watch page, extracts the ``ytInitialPlayerResponse``
JavaScript object literal embedded in the DOM, and returns the parsed dict.
No yt-dlp dependency.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

INITIAL_PLAYER_RESPONSE_PATTERN = re.compile(
    r"(?:var\s+)?ytInitialPlayerResponse\s*=\s*(\{)",
    re.DOTALL,
)

GEO_RESTRICTED_REASONS = {
    "GEO_RESTRICTED",
    "COPYRIGHTED_CONTENT",
    "LIVE_STREAM_OFFLINE",
}

AGE_CHECK_REASONS = {
    "AGE_CHECK_NOT_ALLOWED",
    "AGE_CHECK_REQUIRED",
}


def _extract_json_object(html: str, start: int) -> Optional[str]:
    depth = 0
    in_string = False
    escape_next = False
    i = start
    while i < len(html):
        ch = html[i]
        if escape_next:
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            i += 1
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
        i += 1
    return None


def _extract_initial_player_response(html: str) -> Optional[Dict[str, Any]]:
    match = INITIAL_PLAYER_RESPONSE_PATTERN.search(html)
    if not match:
        return None
    json_str = _extract_json_object(html, match.start(1))
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        return None


class MetadataExtractionError(Exception):
    """Raised when video metadata cannot be extracted from YouTube."""
    pass


def _fetch_page(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.MissingSchema as exc:
        raise MetadataExtractionError(
            f"Invalid URL: {url!r}. Expected a full URL like "
            "'https://www.youtube.com/watch?v=VIDEO_ID'."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise MetadataExtractionError(
            "Connection error. Check your internet connection and try again."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise MetadataExtractionError(
            "Request timed out. YouTube may be slow or unreachable."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        if status_code == 404:
            raise MetadataExtractionError(
                "Video not found (404). The video may have been removed."
            ) from exc
        if status_code == 403:
            raise MetadataExtractionError(
                "Access denied (403). The video may be geo-restricted or private."
            ) from exc
        raise MetadataExtractionError(
            f"HTTP error {status_code} when fetching video page."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise MetadataExtractionError(
            "An error occurred while contacting YouTube. Please try again later."
        ) from exc
    return response.text


def _check_playability(
    status: Dict[str, Any],
) -> Optional[str]:
    status_string = status.get("status", "")
    return status_string if status_string else None


def _validate_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise MetadataExtractionError(
            f"Invalid URL: {url!r}. Expected a full URL starting with http(s)://."
        )
    netloc = urlparse(url).netloc.lower()
    if not (netloc == "youtube.com" or netloc.endswith(".youtube.com") or netloc == "youtu.be"):
        raise MetadataExtractionError(
            f"URL does not appear to be a YouTube link: {url!r}"
        )


def get_video_info(url: str) -> Dict[str, Any]:
    _validate_url(url)

    html = _fetch_page(url)
    player_data = _extract_initial_player_response(html)

    if player_data is None:
        raise MetadataExtractionError(
            "Could not extract video data from page. "
            "The video may be unavailable, geo-restricted, or age-gated."
        )

    playability = player_data.get("playabilityStatus", {})
    status = _check_playability(playability)

    if status in AGE_CHECK_REASONS or not status:
        reason = playability.get("reason", "Age-restricted content.")
        raise MetadataExtractionError(
            f"Age verification required: {reason}"
        )
    if status == "LOGIN_REQUIRED":
        reason = playability.get("reason", "Login required.")
        raise MetadataExtractionError(
            f"This video requires login. It may be age-restricted or private: {reason}"
        )
    if status == "UNPLAYABLE":
        reason = playability.get("reason", "Unknown reason")
        raise MetadataExtractionError(f"Video is unplayable: {reason}")
    if status == "ERROR":
        reason = playability.get("reason", "Unknown error")
        sub_reason = playability.get("errorScreen", {}).get("playerErrorMessage", {})
        sub = sub_reason.get("subreason", {})
        sub_text = sub.get("simpleText") or sub.get("runs", [{}])[0].get("text", "")
        if sub_text:
            reason = f"{reason} — {sub_text}"
        raise MetadataExtractionError(f"YouTube error: {reason}")
    if status == "AGE_RESTRICTED":
        reason = playability.get("reason", "Age-restricted.")
        raise MetadataExtractionError(f"Age-restricted video: {reason}")
    if status in GEO_RESTRICTED_REASONS:
        reason = playability.get("reason", "Content is geo-restricted.")
        raise MetadataExtractionError(f"Geo-restricted content: {reason}")

    video_details = player_data.get("videoDetails", {})
    streaming_data = player_data.get("streamingData", {})
    microformat = (
        player_data.get("microformat", {}).get("playerMicroformatRenderer", {})
    )

    if not video_details:
        raise MetadataExtractionError(
            "Video details not found. The video may have been removed."
        )

    result: Dict[str, Any] = {
        "videoDetails": video_details,
        "streamingData": streaming_data,
        "player_response": player_data,
        "id": video_details.get("videoId"),
        "title": video_details.get("title"),
        "author": video_details.get("author"),
        "channel_id": video_details.get("channelId"),
        "length_seconds": int(video_details.get("lengthSeconds", 0)),
        "duration": _format_duration(video_details.get("lengthSeconds", "0")),
        "view_count": _safe_int(video_details.get("viewCount")),
        "keywords": video_details.get("keywords", []),
        "short_description": video_details.get("shortDescription", ""),
        "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", []),
        "upload_date": microformat.get("publishDate"),
        "live_status": video_details.get("isLiveContent"),
        "is_private": video_details.get("isPrivate"),
        "formats": streaming_data.get("formats", []),
        "adaptiveFormats": streaming_data.get("adaptiveFormats", []),
    }
    return result


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _format_duration(seconds_str: str) -> str:
    try:
        total_seconds = int(seconds_str)
    except (ValueError, TypeError):
        return "0:00"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
