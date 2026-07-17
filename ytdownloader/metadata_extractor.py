"""
Comprehensive video metadata extraction module.

This module provides all the tools needed to extract, validate, and format
video metadata from the YouTube player response JSON.  It exposes a single
public facade (:func:`extract_metadata`) alongside granular extractor
functions for each individual field so that callers can pick and choose
what they need.

The extraction logic is defensive by default: missing or malformed fields
are logged at DEBUG level and translated into sensible defaults rather
than raising, unless the caller explicitly opts in to strict mode via the
``strict`` argument.

Public API
----------
All public symbols are declared in ``__all__`` at the bottom of this
module.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from datetime import datetime, timezone

from ytdownloader.exceptions import MetadataExtractionError
from ytdownloader.logger import get_logger

logger = get_logger("metadata_extractor")

__all__ = [
    "MetadataExtractionError",
    "extract_metadata",
    "build_metadata_dict",
    "extract_title",
    "extract_author",
    "extract_channel_id",
    "extract_duration",
    "extract_view_count",
    "extract_like_count",
    "extract_upload_date",
    "extract_description",
    "extract_thumbnail_urls",
    "extract_keywords",
    "extract_categories",
    "extract_is_live",
    "extract_is_private",
    "extract_rating",
    "extract_video_id",
    "extract_channel_name",
    "get_duration_ms",
    "get_channel_info",
    "get_monetization_info",
    "get_live_broadcast_details",
    "get_availability",
    "get_format_summary",
    "get_video_id_from_canonical",
    "format_duration",
    "format_view_count",
    "format_upload_date",
    "format_file_size",
    "format_description_preview",
    "MetadataRaw",
    "MetadataSummary",
]


# ---------------------------------------------------------------------------
# Simple dataclass-style containers (kept as plain classes for compat)
# ---------------------------------------------------------------------------


class MetadataRaw:
    """Container for freshly extracted raw metadata from video_details.

    Attributes:
        video_id: 11-character YouTube video identifier.
        title: Video title string.
        author: Channel display name.
        channel_id: Channel ID string (starts with ``"UC"``).
        duration: Duration in seconds.
        view_count: Number of views.
        like_count: Number of likes.
        upload_date: Upload date in YYYYMMDD format.
        description: Full video description text.
        thumbnail_urls: List of thumbnail dicts with ``url``, ``width``,
            ``height``, and ``quality`` keys.
        keywords: List of keyword strings.
        categories: List of category strings.
        is_live: ``True`` when the video is a live broadcast.
        is_private: ``True`` when the video is private.
        rating: Average star rating or ``None``.
        short_description: Short description (may differ from ``description``).
        length_seconds: Duration as integer seconds (alias for ``duration``).
        average_rating: Alias for ``rating``.
        like_count_raw: Raw like count from response.
        dislike_count: Dislike count (usually 0 now).
    """

    __slots__ = (
        "video_id",
        "title",
        "author",
        "channel_id",
        "duration",
        "view_count",
        "like_count",
        "upload_date",
        "description",
        "thumbnail_urls",
        "keywords",
        "categories",
        "is_live",
        "is_private",
        "rating",
        "short_description",
        "length_seconds",
        "average_rating",
        "like_count_raw",
        "dislike_count",
    )

    def __init__(self) -> None:
        self.video_id: str | None = None
        self.title: str | None = None
        self.author: str | None = None
        self.channel_id: str | None = None
        self.duration: int | None = None
        self.view_count: int | None = None
        self.like_count: int | None = None
        self.upload_date: str | None = None
        self.description: str | None = None
        self.thumbnail_urls: list[dict[str, Any]] = []
        self.keywords: list[str] = []
        self.categories: list[str] = []
        self.is_live: bool = False
        self.is_private: bool = False
        self.rating: float | None = None
        self.short_description: str | None = None
        self.length_seconds: int | None = None
        self.average_rating: float | None = None
        self.like_count_raw: int | None = None
        self.dislike_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "channel_id": self.channel_id,
            "duration": self.duration,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "upload_date": self.upload_date,
            "description": self.description,
            "thumbnail_urls": self.thumbnail_urls,
            "keywords": self.keywords,
            "categories": self.categories,
            "is_live": self.is_live,
            "is_private": self.is_private,
            "rating": self.rating,
            "short_description": self.short_description,
            "length_seconds": self.length_seconds,
            "average_rating": self.average_rating,
            "like_count_raw": self.like_count_raw,
            "dislike_count": self.dislike_count,
        }

    def __repr__(self) -> str:
        return (
            f"MetadataRaw("
            f"video_id={self.video_id!r}, "
            f"title={self.title!r}, "
            f"author={self.author!r}, "
            f"channel_id={self.channel_id!r}, "
            f"duration={self.duration!r}"
            f")"
        )


class MetadataSummary:
    """Human-readable summary of video metadata with formatted fields.

    Attributes:
        title: Human-readable title.
        author: Channel name.
        duration_str: Human-readable duration ``HH:MM:SS``.
        view_count_str: Formatted view count (e.g. ``"1.2M views"``).
        upload_date_str: Human-readable date (e.g. ``"2026-07-17"``).
        thumbnail_url: Best available thumbnail URL.
        is_live: Whether the video is live.
        is_private: Whether the video is private.
        description_preview: First 200 characters of description.
    """

    __slots__ = (
        "title",
        "author",
        "channel_id",
        "duration_str",
        "view_count_str",
        "upload_date_str",
        "thumbnail_url",
        "is_live",
        "is_private",
        "description_preview",
        "formatted",
        "raw",
    )

    def __init__(self) -> None:
        self.title: str | None = None
        self.author: str | None = None
        self.channel_id: str | None = None
        self.duration_str: str | None = None
        self.view_count_str: str | None = None
        self.upload_date_str: str | None = None
        self.thumbnail_url: str | None = None
        self.is_live: bool = False
        self.is_private: bool = False
        self.description_preview: str | None = None
        self.formatted: dict[str, Any] = {}
        self.raw: dict[str, Any] = {}

    def __repr__(self) -> str:
        return (
            f"MetadataSummary("
            f"title={self.title!r}, "
            f"author={self.author!r}, "
            f"duration_str={self.duration_str!r}, "
            f"view_count_str={self.view_count_str!r}"
            f")"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

#: Default value used when a field cannot be extracted.
_DEFAULT_STR: str | None = None
_DEFAULT_INT: int = 0
_DEFAULT_LIST: list = []
_DEFAULT_BOOL: bool = False

#: Regex pattern matching a YYYYMMDD date string.
_DATE_PATTERN = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

#: Pattern for utility strings like "stream detailed stats unavailable".
_UTILITY_STR_PATTERN = re.compile(
    r"^(stream details|stream stats|details|stats|available).*",
    re.IGNORECASE,
)


def _is_utility_string(value: Any) -> bool:
    """Return ``True`` if *value* looks like a placeholder / utility string.

    YouTube sometimes inserts the string ``"stream detailed stats unavailable"``
    or similar for fields that are not populated; these are not real values.

    Args:
        value: The candidate value to inspect.

    Returns:
        ``True`` if *value* is a string matching a known utility/placeholder
        pattern, ``False`` otherwise.
    """
    if not isinstance(value, str):
        return False
    return bool(_UTILITY_STR_PATTERN.match(value.strip()))


def _safe_get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely retrieve a (possibly nested) value from a dict.

    Walks the key sequence; if any key is missing or the current value is
    not a dict, returns *default* without raising.

    Args:
        d: The dictionary to access.
        *keys: Key path to follow.
        default: Fallback value if any key is missing.

    Returns:
        The value found, or *default*.
    """
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is None and key not in (current or {}):
            return default
    return current


def _parse_int(value: Any, default: int = 0) -> int:
    """Coerce *value* to an integer without raising.

    Handles strings, floats, and ``None``.  Falls back to *default* on
    failure.

    Args:
        value: The value to coerce.
        default: Integer to return on coercion failure.

    Returns:
        An integer representation, or *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return default
        try:
            return int(float(cleaned))
        except (TypeError, ValueError):
            return default
    return default


def _parse_float(value: Any, default: float = 0.0) -> float:
    """Coerce *value* to a float without raising.

    Args:
        value: The value to coerce.
        default: Float to return on coercion failure.

    Returns:
        A float representation, or *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or _is_utility_string(cleaned):
            return default
        try:
            return float(cleaned)
        except (TypeError, ValueError):
            return default
    return default


def _parse_str(value: Any, default: str | None = None) -> str | None:
    """Coerce *value* to ``str`` without raising.

    Args:
        value: The value to coerce.
        default: String to return when *value* is ``None`` or coercing fails.

    Returns:
        A string, or *default*.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return str(value)


def _parse_bool(value: Any, default: bool = False) -> bool:
    """Coerce *value* to ``bool``.

    YouTube uses various representations for truthy values (integers,
    strings, native bools).

    Args:
        value: The value to coerce.
        default: Bool to return when *value* is ``None`` or falsy.

    Returns:
        ``True`` if *value* is a truthy representation, else *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def _get_video_details(data: dict[str, Any]) -> dict[str, Any]:
    """Extract and return the ``videoDetails`` sub-dict from *data*.

    Args:
        data: The raw player response dict.

    Returns:
        The ``videoDetails`` dict, or an empty dict if missing.
    """
    details = _safe_get(data, "videoDetails", default={})
    if not isinstance(details, dict):
        logger.debug("_get_video_details: 'videoDetails' is not a dict, returning empty")
        return {}
    return details


def _get_microformat(data: dict[str, Any]) -> dict[str, Any]:
    """Extract and return the ``playerMicroformatRenderer`` dict.

    Args:
        data: The raw player response dict.

    Returns:
        The microformat dict or an empty dict if absent.
    """
    micro = _safe_get(data, "microformat", default={})
    if not isinstance(micro, dict):
        return {}
    player_mf = micro.get("playerMicroformatRenderer", {})
    if not isinstance(player_mf, dict):
        return {}
    return player_mf


def _validate_video_details(details: dict[str, Any]) -> None:
    """Raise :exc:`~ytdownloader.exceptions.MetadataExtractionError` when *details* is empty.

    Args:
        details: A ``videoDetails`` dict to validate.

    Raises:
        MetadataExtractionError: When *details* is empty (video details absent).
        TypeError: When *details* is not a dict.
    """
    if not isinstance(details, dict):
        raise MetadataExtractionError(
            "'videoDetails' is present but is not a dict in player response."
        )
    if not details:
        raise MetadataExtractionError(
            "'videoDetails' is missing or empty; cannot extract metadata."
        )


# ---------------------------------------------------------------------------
# Individual field extractors
# ---------------------------------------------------------------------------


def extract_video_id(data: dict[str, Any]) -> str | None:
    """Extract the YouTube video identifier.

    The video ID is an 11-character string such as ``"dQw4w9WgXcQ"``.
    It is read first from ``videoDetails.videoId`` and then from the
    canonical URL embedded in the watch page HTML if the former is absent.

    Args:
        data: The raw player response dict.

    Returns:
        A string video ID, or ``None`` if it cannot be found.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(f"extract_video_id expected a dict, got {type(data).__name__}")
    details = _get_video_details(data)
    video_id = _parse_str(_safe_get(details, "videoId", default=None))
    if video_id:
        logger.debug("extract_video_id: found videoId=%s", video_id)
        return video_id

    canonical = get_video_id_from_canonical(data)
    if canonical:
        logger.debug("extract_video_id: derived from canonical URL: %s", canonical)
    return canonical


def extract_title(video_details: dict[str, Any]) -> str | None:
    """Extract the video title from the ``videoDetails`` dict.

    Handles the common case where the ``title`` field is wrapped in a
    ``runs`` list of dicts (e.g. ``[{"text": "Actual Title"}]``) as well
    as the plain-string case.

    Args:
        video_details: The ``videoDetails`` sub-dict from the player response.

    Returns:
        The title string, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
        MetadataExtractionError: If *video_details* is empty and ``strict``
            behaviour is requested (handled by caller).
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_title expected a dict, got {type(video_details).__name__}"
        )

    title = _safe_get(video_details, "title", default=None)
    if title is None:
        logger.debug("extract_title: title key is missing")
        return None

    if isinstance(title, list) and title:
        first = title[0]
        if isinstance(first, dict):
            title = first.get("text")

    return _parse_str(title, default=None)


def extract_author(video_details: dict[str, Any]) -> str | None:
    """Extract the channel display name (author) from ``videoDetails``.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Author/channel name string, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_author expected a dict, got {type(video_details).__name__}"
        )

    author = _safe_get(video_details, "author", default=None)
    return _parse_str(author, default=None)


def extract_channel_id(video_details: dict[str, Any]) -> str | None:
    """Extract the channel ID from ``videoDetails``.

    The channel ID is a string starting with ``"UC"`` followed by 22
    characters.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Channel ID string, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_channel_id expected a dict, got {type(video_details).__name__}"
        )

    channel_id = _safe_get(video_details, "channelId", default=None)
    return _parse_str(channel_id, default=None)


def extract_duration(video_details: dict[str, Any]) -> int | None:
    """Extract video duration in seconds from ``videoDetails``.

    YouTube represents duration as ``lengthSeconds`` (string or int).

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Duration in seconds, or ``None`` if absent / unparseable.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_duration expected a dict, got {type(video_details).__name__}"
        )

    raw = _safe_get(video_details, "lengthSeconds", default=None)
    if _is_utility_string(raw):
        logger.debug("extract_duration: found utility string instead of duration")
        return None

    result = _parse_int(raw, default=_DEFAULT_INT)
    if result <= 0:
        return None
    return result


def extract_view_count(video_details: dict[str, Any]) -> int | None:
    """Extract the total view count from ``videoDetails``.

    YouTube provides this as an integer (sometimes a string representation
    of a large number).

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Total view count as int, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_view_count expected a dict, got {type(video_details).__name__}"
        )

    raw = _safe_get(video_details, "viewCount", default=None)
    if _is_utility_string(raw):
        logger.debug("extract_view_count: found utility string, returning None")
        return None

    result = _parse_int(raw, default=None)
    return result if result is not None and result >= 0 else None


def extract_like_count(video_details: dict[str, Any]) -> int | None:
    """Extract the like count from ``videoDetails``.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Like count as int, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_like_count expected a dict, got {type(video_details).__name__}"
        )

    raw = _safe_get(video_details, "likeCount", default=None)
    if _is_utility_string(raw):
        return None

    result = _parse_int(raw, default=None)
    return result if result is not None and result >= 0 else None


def extract_upload_date(video_details: dict[str, Any]) -> str | None:
    """Extract the upload date as a ``YYYYMMDD`` string.

    Checks multiple locations in priority order:

    1. ``videoDetails.uploadDate``
    2. ``microformat.playerMicroformatRenderer.uploadDate``
    3. Derived from ``videoDetails.publishDate``

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        A string in ``YYYYMMDD`` format, or ``None`` if not found.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_upload_date expected a dict, got {type(video_details).__name__}"
        )

    for key in ("uploadDate", "publicationDate", "publishDate"):
        raw = _safe_get(video_details, key, default=None)
        if raw:
            return _normalize_date(raw)

    return None


def _normalize_date(raw: str) -> str | None:
    """Normalize a raw date value to ``YYYYMMDD`` format.

    Handles ``YYYYMMDD``, ISO-8601 (``YYYY-MM-DDT...``), and common
    variants.

    Args:
        raw: A date string as returned by the YouTube API.

    Returns:
        A ``YYYYMMDD`` string, or ``None`` if the format is unrecognised.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    m = _DATE_PATTERN.match(s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"

    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if iso_match:
        return f"{iso_match.group(1)}{iso_match.group(2)}{iso_match.group(3)}"

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y%m%d")
    except (ValueError, TypeError):
        pass

    return None


def extract_description(video_details: dict[str, Any]) -> str | None:
    """Extract the full video description from ``videoDetails``.

    Handles both plain-string and ``runs`` list representations.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Full description text, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_description expected a dict, got {type(video_details).__name__}"
        )

    for key in ("shortDescription", "description", "longDescription"):
        raw = _safe_get(video_details, key, default=None)
        if raw is None:
            continue
        if isinstance(raw, list) and raw:
            parts: list[str] = []
            for item in raw:
                if isinstance(item, dict):
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts) if parts else None
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped and not _is_utility_string(stripped):
                return stripped
            continue

    return None


def extract_thumbnail_urls(video_details: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all available thumbnail URL variants.

    Iterates over the ``thumbnail.thumbnails`` list inside
    ``videoDetails`` and collects each entry into a typed dict.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        A list of dicts; each has keys ``url``, ``width``, ``height``,
        ``quality``.  The list is empty when no thumbnails are found.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_thumbnail_urls expected a dict, got {type(video_details).__name__}"
        )

    thumbnails: list[dict[str, Any]] = []

    thumbs = _safe_get(video_details, "thumbnail", "thumbnails", default=[])
    if isinstance(thumbs, list):
        for thumb in thumbs:
            if not isinstance(thumb, dict):
                continue
            url = _safe_get(thumb, "url", default=None)
            if not url:
                continue
            quality = _safe_get(thumb, "quality", default=None)
            thumbnails.append(
                {
                    "url": _parse_str(url),
                    "width": _parse_int(_safe_get(thumb, "width", default=None)),
                    "height": _parse_int(_safe_get(thumb, "height", default=None)),
                    "quality": _parse_str(quality),
                }
            )

    logger.debug("extract_thumbnail_urls: found %d thumbnail variants", len(thumbnails))
    return thumbnails


def extract_keywords(data: dict[str, Any]) -> list[str]:
    """Extract video keywords / tags from the player response.

    Searches ``videoDetails.keywords`` first, then falls back to the
    microformat ``keywords`` field.  Converts all non-string entries to
    strings and filters out empty / placeholder values.

    Args:
        data: The raw player response dict (required for fallback paths).

    Returns:
        A list of keyword strings.  The list may be empty.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_keywords expected a dict, got {type(data).__name__}"
        )

    details = _get_video_details(data)
    raw = _safe_get(details, "keywords", default=None)

    if raw is None:
        mf = _get_microformat(data)
        raw = _safe_get(mf, "keywords", default=None)

    if not isinstance(raw, list):
        logger.debug("extract_keywords: keywords field is not a list")
        return []

    keywords: list[str] = []
    for kw in raw:
        s = _parse_str(kw, default=None)
        if s and not _is_utility_string(s):
            keywords.append(s)

    logger.debug("extract_keywords: found %d keywords", len(keywords))
    return keywords


def extract_categories(data: dict[str, Any]) -> list[str]:
    """Extract video category labels from the player response.

    Checks ``videoDetails.category`` first, then tries the microformat
    ``category`` field.  Also maps numeric category codes to human-readable
    names when only a numeric code is available (deprecated YouTube
    feature, but still seen in some responses).

    Args:
        data: The raw player response dict.

    Returns:
        A list of category name strings.  The list may be empty.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_categories expected a dict, got {type(data).__name__}"
        )

    details = _get_video_details(data)
    raw = _safe_get(details, "category", default=None)

    if raw is None:
        mf = _get_microformat(data)
        raw = _safe_get(mf, "category", default=None)

    categories: list[str] = []
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped and not _is_utility_string(stripped):
            categories.append(stripped)
    elif isinstance(raw, list):
        for item in raw:
            s = _parse_str(item, default=None)
            if s and not _is_utility_string(s):
                categories.append(s)

    logger.debug("extract_categories: found %d categories", len(categories))
    return categories


def extract_is_live(video_details: dict[str, Any]) -> bool:
    """Determine whether the video is a live broadcast.

    Reads ``videoDetails.isLive`` which is a boolean that YouTube sets for
    live streams.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        ``True`` if the video is a live stream, ``False`` otherwise.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_is_live expected a dict, got {type(video_details).__name__}"
        )

    raw = _safe_get(video_details, "isLive", default=False)
    return _parse_bool(raw, default=False)


def extract_is_private(video_details: dict[str, Any]) -> bool:
    """Determine whether the video is private.

    Reads ``videoDetails.isPrivate``.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        ``True`` if the video is private, ``False`` otherwise.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_is_private expected a dict, got {type(video_details).__name__}"
        )

    raw = _safe_get(video_details, "isPrivate", default=False)
    return _parse_bool(raw, default=False)


def extract_rating(video_details: dict[str, Any]) -> float | None:
    """Extract the average star rating from ``videoDetails``.

    YouTube stores the rating as ``averageRating`` (float, typically in the
    range 1.0–5.0).

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Average rating as float, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_rating expected a dict, got {type(video_details).__name__}"
        )

    raw = _safe_get(video_details, "averageRating", default=None)
    result = _parse_float(raw, default=None)
    return result if result is not None and result > 0 else None


def extract_channel_name(video_details: dict[str, Any]) -> str | None:
    """Extract the channel display name, mirroring ``extract_author``.

    YouTube populates both ``author`` and ``channel`` keys in
    ``videoDetails``; this function returns whichever is available, with
    ``author`` taking priority.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        Channel name string, or ``None`` if absent.

    Raises:
        TypeError: If *video_details* is not a dict.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_channel_name expected a dict, got {type(video_details).__name__}"
        )

    author = extract_author(video_details)
    if author:
        return author

    channel_name = _safe_get(video_details, "channel", default=None)
    return _parse_str(channel_name, default=None)


# ---------------------------------------------------------------------------
# Higher-level extraction helpers
# ---------------------------------------------------------------------------


def get_duration_ms(data: dict[str, Any]) -> int | None:
    """Get video duration in milliseconds.

    Reads ``videoDetails.lengthSeconds`` from *data* and converts to ms.

    Args:
        data: The raw player response dict.

    Returns:
        Duration in milliseconds, or ``None`` if absent.
    """
    details = _get_video_details(data)
    duration_secs = extract_duration(details)
    if duration_secs is None:
        return None
    return duration_secs * 1000


def get_channel_info(data: dict[str, Any]) -> dict[str, str | None]:
    """Extract channel identity information.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys ``channel_id`` and ``channel_name``.  Either may
        be ``None`` when absent.
    """
    details = _get_video_details(data)
    return {
        "channel_id": extract_channel_id(details),
        "channel_name": extract_channel_name(details),
    }


def get_monetization_info(data: dict[str, Any]) -> dict[str, Any]:
    """Extract monetization-related metadata from the player response.

    Checks the ``microformat.playerMicroformatRenderer`` object for
    monetization flags.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with the following keys (all may be ``None``):

        * ``is_monetized`` (bool | None)
        * ``is_paid`` (bool | None)
        * ``is_family_safe`` (bool | None)
        * ``is_unplugged_corpus`` (bool | None)

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_monetization_info expected a dict, got {type(data).__name__}"
        )
    mf = _get_microformat(data)
    return {
        "is_monetized": _parse_bool(_safe_get(mf, "isMonetized", default=None)),
        "is_paid": _parse_bool(_safe_get(mf, "isPaid", default=None)),
        "is_family_safe": _parse_bool(_safe_get(mf, "isFamilySafe", default=None)),
        "is_unplugged_corpus": _parse_bool(
            _safe_get(mf, "isUnpluggedCorpus", default=None)
        ),
    }


def get_live_broadcast_details(data: dict[str, Any]) -> dict[str, Any]:
    """Extract live broadcast scheduling and viewer count data.

    Reads ``videoDetails.liveBroadcastDetails`` to find start/end times
    and concurrent viewer counts.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys ``is_live``, ``is_live_now``, ``scheduled_start_time``,
        ``scheduled_end_time``, ``concurrent_viewers`` (all ``int``/``str``/``None``).

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_live_broadcast_details expected a dict, got {type(data).__name__}"
        )
    details = _get_video_details(data)
    live_bd = _safe_get(details, "liveBroadcastDetails", default={})
    if not isinstance(live_bd, dict):
        live_bd = {}
    is_live_now_raw = _safe_get(live_bd, "isLiveNow", default=None)
    is_live_now = _parse_bool(is_live_now_raw, default=None) if is_live_now_raw is not None else None
    return {
        "is_live": extract_is_live(details),
        "is_live_now": is_live_now,
        "scheduled_start_time": _parse_int(
            _safe_get(live_bd, "scheduledStartTime", default=None)
        ),
        "scheduled_end_time": _parse_int(
            _safe_get(live_bd, "scheduledEndTime", default=None)
        ),
        "concurrent_viewers": _parse_int(
            _safe_get(live_bd, "concurrentViewers", default=None)
        ),
    }


def get_availability(data: dict[str, Any]) -> dict[str, Any]:
    """Extract availability flags for the video.

    Reads ``videoDetails`` fields related to availability and accessibility.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:
        ``is_private``, ``is_live``, ``is_crawlable``, ``allow_ratings``,
        ``is_family_safe``.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_availability expected a dict, got {type(data).__name__}"
        )
    details = _get_video_details(data)
    mf = _get_microformat(data)
    return {
        "is_private": extract_is_private(details),
        "is_live": extract_is_live(details),
        "is_crawlable": _parse_bool(_safe_get(details, "isCrawlable", default=True)),
        "allow_ratings": _parse_bool(_safe_get(details, "allowRatings", default=True)),
        "is_family_safe": _parse_bool(_safe_get(mf, "isFamilySafe", default=None)),
    }


def get_video_id_from_canonical(data: dict[str, Any]) -> str | None:
    """Derive the video ID from a canonical YouTube watch URL in the page.

    Some player responses omit ``videoDetails.videoId`` but include the
    canonical URL as ``canonicalUrl`` in ``microformat`` or somewhere
    else.

    Args:
        data: The raw player response dict.

    Returns:
        An 11-character video ID, or ``None`` if not found.
    """
    if not isinstance(data, dict):
        return None
    mf = _get_microformat(data)
    canonical = _safe_get(mf, "urlCanonical", default=None)
    if not canonical:
        canonical = _safe_get(data, "canonicalUrl", default=None)
    if not canonical:
        return None
    m = re.search(r"v=([a-zA-Z0-9_-]{11})", str(canonical))
    if m:
        return m.group(1)
    return None


def get_format_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Build a compact summary of available stream formats.

    Reads ``streamingData`` and counts the available formats as well as
    identifying the best quality.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys ``format_count``, ``adaptive_count``,
        ``best_quality``, ``best_itag``.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_format_summary expected a dict, got {type(data).__name__}"
        )
    streaming = _safe_get(data, "streamingData", default={})
    if not isinstance(streaming, dict):
        return {
            "format_count": 0,
            "adaptive_count": 0,
            "best_quality": None,
            "best_itag": None,
        }

    formats = streaming.get("formats", []) or []
    adaptive = streaming.get("adaptiveFormats", []) or []

    best_height = 0
    best_itag = None
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        h = _parse_int(fmt.get("height"), default=0)
        if h > best_height:
            best_height = h
            best_itag = _parse_int(fmt.get("itag"), default=None)

    for fmt in adaptive:
        if not isinstance(fmt, dict):
            continue
        h = _parse_int(fmt.get("height"), default=0)
        if isinstance(fmt, dict) and h > best_height and not _safe_get(fmt, "videoCodec", default="none") not in (None, "none"):
            best_height = h
            best_itag = _parse_int(fmt.get("itag"), default=None)

    return {
        "format_count": len(formats) if isinstance(formats, list) else 0,
        "adaptive_count": len(adaptive) if isinstance(adaptive, list) else 0,
        "best_quality": best_height if best_height > 0 else None,
        "best_itag": best_itag,
    }


# ---------------------------------------------------------------------------
# Master metadata builder
# ---------------------------------------------------------------------------


def build_metadata_dict(video_details: dict[str, Any]) -> dict[str, Any]:
    """Build a comprehensive metadata dict from a ``videoDetails`` object.

    Calls every individual extractor and collects results into a single
    flat dict.  Extracts that depend on the full player response (e.g.
    keywords requiring microformat fallback) are filled from the
    ``videoDetails`` alone.

    The returned dict contains all fields defined in
    :class:`MetadataRaw` plus the following additional keys:

    * ``duration_ms`` (int): Duration in milliseconds.
    * ``availability`` (dict): Availability flags.
    * ``live_broadcast_details`` (dict): Live streaming details.
    * ``monetization_info`` (dict): Monetization flags.
    * ``format_summary`` (dict): Stream format summary.
    * ``extraction_success`` (bool): ``True`` when at least title and
      video_id were found.

    Args:
        video_details: The ``videoDetails`` sub-dict from the player
            response.  When this dict is empty or None, a
            :exc:`~ytdownloader.exceptions.MetadataExtractionError` is
            raised.

    Returns:
        A flat dict with every available metadata field.

    Raises:
        TypeError: If *video_details* is not a dict.
        MetadataExtractionError: If *video_details* is empty.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"build_metadata_dict expected a dict, got {type(video_details).__name__}"
        )
    _validate_video_details(video_details)

    thumbnails = extract_thumbnail_urls(video_details)
    thumbnail_url = thumbnails[0]["url"] if thumbnails else None

    return {
        "video_id": extract_video_id({"videoDetails": video_details}),
        "title": extract_title(video_details),
        "author": extract_author(video_details),
        "channel_id": extract_channel_id(video_details),
        "channel_name": extract_channel_name(video_details),
        "duration": extract_duration(video_details),
        "duration_ms": extract_duration(video_details) * 1000 if extract_duration(video_details) else None,
        "view_count": extract_view_count(video_details),
        "like_count": extract_like_count(video_details),
        "upload_date": extract_upload_date(video_details),
        "description": extract_description(video_details),
        "short_description": _parse_str(_safe_get(video_details, "shortDescription", default=None)),
        "thumbnail_urls": thumbnails,
        "thumbnail_url": thumbnail_url,
        "keywords": extract_keywords({"videoDetails": video_details}),
        "categories": extract_categories({"videoDetails": video_details}),
        "is_live": extract_is_live(video_details),
        "is_private": extract_is_private(video_details),
        "rating": extract_rating(video_details),
        "average_rating": extract_rating(video_details),
        "dislike_count": _parse_int(
            _safe_get(video_details, "dislikeCount", default=None)
        ),
        "like_count_raw": _parse_int(_safe_get(video_details, "likeCount", default=None)),
        "length_seconds": extract_duration(video_details),
        "availability": get_availability({"videoDetails": video_details}),
        "live_broadcast_details": get_live_broadcast_details({"videoDetails": video_details}),
        "monetization_info": get_monetization_info({"videoDetails": video_details}),
        "format_summary": get_format_summary({"videoDetails": video_details}),
        "extraction_success": bool(
            _safe_get(video_details, "title") or _safe_get(video_details, "videoId")
        ),
    }


def extract_metadata(
    video_details: dict[str, Any],
    streaming_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract all video metadata from ``video_details`` and ``streaming_data``.

    This is the primary top-level entry point for metadata extraction.  It
    calls :func:`build_metadata_dict` to get all textual / scalar fields,
    then enriches the result with streaming-format counts from
    ``streaming_data`` (when supplied).

    Args:
        video_details: The ``videoDetails`` sub-dict from the player
            response.
        streaming_data: The ``streamingData`` sub-dict from the player
            response.  Optional; when provided, format counts are added
            to the result.

    Returns:
        A flat dict with every available metadata field.  Missing fields
        are set to ``None`` (scalar), ``0`` (numeric), or ``[]`` (list).

    Raises:
        TypeError: If *video_details* is not a dict.
        MetadataExtractionError: If *video_details* is empty or required
            keys are missing in strict mode.
    """
    if not isinstance(video_details, dict):
        raise TypeError(
            f"extract_metadata expected video_details to be a dict, "
            f"got {type(video_details).__name__}"
        )

    logger.debug("extract_metadata: starting extraction")

    try:
        result = build_metadata_dict(video_details)
    except MetadataExtractionError:
        raise

    if streaming_data is not None:
        if not isinstance(streaming_data, dict):
            logger.warning(
                "extract_metadata: streaming_data was not a dict; skipping format enrichment"
            )
        else:
            formats = streaming_data.get("formats", []) or []
            adaptive = streaming_data.get("adaptiveFormats", []) or []
            result["streaming_format_count"] = len(formats)
            result["adaptive_format_count"] = len(adaptive)
            result["total_format_count"] = len(formats) + len(adaptive)

            audio_only = sum(
                1
                for f in formats + adaptive
                if isinstance(f, dict)
                and f.get("audioCodec") not in (None, "none")
                and f.get("videoCodec") in (None, "none")
            )
            video_only = sum(
                1
                for f in formats + adaptive
                if isinstance(f, dict)
                and f.get("videoCodec") not in (None, "none")
                and f.get("audioCodec") in (None, "none")
            )
            combined = sum(
                1
                for f in formats + adaptive
                if isinstance(f, dict)
                and f.get("videoCodec") not in (None, "none")
                and f.get("audioCodec") not in (None, "none")
            )
            result["audio_only_count"] = audio_only
            result["video_only_count"] = video_only
            result["combined_count"] = combined

            best_height = 0
            best_itag = None
            for fmt in formats + adaptive:
                if not isinstance(fmt, dict):
                    continue
                h = _parse_int(fmt.get("height"), default=0)
                if h > best_height:
                    best_height = h
                    best_itag = _parse_int(fmt.get("itag"), default=None)
            result["best_available_height"] = best_height if best_height > 0 else None
            result["best_available_itag"] = best_itag

    result["extraction_success"] = bool(
        result.get("video_id") or result.get("title")
    )
    result["extraction_warnings"] = _collect_warnings(video_details)

    logger.debug("extract_metadata: extraction complete")
    return result


def _collect_warnings(video_details: dict[str, Any]) -> list[str]:
    """Inspect *video_details* and return a list of non-fatal warnings.

    Checks for known placeholder / utility strings that YouTube inserts
    when certain fields are unavailable.

    Args:
        video_details: The ``videoDetails`` sub-dict.

    Returns:
        A list of warning strings.  The list is empty when no issues are
        detected.
    """
    warnings: list[str] = []
    if not isinstance(video_details, dict):
        return warnings
    for field in ("viewCount", "likeCount", "description"):
        raw = _safe_get(video_details, field, default=None)
        if _is_utility_string(raw):
            warnings.append(
                f"videoDetails.{field} contains a utility/placeholder value"
            )
    return warnings


# ---------------------------------------------------------------------------
# Formatting utilities
# ---------------------------------------------------------------------------


def format_duration(seconds: int | None) -> str | None:
    """Format a duration in seconds as a ``HH:MM:SS`` string.

    The format is human-readable and zero-padded.  Durations shorter
    than one minute drop the hour component.

    Args:
        seconds: Duration in seconds.  A value of ``None`` or ``< 0``
            returns ``None``.

    Returns:
        A string such as ``"01:23:45"`` or ``"03:21"``, or ``None``.

    Examples:
        >>> format_duration(8465)
        '02:21:05'
        >>> format_duration(61)
        '01:01'
    """
    if seconds is None or seconds < 0:
        return None
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_view_count(count: int | None) -> str | None:
    """Format a raw view count as a short human-readable string.

    Converts the raw integer into a compact representation using
    ``K`` (thousands), ``M`` (millions), or ``B`` (billions) suffixes
    with one decimal place.

    Args:
        count: Raw integer view count, or ``None``.

    Returns:
        A string such as ``"1.2M views"``, ``"845K views"``,
        ``"2.3B views"``, or ``"0 views"``, or ``None`` when *count* is
        ``None``.

    Examples:
        >>> format_view_count(1250000)
        '1.2M views'
        >>> format_view_count(500)
        '500 views'
    """
    if count is None:
        return None
    abs_count = abs(count)
    if abs_count >= 1_000_000_000:
        val = abs_count / 1_000_000_000
        label = "B"
    elif abs_count >= 1_000_000:
        val = abs_count / 1_000_000
        label = "M"
    elif abs_count >= 1_000:
        val = abs_count / 1_000
        label = "K"
    else:
        return f"{abs_count} views"
    rounded = round(val, 1)
    if rounded == int(rounded):
        formatted = f"{int(rounded)}{label}"
    else:
        formatted = f"{val:.1f}{label}"
    sign = "-" if count < 0 else ""
    return f"{sign}{formatted} views"


def format_upload_date(date_str: str | None) -> str | None:
    """Format a ``YYYYMMDD`` date string in a human-readable ``MM/DD/YYYY``
    form.

    Accepts both the raw ``YYYYMMDD`` format produced by
    :func:`extract_upload_date` and the ISO-8601 variants.  Unparseable
    inputs are returned unchanged.

    Args:
        date_str: A date string such as ``"20260717"``.

    Returns:
        A formatted string such as ``"07/17/2026"``, or ``None`` when
        *date_str* is ``None`` or unparseable.

    Examples:
        >>> format_upload_date("20260717")
        '07/17/2026'
        >>> format_upload_date("2026-07-17T00:00:00Z")
        Traceback (most remove...):
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    if not s or _is_utility_string(s):
        return None
    m = _DATE_PATTERN.match(s)
    if m:
        return f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if iso_match:
        return f"{iso_match.group(2)}/{iso_match.group(3)}/{iso_match.group(1)}"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%m/%d/%Y")
    except (ValueError, TypeError):
        return s


def format_file_size(num_bytes: int | None) -> str | None:
    """Format a byte count as a compact human-readable string.

    Args:
        num_bytes: Raw byte count, or ``None``.

    Returns:
        A string such as ``"1.5 MB"`` or ``"320 B"``, or ``None`` when
        *num_bytes* is ``None``.

    Examples:
        >>> format_file_size(1536000)
        '1.46 MB'
    """
    if num_bytes is None:
        return None
    abs_bytes = abs(num_bytes)
    if abs_bytes >= 1 << 30:
        val = num_bytes / (1 << 30)
        return f"{val:.2f} GB"
    if abs_bytes >= 1 << 20:
        val = num_bytes / (1 << 20)
        return f"{val:.2f} MB"
    if abs_bytes >= 1 << 10:
        val = num_bytes / (1 << 10)
        return f"{val:.2f} KB"
    return f"{num_bytes} B"


def format_description_preview(
    description: str | None, max_length: int = 200
) -> str | None:
    """Return a short preview of the video description.

    Truncates the description to *max_length* characters and appends
    ``"..."`` if truncated.

    Args:
        description: Full video description text.
        max_length: Maximum length of the returned preview.  Defaults
            to ``200``.

    Returns:
        A truncated description string, or ``None`` when *description*
        is ``None`` or empty.
    """
    if not description:
        return None
    description = description.strip()
    if not description:
        return None
    if len(description) <= max_length:
        return description
    return description[:max_length].rstrip() + "..."


def build_summary(
    metadata: dict[str, Any],
) -> MetadataSummary:
    """Build a :class:`MetadataSummary` from an extracted :class:`MetadataRaw` dict.

    Convenience wrapper that applies all :func:`format_*` helpers to
    produce a display-ready object.

    Args:
        metadata: A dict produced by :func:`extract_metadata`.

    Returns:
        A fully populated :class:`MetadataSummary` instance.
    """
    summary = MetadataSummary()
    summary.title = metadata.get("title")
    summary.author = metadata.get("author")
    summary.channel_id = metadata.get("channel_id")
    summary.duration_str = format_duration(metadata.get("duration"))
    summary.view_count_str = format_view_count(metadata.get("view_count"))
    summary.upload_date_str = format_upload_date(metadata.get("upload_date"))
    thumbs = metadata.get("thumbnail_urls", [])
    summary.thumbnail_url = thumbs[0]["url"] if thumbs else metadata.get("thumbnail_url")
    summary.is_live = bool(metadata.get("is_live"))
    summary.is_private = bool(metadata.get("is_private"))
    summary.description_preview = format_description_preview(metadata.get("description"))
    summary.formatted = {
        "duration_str": summary.duration_str,
        "view_count_str": summary.view_count_str,
        "upload_date_str": summary.upload_date_str,
        "description_preview": summary.description_preview,
    }
    summary.raw = metadata
    return summary
