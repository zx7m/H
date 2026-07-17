"""
Metadata extraction from YouTube videos.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import requests

INITIAL_PLAYER_RESPONSE_PATTERN = re.compile(
    r"var\s+ytInitialPlayerResponse\s*=\s*(\{)",
    re.DOTALL,
)


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
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.text


def _check_playability(status: Dict[str, Any]) -> str:
    status_string = status.get("status", "")
    return status_string


def get_video_info(url: str) -> Dict[str, Any]:
    html = _fetch_page(url)
    player_data = _extract_initial_player_response(html)

    if player_data is None:
        raise MetadataExtractionError(
            "Could not extract video data from page. The video may be unavailable or geo-restricted."
        )

    playability = player_data.get("playabilityStatus", {})
    status = _check_playability(playability)

    if status == "LOGIN_REQUIRED":
        raise MetadataExtractionError(
            "This video requires login. It may be age-restricted or private."
        )
    if status == "UNPLAYABLE":
        reason = playability.get("reason", "Unknown reason")
        raise MetadataExtractionError(f"Video is unplayable: {reason}")
    if status == "AGE_CHECK_REQUIRED":
        raise MetadataExtractionError(
            "Age verification required. This video is age-restricted."
        )
    if status == "AGE_CHECK_NOT_ALLOWED":
        raise MetadataExtractionError(
            "Age-restricted video not allowed."
        )
    if status in ("ERROR", "AGE_RESTRICTED"):
        reason = playability.get("reason", "Unknown error")
        raise MetadataExtractionError(f"YouTube error: {reason}")

    video_details = player_data.get("videoDetails", {})
    microformat = player_data.get("microformat", {}).get("playerMicroformatRenderer", {})

    if not video_details:
        raise MetadataExtractionError("Video details not found. The video may have been removed.")

    streaming_data = player_data.get("streamingData", {})

    result = {
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
        "streaming_data": streaming_data,
        "formats": streaming_data.get("formats", []),
        "adaptive_formats": streaming_data.get("adaptiveFormats", []),
    }
    return result


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _format_duration(seconds_str: str) -> str:
    try:
        total_seconds = int(seconds_str)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
    except (ValueError, TypeError):
        return "0:00"
