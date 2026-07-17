"""
Comprehensive parser for the ytInitialPlayerResponse object structure.

This module provides functions to parse, validate, and extract all relevant
data from the YouTube initial player response JSON object that is embedded
in watch page HTML. All extraction functions operate on the raw dict
produced by JSON-decoding ytInitialPlayerResponse, or on the result of
parse_player_response().
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from ytdownloader import constants
from ytdownloader.exceptions import (
    MetadataExtractionError,
    StreamResolutionError,
    VideoUnavailableError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Required top-level keys that must be present in a valid player response.
_REQUIRED_KEYS: List[str] = [
    "videoDetails",
    "streamingData",
]

#: Keys expected inside the videoDetails object.
_VIDEO_DETAILS_KEYS: List[str] = [
    "video_id",
    "title",
    "author",
    "channel_id",
    "lengthSeconds",
    "viewCount",
    "shortDescription",
    "isLive",
    "isPrivate",
    "isCrawlable",
    "allowRatings",
    "averageRating",
    "likeCount",
    "dislikeCount",
]

#: Keys expected inside the streamingData object.
_STREAMING_DATA_KEYS: List[str] = [
    "formats",
    "adaptiveFormats",
]

#: Keys expected inside microformat data.
_MICROFORMAT_KEYS: List[str] = [
    "playerMicroformatRenderer",
]

#: Keys expected inside the playabilityStatus object.
_PLAYABILITY_KEYS: List[str] = [
    "status",
    "reason",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely retrieve a nested value from a dict using a chain of keys.

    Args:
        d: The dictionary to traverse.
        *keys: A sequence of keys to walk through.  Each key is looked up
            in the current sub-dict; if any lookup fails the *default* is
            returned.
        default: The value to return when any key in the chain is missing
            or the current value is not a dict.  Defaults to ``None``.

    Returns:
        The value found at the end of the key chain, or *default*.

    Raises:
        TypeError: If *d* is not a dict or any intermediate value is not a
            dict (and the key chain has not yet been exhausted).  This
            should never happen for well-formed YouTube responses, but is
            included for defensive programming.
    """
    if not isinstance(d, dict):
        raise TypeError(
            f"_safe_get expected a dict as the first argument, got {type(d).__name__}"
        )
    current: Any = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is default and key != list(current.keys())[0] if isinstance(current, dict) else False:
            return default
        if current is None and key in d if isinstance(d, dict) else False:
            return default
        if current is None:
            return default
    return current


def _parse_int_safe(value: Any, default: int = 0) -> int:
    """Safely convert a value to an integer.

    Handles strings, floats, and ``None``.  Non-numeric strings and other
    non-coercible types silently fall back to *default*.

    Args:
        value: The value to convert.
        default: The integer returned when conversion is not possible.
            Defaults to ``0``.

    Returns:
        An integer representation of *value*, or *default*.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_player_response(data: Dict[str, Any]) -> bool:
    """Check that *data* contains all required ytInitialPlayerResponse keys.

    A valid player response must be a dict that contains at least the
    top-level keys ``"videoDetails"`` and ``"streamingData"``.

    Args:
        data: The parsed JSON object from ytInitialPlayerResponse.

    Returns:
        ``True`` if all required keys are present, ``False`` otherwise.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"validate_player_response expected a dict, got {type(data).__name__}"
        )
    for key in _REQUIRED_KEYS:
        if key not in data:
            logger.warning(
                "validate_player_response: missing required key '%s' in player response",
                key,
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_player_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse and validate the raw ytInitialPlayerResponse dict.

    This is the main entry point for player response processing.  It
    validates the structure of *data*, then returns it unchanged (the
    individual extraction functions operate directly on the raw dict).

    Args:
        data: The raw dict decoded from the ``ytInitialPlayerResponse``
            JavaScript object embedded in a YouTube watch page.

    Returns:
        The same *data* dict if validation passes.

    Raises:
        VideoUnavailableError: If the response indicates the video is not
            playable (age-restricted, private, geo-restricted, etc.).
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"parse_player_response expected a dict, got {type(data).__name__}"
        )

    if not validate_player_response(data):
        raise VideoUnavailableError(
            "Player response is missing required keys; video may be unavailable."
        )

    playability = extract_playability_status(data)
    status = playability.get("status", "OK")
    if status not in ("OK", "LIVE_STREAM_OFFLINE_WITH_CONTENT"):
        reason = playability.get("reason", "Unknown reason")
        logger.warning(
            "parse_player_response: video is not playable (status=%s, reason=%s)",
            status,
            reason,
        )
        raise VideoUnavailableError(
            f"Video is not playable: {reason} (status: {status})"
        )

    logger.debug("parse_player_response: validation passed")
    return data


# ---------------------------------------------------------------------------
# Video details extraction
# ---------------------------------------------------------------------------


def extract_video_details(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract core video details from the player response.

    Pulls the ``videoDetails`` sub-object and flattens the fields into a
    plain dict with consistent types.

    Args:
        data: The raw player response dict (or the output of
            :func:`parse_player_response`).

    Returns:
        A dict with the following keys (values may be ``None`` or
        defaults when not present in the source):

        * ``video_id`` (str)
        * ``title`` (str)
        * ``author`` (str)
        * ``channel_id`` (str)
        * ``length_seconds`` (int)
        * ``view_count`` (int)
        * ``short_description`` (str)
        * ``is_live`` (bool)
        * ``is_private`` (bool)
        * ``is_crawlable`` (bool)
        * ``allow_ratings`` (bool)
        * ``average_rating`` (float)
        * ``like_count`` (int)
        * ``dislike_count`` (int)

    Raises:
        MetadataExtractionError: If the ``videoDetails`` key is missing
            from *data*.
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_video_details expected a dict, got {type(data).__name__}"
        )

    video_details = _safe_get(data, "videoDetails", default={})
    if not isinstance(video_details, dict):
        raise MetadataExtractionError(
            "'videoDetails' key is present but not a dict in player response."
        )

    return {
        "video_id": _safe_get(video_details, "videoId", default=None),
        "title": _safe_get(video_details, "title", default=None),
        "author": _safe_get(video_details, "author", default=None),
        "channel_id": _safe_get(video_details, "channelId", default=None),
        "length_seconds": _parse_int_safe(
            _safe_get(video_details, "lengthSeconds", default=None)
        ),
        "view_count": _parse_int_safe(
            _safe_get(video_details, "viewCount", default=None)
        ),
        "short_description": _safe_get(video_details, "shortDescription", default=None),
        "is_live": bool(_safe_get(video_details, "isLive", default=False)),
        "is_private": bool(_safe_get(video_details, "isPrivate", default=False)),
        "is_crawlable": bool(_safe_get(video_details, "isCrawlable", default=True)),
        "allow_ratings": bool(_safe_get(video_details, "allowRatings", default=True)),
        "average_rating": _safe_get(video_details, "averageRating", default=None),
        "like_count": _parse_int_safe(
            _safe_get(video_details, "likeCount", default=None)
        ),
        "dislike_count": _parse_int_safe(
            _safe_get(video_details, "dislikeCount", default=None)
        ),
    }


# ---------------------------------------------------------------------------
# Streaming data extraction
# ---------------------------------------------------------------------------


def extract_streaming_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the streamingData object from the player response.

    Returns the raw ``streamingData`` sub-dict, which contains the
    ``formats`` and ``adaptiveFormats`` lists.  Callers should pass each
    entry through :func:`~ytdownloader.streaming_data.parse_streaming_data`
    for further processing.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``formats`` (list[dict]): Combined audio+video (progressive) formats.
        * ``adaptiveFormats`` (list[dict]): Separate audio and video (DASH)
          formats.

        The lists may be empty if no formats of that type are available.

    Raises:
        StreamResolutionError: If the ``streamingData`` key is missing or
            is not a dict.
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_streaming_data expected a dict, got {type(data).__name__}"
        )

    streaming_data = _safe_get(data, "streamingData", default=None)
    if streaming_data is None:
        raise StreamResolutionError(
            "'streamingData' key is missing from player response."
        )
    if not isinstance(streaming_data, dict):
        raise StreamResolutionError(
            f"'streamingData' is present but is a {type(streaming_data).__name__}, "
            "expected a dict."
        )

    return {
        "formats": list(streaming_data.get("formats", []) or []),
        "adaptiveFormats": list(streaming_data.get("adaptiveFormats", []) or []),
    }


# ---------------------------------------------------------------------------
# Microformat extraction
# ---------------------------------------------------------------------------


def extract_microformat(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract microformat player information from the player response.

    The microformat data provides additional metadata used by search
    indexing and social sharing.  The returned dict contains whatever
    fields are present in the ``playerMicroformatRenderer`` sub-object.

    Args:
        data: The raw player response dict.

    Returns:
        A dict representing the ``playerMicroformatRenderer`` object,
        or an empty dict if the key is not present or is not a dict.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_microformat expected a dict, got {type(data).__name__}"
        )

    microformat = _safe_get(data, "microformat", default={})
    if not isinstance(microformat, dict):
        return {}

    player_microformat = microformat.get("playerMicroformatRenderer", {})
    if not isinstance(player_microformat, dict):
        return {}

    return dict(player_microformat)


# ---------------------------------------------------------------------------
# Playability status extraction
# ---------------------------------------------------------------------------


def extract_playability_status(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the playability status from the player response.

    The playability status tells whether the video can be played and, if
    not, provides a human-readable reason.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``status`` (str): One of the status codes from
          :data:`~ytdownloader.constants.PLAYABILITY_STATUSES`.
        * ``reason`` (str): Human-readable explanation, or ``None``.
        * ``errorScreen`` (dict | None): Additional error details when
          present.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_playability_status expected a dict, got {type(data).__name__}"
        )

    playability = _safe_get(data, "playabilityStatus", default={})
    if not isinstance(playability, dict):
        return {"status": "ERROR", "reason": "playabilityStatus is not a dict", "errorScreen": None}

    status = _safe_get(playability, "status", default="ERROR")
    reason = _safe_get(playability, "reason", default=None)
    error_screen = _safe_get(playability, "errorScreen", default=None)

    return {
        "status": status,
        "reason": reason,
        "errorScreen": error_screen,
    }


# ---------------------------------------------------------------------------
# Captions extraction
# ---------------------------------------------------------------------------


def extract_captions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract caption/subtitle tracks from the player response.

    YouTube provides caption tracks inside the
    ``captions.playerCaptionsTracklistRenderer.captionTracks`` path.
    This function extracts each track as a plain dict.

    Args:
        data: The raw player response dict.

    Returns:
        A list of dicts, one per caption track.  Each dict has keys:

        * ``url`` (str): URL of the caption XML/JSON file.
        * ``lang_code`` (str): BCP-47 language code (e.g. ``"en"``).
        * ``lang`` (str): Human-readable language name.
        * ``is_auto`` (bool): ``True`` if auto-generated.
        * ``is_translated`` (bool): ``True`` if this is a translation.
        * ``kind`` (str | None): Caption kind (``"asr"`` for auto, or ``None``).

        The list is empty when no captions are available.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_captions expected a dict, got {type(data).__name__}"
        )

    captions_node = _safe_get(
        data, "captions", "playerCaptionsTracklistRenderer", "captionTracks", default=[]
    )

    if not isinstance(captions_node, list):
        logger.debug("extract_captions: captionTracks is not a list, returning empty")
        return []

    tracks: List[Dict[str, Any]] = []
    for track in captions_node:
        if not isinstance(track, dict):
            continue
        url = _safe_get(track, "baseUrl", default=None)
        if url is None:
            url = _safe_get(track, "url", default=None)
        lang_code = _safe_get(track, "languageCode", default=None)
        lang = _safe_get(track, "name", "simpleText", default=None)
        is_auto = bool(_safe_get(track, "kind", default="") == "asr")
        is_translated = bool(_safe_get(track, "is_translatable", default=False))
        kind = _safe_get(track, "kind", default=None)

        if url is not None:
            tracks.append(
                {
                    "url": url,
                    "lang_code": lang_code,
                    "lang": lang,
                    "is_auto": is_auto,
                    "is_translated": is_translated,
                    "kind": kind,
                }
            )

    logger.debug("extract_captions: found %d caption tracks", len(tracks))
    return tracks


# ---------------------------------------------------------------------------
# Audio tracks extraction
# ---------------------------------------------------------------------------


def extract_audio_tracks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract audio-only formats from the player response.

    Filters :func:`extract_streaming_data` results to formats that have
    an audio codec but no video stream.  The returned dicts contain a
    subset of the raw format fields relevant to audio-only downloads.

    Args:
        data: The raw player response dict.

    Returns:
        A list of dicts, one per audio-only format.  Each dict has keys:

        * ``itag`` (int): YouTube itag number.
        * ``mime_type`` (str | None): Full MIME type string.
        * ``bitrate`` (int | None): Average bitrate in kbps.
        * ``audio_quality`` (str | None): Quality label if provided.
        * ``audio_sample_rate`` (int | None): Sample rate in Hz.
        * ``audio_channels`` (str | None): Channel count description.
        * ``url`` (str | None): Direct stream URL (may be missing if
          signatureCipher is required).
        * ``signature_cipher`` (str | None): Raw ``signatureCipher`` value.
        * ``content_length`` (int | None): Expected file size in bytes.
        * ``approx_duration_ms`` (int | None): Duration in milliseconds.
        * ``container`` (str | None): Container format derived from MIME.
        * ``acodec`` (str | None): Audio codec derived from MIME.

        The list is empty when no audio-only formats are found.

    Raises:
        TypeError: If *data* is not a dict.
        StreamResolutionError: If ``streamingData`` is missing.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_audio_tracks expected a dict, got {type(data).__name__}"
        )

    streaming = extract_streaming_data(data)
    all_formats: List[Dict[str, Any]] = (
        streaming.get("formats", []) + streaming.get("adaptiveFormats", [])
    )

    audio_tracks: List[Dict[str, Any]] = []
    for fmt in all_formats:
        if not isinstance(fmt, dict):
            continue
        mime_type = _safe_get(fmt, "mimeType", default="")
        acodec = _safe_get(fmt, "audioCodec", default=None)
        vcodec = _safe_get(fmt, "videoCodec", default=None)

        has_audio = acodec is not None and acodec != "none"
        has_video = vcodec is not None and vcodec != "none"

        if has_audio and not has_video:
            container, parsed_acodec, _ = _parse_mime_type(mime_type)
            audio_tracks.append(
                {
                    "itag": _parse_int_safe(_safe_get(fmt, "itag", default=None)),
                    "mime_type": mime_type,
                    "bitrate": _parse_int_safe(_safe_get(fmt, "averageBitrate", default=None)),
                    "audio_quality": _safe_get(fmt, "audioQuality", default=None),
                    "audio_sample_rate": _parse_int_safe(
                        _safe_get(fmt, "audioSampleRate", default=None)
                    ),
                    "audio_channels": _safe_get(fmt, "audioChannels", default=None),
                    "url": _safe_get(fmt, "url", default=None),
                    "signature_cipher": _safe_get(fmt, "signatureCipher", default=None),
                    "content_length": _parse_int_safe(
                        _safe_get(fmt, "contentLength", default=None)
                    ),
                    "approx_duration_ms": _parse_int_safe(
                        _safe_get(fmt, "approxDurationMs", default=None)
                    ),
                    "container": container,
                    "acodec": parsed_acodec,
                }
            )

    logger.debug("extract_audio_tracks: found %d audio-only formats", len(audio_tracks))
    return audio_tracks


# ---------------------------------------------------------------------------
# Thumbnail URLs extraction
# ---------------------------------------------------------------------------


def extract_thumbnail_urls(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract all available thumbnail URLs for the video.

    YouTube provides thumbnails at several resolutions.  This function
    extracts both the standard thumbnail URLs from ``videoDetails`` and
    any additional thumbnail variants found in ``storyboards``.

    Args:
        data: The raw player response dict.

    Returns:
        A list of dicts.  Each dict has keys:

        * ``url`` (str): The thumbnail URL.
        * ``width`` (int | None): Thumbnail width in pixels.
        * ``height`` (int | None): Thumbnail height in pixels.
        * ``quality`` (str | None): Quality label such as ``"default"``,
          ``"medium"``, ``"high"``, ``"standard"``, ``"maxres"``.

        When the source does not provide width/height, those fields are
        ``None``.  The list is empty when no thumbnails are found.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_thumbnail_urls expected a dict, got {type(data).__name__}"
        )

    thumbnails: List[Dict[str, Any]] = []

    video_details = _safe_get(data, "videoDetails", default={})
    if isinstance(video_details, dict):
        thumbs = _safe_get(video_details, "thumbnail", "thumbnails", default=[])
        if isinstance(thumbs, list):
            for thumb in thumbs:
                if not isinstance(thumb, dict):
                    continue
                url = _safe_get(thumb, "url", default=None)
                if url is None:
                    continue
                thumbnails.append(
                    {
                        "url": url,
                        "width": _parse_int_safe(_safe_get(thumb, "width", default=None)),
                        "height": _parse_int_safe(_safe_get(thumb, "height", default=None)),
                        "quality": _safe_get(thumb, "quality", default=None),
                    }
                )

    microformat = _safe_get(data, "microformat", "playerMicroformatRenderer", default={})
    if isinstance(microformat, dict):
        mf_thumbs = _safe_get(microformat, "thumbnail", "thumbnails", default=[])
        if isinstance(mf_thumbs, list):
            for thumb in mf_thumbs:
                if not isinstance(thumb, dict):
                    continue
                url = _safe_get(thumb, "url", default=None)
                if url is None:
                    continue
                thumbnails.append(
                    {
                        "url": url,
                        "width": _parse_int_safe(_safe_get(thumb, "width", default=None)),
                        "height": _parse_int_safe(_safe_get(thumb, "height", default=None)),
                        "quality": _safe_get(thumb, "quality", default=None),
                    }
                )

    unique_urls = set()
    deduped: List[Dict[str, Any]] = []
    for thumb in thumbnails:
        url = thumb.get("url")
        if url and url not in unique_urls:
            unique_urls.add(url)
            deduped.append(thumb)

    logger.debug("extract_thumbnail_urls: found %d unique thumbnails", len(deduped))
    return deduped


# ---------------------------------------------------------------------------
# Engagement panels extraction
# ---------------------------------------------------------------------------


def extract_engagement_panels(data: Dict[str, Any]) -> List[Any]:
    """Extract engagement panel data from the player response.

    Engagement panels appear in the YouTube UI below the video player
    (e.g. likes/dislikes, comments summary, related videos carousel).

    Args:
        data: The raw player response dict.

    Returns:
        A list of engagement panel dicts as found in the raw response.
        The list is empty when no engagement panels are present.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_engagement_panels expected a dict, got {type(data).__name__}"
        )

    panels = _safe_get(data, "engagementPanels", default=[])
    if not isinstance(panels, list):
        logger.debug("extract_engagement_panels: engagementPanels is not a list")
        return []

    result: List[Any] = []
    for panel in panels:
        if isinstance(panel, dict):
            result.append(panel)
        else:
            result.append(panel)

    logger.debug("extract_engagement_panels: found %d panels", len(result))
    return result


# ---------------------------------------------------------------------------
# Live stream detection
# ---------------------------------------------------------------------------


def is_live_stream(data: Dict[str, Any]) -> bool:
    """Determine whether the video is a live stream.

    Checks both the ``videoDetails.isLive`` field and the
    ``playabilityStatus.status`` field for ``"LIVE_STREAM_OFFLINE"`` or
    ``"LIVE_STREAM_OFFLINE_WITH_CONTENT"`` values.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if the video is a live broadcast, ``False`` otherwise.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"is_live_stream expected a dict, got {type(data).__name__}"
        )

    video_details = _safe_get(data, "videoDetails", default={})
    if isinstance(video_details, dict) and bool(video_details.get("isLive", False)):
        return True

    playability = extract_playability_status(data)
    live_statuses = {
        "LIVE_STREAM_OFFLINE",
        "LIVE_STREAM_OFFLINE_WITH_CONTENT",
    }
    if playability.get("status") in live_statuses:
        return True

    return False


# ---------------------------------------------------------------------------
# Age restriction detection
# ---------------------------------------------------------------------------


def is_age_restricted(data: Dict[str, Any]) -> bool:
    """Determine whether the video is age-restricted.

    Checks both the ``playabilityStatus.status`` field and the
    ``videoDetails.isPrivate`` flag as a secondary indicator.  An age-
    restricted video will have a playability status of
    ``"AGE_CHECK_REQUIRED"``, ``"AGE_VERIFICATION_REQUIRED"``, or
    ``"AGE_GATE"``.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if the video requires age verification, ``False``
        otherwise.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"is_age_restricted expected a dict, got {type(data).__name__}"
        )

    playability = extract_playability_status(data)
    age_restricted_statuses = {
        "AGE_CHECK_REQUIRED",
        "AGE_VERIFICATION_REQUIRED",
        "AGE_CHECK_REQUIRED_OR_AGE_VERIFICATION_REQUIRED",
        "AGE_VERIFICATION_REQUIRED_OR_AGE_CHECK_REQUIRED",
        "AGE_GATE",
        "CONTENT_CHECK_REQUIRED",
        "CONTENT_RATING_REQUIRED",
    }
    if playability.get("status") in age_restricted_statuses:
        return True

    return False


# ---------------------------------------------------------------------------
# Recommended next video URL
# ---------------------------------------------------------------------------


def get_recommended_url(data: Dict[str, Any]) -> Optional[str]:
    """Extract the recommended next-video URL if present.

    YouTube embeds a ``recommended`` or ``watchNext`` section in the
    player response.  This function walks common paths to find the first
    available video URL.

    Args:
        data: The raw player response dict.

    Returns:
        A full YouTube watch URL for the next recommended video, or
        ``None`` if no recommendation is available.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_recommended_url expected a dict, got {type(data).__name__}"
        )

    watch_next = _safe_get(data, "watchNext", "unplugged", "videoUrls", default=None)
    if isinstance(watch_next, list) and watch_next:
        return str(watch_next[0])

    recommended = _safe_get(data, "recommended", default=None)
    if isinstance(recommended, list) and recommended:
        first = recommended[0]
        if isinstance(first, dict):
            video_id = _safe_get(first, "videoId", default=None)
            if video_id:
                return f"{constants.YOUTUBE_WATCH_URL_FORMAT.format(video_id=video_id)}"

    watch_next_data = _safe_get(data, "watchNext", default=None)
    if isinstance(watch_next_data, dict):
        url = _safe_get(watch_next_data, "url", default=None)
        if url:
            return str(url)

    return None


# ---------------------------------------------------------------------------
# Endscreen extraction
# ---------------------------------------------------------------------------


def extract_endscreen(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract end-screen element data from the player response.

    End-screen elements are the overlay cards that appear during the
    last few seconds of a video (e.g. "subscribe", "watch next",
    "visit channel").

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``elements`` (list[dict]): Each element dict has ``title``,
          ``url``, ``image``, and ``position`` fields when available.
        * ``endscreen`` (dict | None): The raw
          ``videoDetails.endscreen`` object, or ``None``.

        Returns an empty dict with ``"elements"`` as ``[]`` when no
        end-screen data is present.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_endscreen expected a dict, got {type(data).__name__}"
        )

    video_details = _safe_get(data, "videoDetails", default={})
    if not isinstance(video_details, dict):
        return {"elements": [], "endscreen": None}

    endscreen_raw = _safe_get(video_details, "endscreen", default=None)
    elements: List[Dict[str, Any]] = []

    if isinstance(endscreen_raw, dict):
        raw_elements = _safe_get(endscreen_raw, "endscreenElementRenderer", default=[])
        if isinstance(raw_elements, list):
            for elem in raw_elements:
                if not isinstance(elem, dict):
                    continue
                title = _safe_get(
                    elem,
                    "title",
                    "runs",
                    0,
                    "text",
                    default=None,
                )
                endpoint = _safe_get(elem, "navigationEndpoint", default={})
                url = _safe_get(endpoint, "urlEndpoint", "url", default=None)
                if url is None:
                    url = _safe_get(
                        endpoint,
                        "watchEndpoint",
                        "videoId",
                        default=None,
                    )
                    if url:
                        url = constants.YOUTUBE_WATCH_URL_FORMAT.format(video_id=url)

                image = _safe_get(elem, "thumbnail", "thumbnails", default=[])
                image_url = None
                if isinstance(image, list) and image:
                    last_thumb = image[-1]
                    if isinstance(last_thumb, dict):
                        image_url = _safe_get(last_thumb, "url", default=None)

                elements.append(
                    {
                        "title": title,
                        "url": url,
                        "image": image_url,
                        "position": _safe_get(elem, "left", default=None),
                    }
                )

    return {"elements": elements, "endscreen": endscreen_raw}


# ---------------------------------------------------------------------------
# Cards extraction
# ---------------------------------------------------------------------------


def extract_cards(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract info card data from the player response.

    Info cards are interactive overlay elements that YouTube publishers
    can add to videos.  They are stored in the
    ``videoDetails.endscreen`` or ``cards`` paths of the player response.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``cards`` (list[dict]): Each card dict has ``title``, ``url``,
          ``summary``, and ``icon`` fields when available.
        * ``raw_cards`` (list | None): The raw ``cards`` list from the
          player response, or ``None``.

        Returns ``{"cards": [], "raw_cards": None}`` when no card data
        is present.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_cards expected a dict, got {type(data).__name__}"
        )

    raw_cards = _safe_get(data, "cards", default=None)
    cards_list: List[Dict[str, Any]] = []

    if isinstance(raw_cards, list):
        for card in raw_cards:
            if not isinstance(card, dict):
                continue
            card_renderer = _safe_get(card, "cardRenderer", default=card)
            if not isinstance(card_renderer, dict):
                continue

            title = None
            title_node = _safe_get(card_renderer, "title", default={})
            if isinstance(title_node, dict):
                runs = _safe_get(title_node, "runs", default=[])
                if isinstance(runs, list) and runs:
                    first_run = runs[0]
                    if isinstance(first_run, dict):
                        title = _safe_get(first_run, "text", default=None)

            summary = None
            summary_node = _safe_get(card_renderer, "summary", default={})
            if isinstance(summary_node, dict):
                runs = _safe_get(summary_node, "runs", default=[])
                if isinstance(runs, list) and runs:
                    first_run = runs[0]
                    if isinstance(first_run, dict):
                        summary = _safe_get(first_run, "text", default=None)

            endpoint = _safe_get(card_renderer, "cardClickAnalyticsClickUrl", default=None)
            if endpoint is None:
                endpoint = _safe_get(card_renderer, "urlEndpoint", "url", default=None)
            if endpoint is None:
                endpoint = _safe_get(card_renderer, "navigationEndpoint", default={})
                if isinstance(endpoint, dict):
                    endpoint = _safe_get(endpoint, "urlEndpoint", "url", default=None)

            icon = None
            icon_node = _safe_get(card_renderer, "icon", default={})
            if isinstance(icon_node, dict):
                thumbs = _safe_get(icon_node, "thumbnails", default=[])
                if isinstance(thumbs, list) and thumbs:
                    last_thumb = thumbs[-1]
                    if isinstance(last_thumb, dict):
                        icon = _safe_get(last_thumb, "url", default=None)

            cards_list.append(
                {
                    "title": title,
                    "url": endpoint,
                    "summary": summary,
                    "icon": icon,
                }
            )

    return {"cards": cards_list, "raw_cards": raw_cards}


# ---------------------------------------------------------------------------
# MIME type helper
# ---------------------------------------------------------------------------


def _parse_mime_type(mime: str) -> tuple[str, Optional[str], Optional[str]]:
    """Parse a MIME type string into (container, vcodec, acodec).

    Args:
        mime: A MIME type string such as
            ``"video/webm; codecs=\"vp9,opus\""``.

    Returns:
        A 3-tuple ``(container, vcodec, acodec)`` where *container* is
        the media container (e.g. ``"webm"``), and *vcodec*/*acodec*
        are the video and audio codec identifiers respectively.  Either
        codec field may be ``None`` when not present in the MIME string.
    """
    container: Optional[str] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None

    if not mime:
        return container, vcodec, acodec

    mime_lower = mime.lower().strip()

    if ";" in mime_lower:
        parts = mime_lower.split(";", 1)
        container = parts[0].strip()
        codecs_part = parts[1].strip()
        if codecs_part.startswith("codecs="):
            codecs_raw = codecs_part[len("codecs="):]
            codecs_raw = codecs_raw.strip('"').strip("'")
            codecs = [c.strip() for c in codecs_raw.split(",")]
            from ytdownloader.constants import VIDEO_CODECS, AUDIO_CODECS

            for codec in codecs:
                codec_lower = codec.lower()
                if codec_lower in [vc.lower() for vc in VIDEO_CODECS]:
                    vcodec = codec_lower
                elif codec_lower in [ac.lower() for ac in AUDIO_CODECS]:
                    acodec = codec_lower
    else:
        container = mime_lower.strip()

    return container, vcodec, acodec


# ---------------------------------------------------------------------------
# Comprehensive player response summary builder
# ---------------------------------------------------------------------------


def build_player_response_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build a comprehensive summary dict from the player response.

    Calls all the major extractor functions and collects their results
    into a single flat dict that is convenient for downstream consumers.

    Args:
        data: The raw player response dict (or the output of
            :func:`parse_player_response`).

    Returns:
        A comprehensive dict with all extracted fields.  Keys that
        could not be extracted have a value of ``None`` (or ``0`` for
        numeric counts, or ``[]`` for list fields).

        Top-level keys include:

        * ``video_id``, ``title``, ``author``, ``channel_id``
        * ``length_seconds``, ``view_count``, ``like_count``, ``dislike_count``
        * ``average_rating``, ``short_description``
        * ``is_live``, ``is_private``, ``is_crawlable``, ``allow_ratings``
        * ``streaming_data`` (dict)
        * ``microformat`` (dict)
        * ``playability_status`` (dict)
        * ``captions`` (list)
        * ``audio_tracks`` (list)
        * ``thumbnail_urls`` (list)
        * ``engagement_panels`` (list)
        * ``endscreen`` (dict)
        * ``cards`` (dict)
        * ``recommended_url`` (str | None)
        * ``channel_info`` (dict)
        * ``keywords`` (list)
        * ``description`` (str | None)
        * ``duration_ms`` (int)
        * ``format_summary`` (dict)

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"build_player_response_summary expected a dict, got {type(data).__name__}"
        )

    video_details = extract_video_details(data)
    streaming = extract_streaming_data(data)
    microformat = extract_microformat(data)
    playability = extract_playability_status(data)
    captions = extract_captions(data)
    audio_tracks = extract_audio_tracks(data)
    thumbnails = extract_thumbnail_urls(data)
    panels = extract_engagement_panels(data)
    endscreen = extract_endscreen(data)
    cards = extract_cards(data)
    recommended = get_recommended_url(data)
    channel_info = get_channel_info(data)
    keywords = get_keywords(data)
    description = get_description(data)
    duration_ms = get_duration_ms(data)
    fmt_summary = get_format_summary(data)

    return {
        "video_id": video_details.get("video_id"),
        "title": video_details.get("title"),
        "author": video_details.get("author"),
        "channel_id": video_details.get("channel_id"),
        "length_seconds": video_details.get("length_seconds"),
        "view_count": video_details.get("view_count"),
        "like_count": video_details.get("like_count"),
        "dislike_count": video_details.get("dislike_count"),
        "average_rating": video_details.get("average_rating"),
        "short_description": video_details.get("short_description"),
        "is_live": video_details.get("is_live"),
        "is_private": video_details.get("is_private"),
        "is_crawlable": video_details.get("is_crawlable"),
        "allow_ratings": video_details.get("allow_ratings"),
        "streaming_data": streaming,
        "microformat": microformat,
        "playability_status": playability,
        "captions": captions,
        "audio_tracks": audio_tracks,
        "thumbnail_urls": thumbnails,
        "engagement_panels": panels,
        "endscreen": endscreen,
        "cards": cards,
        "recommended_url": recommended,
        "channel_info": channel_info,
        "keywords": keywords,
        "description": description,
        "duration_ms": duration_ms,
        "format_summary": fmt_summary,
    }


# ---------------------------------------------------------------------------
# Low-level raw field accessor with fallback paths
# ---------------------------------------------------------------------------


def get_raw_field(
    data: Dict[str, Any],
    field: str,
    fallback_keys: Optional[List[str]] = None,
    default: Any = None,
) -> Any:
    """Retrieve a field from the player response with fallback key paths.

    This helper tries *field* first at the top level of *data*, then
    inside ``videoDetails``, and finally inside
    ``microformat.playerMicroformatRenderer``.  Additional fallback
    paths can be supplied via *fallback_keys*.

    Args:
        data: The raw player response dict.
        field: The primary field name to look up.
        fallback_keys: Optional list of additional key paths to try.
            Each element is a dot-separated string of nested keys
            (e.g. ``"videoDetails.lengthSeconds"``).
        default: Value returned when the field is not found in any
            location.

    Returns:
        The field value, or *default*.
    """
    if not isinstance(data, dict):
        raise TypeError(f"get_raw_field expected a dict, got {type(data).__name__}")

    candidates: List[Any] = []

    candidates.append(_safe_get(data, field, default=None))
    candidates.append(_safe_get(data, "videoDetails", field, default=None))
    candidates.append(
        _safe_get(data, "microformat", "playerMicroformatRenderer", field, default=None)
    )

    if fallback_keys:
        for path in fallback_keys:
            keys = path.split(".")
            candidates.append(_safe_get(data, *keys, default=None))

    for candidate in candidates:
        if candidate is not None:
            return candidate

    return default


# ---------------------------------------------------------------------------
# Storyboard / thumbnail sequence extraction
# ---------------------------------------------------------------------------


def get_storyboards(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract storyboard thumbnail sequences if present.

    YouTube sometimes provides a sequence of thumbnail images that
    form a visual preview timeline (storyboard).  This function
    returns all available storyboard entries.

    Args:
        data: The raw player response dict.

    Returns:
        A list of dicts, each with keys:

        * ``url`` (str): Template URL for the storyboard image.
        * ``width`` (int | None): Width of each thumbnail tile.
        * ``height`` (int | None): Height of each thumbnail tile.
        * ``count`` (int | None): Total number of thumbnail tiles.
        * ``interval_ms`` (int | None): Milliseconds between frames.

        The list is empty when no storyboard data is present.
    """
    if not isinstance(data, dict):
        raise TypeError(f"get_storyboards expected a dict, got {type(data).__name__}")

    storyboards: List[Dict[str, Any]] = []

    walker = _safe_get(data, "storyboards", default={})
    if not isinstance(walker, dict):
        return storyboards

    for key, value in walker.items():
        if not isinstance(value, dict):
            continue
        url = _safe_get(value, "url", default=None)
        if url is None:
            url = _safe_get(value, "recommendedUrl", default=None)
        if url is None:
            continue

        thumbs = _safe_get(value, "thumbnails", default=[])
        width = None
        height = None
        if isinstance(thumbs, list) and thumbs:
            last = thumbs[-1]
            if isinstance(last, dict):
                width = _parse_int_safe(last.get("width"), default=None)
                height = _parse_int_safe(last.get("height"), default=None)

        storyboards.append(
            {
                "url": str(url),
                "width": width,
                "height": height,
                "count": _parse_int_safe(value.get("count"), default=None),
                "interval_ms": _parse_int_safe(value.get("intervalMs"), default=None),
            }
        )

    logger.debug("get_storyboards: found %d storyboard sets", len(storyboards))
    return storyboards


# ---------------------------------------------------------------------------
# Live broadcast details
# ---------------------------------------------------------------------------


def get_live_details(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract live broadcast-specific details from the player response.

    When a video is a live stream, YouTube includes additional fields
    such as the scheduled start time and the number of concurrent
    viewers.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``is_live`` (bool)
        * ``is_live_now`` (bool | None)
        * ``scheduled_start_time`` (int | None): Unix timestamp.
        * ``scheduled_end_time`` (int | None): Unix timestamp.
        * ``concurrent_viewers`` (int | None)
        * ``video_id`` (str | None)

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(f"get_live_details expected a dict, got {type(data).__name__}")

    video_details = _safe_get(data, "videoDetails", default={})
    live_details = _safe_get(video_details, "liveBroadcastDetails", default={})

    if not isinstance(live_details, dict):
        live_details = {}

    is_live_now_raw = _safe_get(live_details, "isLiveNow", default=None)
    is_live_now: Optional[bool] = None
    if is_live_now_raw is not None:
        is_live_now = bool(is_live_now_raw)

    return {
        "is_live": bool(video_details.get("isLive", False)) if isinstance(video_details, dict) else False,
        "is_live_now": is_live_now,
        "scheduled_start_time": _parse_int_safe(
            _safe_get(live_details, "scheduledStartTime", default=None)
        ),
        "scheduled_end_time": _parse_int_safe(
            _safe_get(live_details, "scheduledEndTime", default=None)
        ),
        "concurrent_viewers": _parse_int_safe(
            _safe_get(live_details, "concurrentViewers", default=None)
        ),
        "video_id": _safe_get(video_details, "videoId", default=None),
    }


# ---------------------------------------------------------------------------
# Privacy / monetization status
# ---------------------------------------------------------------------------


def get_monetization_status(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract monetization and privacy information.

    Checks for indicators such as whether the video is behind a paywall
    or whether ad insertion is possible.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``is_monetized`` (bool | None): Whether ads are enabled.
        * ``is_paid`` (bool | None): Whether the video is a paid
          promotion.
        * ``is_family_safe`` (bool | None): Whether the video is
          marked as family-safe.
        * ``is_unplugged_corpus`` (bool | None): Whether the video is
          part of the YouTube Unplugged corpus.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_monetization_status expected a dict, got {type(data).__name__}"
        )

    microformat = _safe_get(data, "microformat", "playerMicroformatRenderer", default={})
    if not isinstance(microformat, dict):
        microformat = {}

    return {
        "is_monetized": _safe_get(microformat, "isMonetized", default=None),
        "is_paid": _safe_get(microformat, "isPaid", default=None),
        "is_family_safe": _safe_get(microformat, "isFamilySafe", default=None),
        "is_unplugged_corpus": _safe_get(microformat, "isUnpluggedCorpus", default=None),
    }


# ---------------------------------------------------------------------------
# Tracking / analytics parameters
# ---------------------------------------------------------------------------


def get_tracking_params(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract tracking and analytics parameters from the player response.

    YouTube embeds various tracking parameters in the player response
    used by Google Analytics and internal measurement systems.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``videostats_playback_url`` (str | None)
        * ``videostats_heartbeat_url`` (str | None)
        * ``url_c`` (str | None): ``url_c`` value used for client
          identification.
        * ``referrer`` (str | None): Referrer URL.
        * ``root_ve_type`` (int | None): ``root_ve_type`` value.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_tracking_params expected a dict, got {type(data).__name__}"
        )

    tracking = _safe_get(data, "trackingParams", default=None)
    videostats = _safe_get(data, "videoDetails", "videostatsPlaybackUrl", default=None)

    return {
        "videostats_playback_url": videostats,
        "videostats_heartbeat_url": _safe_get(
            data, "videoDetails", "videostatsHeartbeatUrl", default=None
        ),
        "url_c": _safe_get(data, "urlC", default=None),
        "referrer": _safe_get(data, "referrer", default=None),
        "root_ve_type": _parse_int_safe(_safe_get(data, "rootVeType", default=None)),
    }


# ---------------------------------------------------------------------------
# Ad placement detection
# ---------------------------------------------------------------------------


def get_ad_placements(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ad placement data from the player response.

    YouTube may include ad placement metadata in the player response
    for mid-roll and other ad formats.

    Args:
        data: The raw player response dict.

    Returns:
        A list of dicts, each representing an ad placement slot with
        keys ``slot_type`` (str | None) and ``duration_ms`` (int | None).
        The list is empty when no ad placement data is found.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_ad_placements expected a dict, got {type(data).__name__}"
        )

    placements: List[Dict[str, Any]] = []

    ad_placements = _safe_get(data, "adPlacements", default=[])
    if not isinstance(ad_placements, list):
        return placements

    for slot in ad_placements:
        if not isinstance(slot, dict):
            continue
        renderer = _safe_get(slot, "adSlotRenderer", default=slot)
        if not isinstance(renderer, dict):
            continue
        placements.append(
            {
                "slot_type": _safe_get(renderer, "slotType", default=None),
                "duration_ms": _parse_int_safe(
                    _safe_get(renderer, "durationMs", default=None)
                ),
            }
        )

    logger.debug("get_ad_placements: found %d ad placements", len(placements))
    return placements


# ---------------------------------------------------------------------------
# Chapter markers extraction
# ---------------------------------------------------------------------------


def get_chapters(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract chapter markers from the player response if present.

    YouTube allows video creators to add chapter markers that appear
    as time-stamped sections in the description.  Some player
    responses include structured chapter data.

    Args:
        data: The raw player response dict.

    Returns:
        A list of dicts, one per chapter.  Each dict has keys:

        * ``title`` (str | None): Chapter title.
        * ``start_time_ms`` (int | None): Start time in milliseconds.
        * ``end_time_ms`` (int | None): End time in milliseconds.

        The list is empty when no chapter data is present.
    """
    if not isinstance(data, dict):
        raise TypeError(f"get_chapters expected a dict, got {type(data).__name__}")

    chapters: List[Dict[str, Any]] = []

    microformat = _safe_get(data, "microformat", "playerMicroformatRenderer", default={})
    if not isinstance(microformat, dict):
        return chapters

    chapter_data = _safe_get(microformat, "chapters", default=[])
    if not isinstance(chapter_data, list):
        return chapters

    for chapter in chapter_data:
        if not isinstance(chapter, dict):
            continue
        renderer = _safe_get(chapter, "chapterRenderer", default=chapter)
        if not isinstance(renderer, dict):
            continue
        title_node = _safe_get(renderer, "title", "simpleText", default=None)
        if title_node is None:
            runs = _safe_get(renderer, "title", "runs", default=[])
            if isinstance(runs, list) and runs:
                first = runs[0]
                if isinstance(first, dict):
                    title_node = _safe_get(first, "text", default=None)
        chapters.append(
            {
                "title": title_node,
                "start_time_ms": _parse_int_safe(
                    _safe_get(renderer, "startTimeMs", default=None)
                ),
                "end_time_ms": _parse_int_safe(
                    _safe_get(renderer, "endTimeMs", default=None)
                ),
            }
        )

    logger.debug("get_chapters: found %d chapters", len(chapters))
    return chapters


# ---------------------------------------------------------------------------
# Description snippet / expanded description
# ---------------------------------------------------------------------------


def get_description_snippet(data: Dict[str, Any]) -> Optional[str]:
    """Extract the short description snippet shown in search results.

    Args:
        data: The raw player response dict.

    Returns:
        The description snippet string, or ``None`` when not available.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_description_snippet expected a dict, got {type(data).__name__}"
        )

    snippet = _safe_get(
        data,
        "videoDetails",
        "descriptionSnippet",
        "runs",
        0,
        "text",
        default=None,
    )
    if snippet is None:
        snippet = _safe_get(data, "videoDetails", "shortDescription", default=None)
    return str(snippet) if snippet is not None else None


# ---------------------------------------------------------------------------
# Video availability checks
# ---------------------------------------------------------------------------


def is_geo_restricted(data: Dict[str, Any]) -> bool:
    """Determine whether the video is geo-restricted.

    Checks the playability status for geo-restriction codes and the
    microformat for geo-availability flags.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if the video is geo-restricted, ``False`` otherwise.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"is_geo_restricted expected a dict, got {type(data).__name__}"
        )

    playability = extract_playability_status(data)
    geo_statuses = {"GEO_RESTRICTED"}
    if playability.get("status") in geo_statuses:
        return True

    microformat = _safe_get(data, "microformat", "playerMicroformatRenderer", default={})
    if isinstance(microformat, dict):
        unavailable = _safe_get(microformat, "isUnavailable", default=False)
        if unavailable:
            reason = _safe_get(microformat, "unavailableReason", default="")
            if "country" in str(reason).lower() or "region" in str(reason).lower():
                return True

    return False


def is_embeddable(data: Dict[str, Any]) -> bool:
    """Determine whether the video can be embedded on other sites.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if embedding is allowed, ``False`` otherwise.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"is_embeddable expected a dict, got {type(data).__name__}"
        )

    playability = extract_playability_status(data)
    if playability.get("status") == "EMBEDDING_DISABLED":
        return False

    microformat = _safe_get(data, "microformat", "playerMicroformatRenderer", default={})
    if isinstance(microformat, dict):
        return bool(microformat.get("isEmbeddable", True))
    return True


def is_available(data: Dict[str, Any]) -> bool:
    """Determine whether the video is generally available for viewing.

    This is a combined check that returns ``False`` for private,
    deleted, geo-restricted, or age-restricted videos that are not
    otherwise playable.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if the video can be viewed, ``False`` otherwise.
    """
    if not isinstance(data, dict):
        raise TypeError(f"is_available expected a dict, got {type(data).__name__}")

    playability = extract_playability_status(data)
    non_available_statuses = {
        "ERROR",
        "LOGIN_REQUIRED",
        "UNPLAYABLE",
        "PRIVATE_VIDEO",
        "VIDEO_NOT_FOUND",
        "AGE_CHECK_REQUIRED",
        "AGE_VERIFICATION_REQUIRED",
        "AGE_GATE",
        "GEO_RESTRICTED",
    }
    if playability.get("status") in non_available_statuses:
        return False

    if is_private(data):
        return False

    return True


# ---------------------------------------------------------------------------
# Format URL accessor
# ---------------------------------------------------------------------------


def get_stream_urls(data: Dict[str, Any]) -> Dict[str, List[str]]:
    """Extract direct stream URLs grouped by type.

    Iterates over all formats in the player response and collects
    direct ``url`` values.  Formats that only have a
    ``signatureCipher`` are skipped because the URL cannot be resolved
    without first deciphering the signature.

    Args:
        data: The raw player response dict.

    Returns:
        A dict with keys:

        * ``combined`` (list[str]): Direct URLs for progressive formats.
        * ``adaptive`` (list[str]): Direct URLs for adaptive formats.
        * ``audio_only`` (list[str]): Direct URLs for audio-only formats.

    Raises:
        TypeError: If *data* is not a dict.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_stream_urls expected a dict, got {type(data).__name__}"
        )

    combined_urls: List[str] = []
    adaptive_urls: List[str] = []
    audio_only_urls: List[str] = []

    try:
        streaming = extract_streaming_data(data)
    except StreamResolutionError:
        return {
            "combined": combined_urls,
            "adaptive": adaptive_urls,
            "audio_only": audio_only_urls,
        }

    for fmt in streaming.get("formats", []):
        if not isinstance(fmt, dict):
            continue
        url = _safe_get(fmt, "url", default=None)
        if url:
            combined_urls.append(str(url))

    for fmt in streaming.get("adaptiveFormats", []):
        if not isinstance(fmt, dict):
            continue
        url = _safe_get(fmt, "url", default=None)
        vcodec = _safe_get(fmt, "videoCodec", default=None)
        acodec = _safe_get(fmt, "audioCodec", default=None)
        has_video = vcodec is not None and vcodec != "none"
        has_audio = acodec is not None and acodec != "none"
        if url:
            if has_audio and not has_video:
                audio_only_urls.append(str(url))
            else:
                adaptive_urls.append(str(url))

    return {
        "combined": combined_urls,
        "adaptive": adaptive_urls,
        "audio_only": audio_only_urls,
    }


# ---------------------------------------------------------------------------
# String representation helper
# ---------------------------------------------------------------------------


def format_player_response_info(data: Dict[str, Any]) -> str:
    """Produce a human-readable summary string for the player response.

    The summary includes the video title, author, duration, view count,
    quality ceiling, and playability status.

    Args:
        data: The raw player response dict (or the output of
            :func:`parse_player_response`).

    Returns:
        A multi-line string with formatted video information.
    """
    if not isinstance(data, dict):
        return "Invalid player response (not a dict)"

    details = extract_video_details(data)
    playability = extract_playability_status(data)
    fmt_summary = get_format_summary(data)

    lines: List[str] = []

    title = details.get("title") or "Unknown title"
    author = details.get("author") or "Unknown author"
    lines.append(f"Title:   {title}")
    lines.append(f"Author:  {author}")

    video_id = details.get("video_id")
    if video_id:
        lines.append(f"Video ID: {video_id}")

    duration = details.get("length_seconds", 0)
    minutes, seconds = divmod(duration, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        duration_str = f"{hours:d}:{minutes:02d}:{seconds:02d}"
    else:
        duration_str = f"{minutes:02d}:{seconds:02d}"
    lines.append(f"Duration: {duration_str} ({duration} seconds)")

    view_count = details.get("view_count", 0)
    if view_count > 0:
        lines.append(f"Views:   {view_count:,}")
    else:
        lines.append("Views:   N/A")

    is_live = details.get("is_live", False)
    lines.append(f"Live:    {'Yes' if is_live else 'No'}")

    is_private = details.get("is_private", False)
    lines.append(f"Private: {'Yes' if is_private else 'No'}")

    status = playability.get("status", "UNKNOWN")
    lines.append(f"Status:  {status}")

    max_h = fmt_summary.get("max_height")
    if max_h:
        lines.append(f"Max quality: {max_h}p")
    lines.append(f"Formats: {fmt_summary.get('total_count', 0)} total "
                 f"({fmt_summary.get('combined_count', 0)} combined, "
                 f"{fmt_summary.get('adaptive_count', 0)} adaptive)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch extraction helpers
# ---------------------------------------------------------------------------


def extract_all_format_itags(data: Dict[str, Any]) -> List[int]:
    """Return a list of all available itag numbers.

    Args:
        data: The raw player response dict.

    Returns:
        A sorted list of unique itag integers present in the response.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"extract_all_format_itags expected a dict, got {type(data).__name__}"
        )

    itags: set = set()
    try:
        streaming = extract_streaming_data(data)
    except StreamResolutionError:
        return []

    for fmt in streaming.get("formats", []) + streaming.get("adaptiveFormats", []):
        if not isinstance(fmt, dict):
            continue
        itag = _parse_int_safe(fmt.get("itag"), default=None)
        if itag is not None:
            itags.add(itag)

    return sorted(itags)


def get_best_available_quality(data: Dict[str, Any]) -> Optional[str]:
    """Return the quality label of the highest-quality format available.

    Args:
        data: The raw player response dict.

    Returns:
        A quality label string such as ``"1080p"``, or ``None`` when no
        video formats are available.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_best_available_quality expected a dict, got {type(data).__name__}"
        )

    best_height: Optional[int] = None
    best_label: Optional[str] = None

    try:
        all_fmts = iter_all_formats(data)
    except StreamResolutionError:
        return None

    for fmt in all_fmts:
        if not fmt.get("vcodec") or fmt.get("vcodec") == "none":
            continue
        height = fmt.get("height")
        label = fmt.get("quality_label")
        if height is not None and isinstance(height, int) and height > 0:
            if best_height is None or height > best_height:
                best_height = height
                best_label = label or f"{height}p"

    return best_label


def has_formats(data: Dict[str, Any]) -> bool:
    """Check whether the player response contains any streamable formats.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if at least one format is present, ``False`` otherwise.
    """
    if not isinstance(data, dict):
        raise TypeError(f"has_formats expected a dict, got {type(data).__name__}")

    try:
        streaming = extract_streaming_data(data)
    except StreamResolutionError:
        return False

    return bool(
        streaming.get("formats") or streaming.get("adaptiveFormats")
    )


def has_captions(data: Dict[str, Any]) -> bool:
    """Check whether the video has any caption/subtitle tracks.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if at least one caption track is present, ``False``
        otherwise.
    """
    if not isinstance(data, dict):
        raise TypeError(f"has_captions expected a dict, got {type(data).__name__}")

    return len(extract_captions(data)) > 0


def get_video_id_fast(data: Dict[str, Any]) -> Optional[str]:
    """Fast-path extraction of the video ID.

    Accesses the ``videoDetails.videoId`` field directly without
    running the full validation pipeline.

    Args:
        data: The raw player response dict.

    Returns:
        The video ID string, or ``None`` if not present.
    """
    return _safe_get(data, "videoDetails", "videoId", default=None)


def get_playability_reason(data: Dict[str, Any]) -> Optional[str]:
    """Extract the playability failure reason if the video is not playable.

    Args:
        data: The raw player response dict.

    Returns:
        The human-readable reason string, or ``None`` if the video is
        playable.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_playability_reason expected a dict, got {type(data).__name__}"
        )
    playability = extract_playability_status(data)
    status = playability.get("status", "OK")
    if status == "OK":
        return None
    return playability.get("reason") or status


def requires_signature(data: Dict[str, Any]) -> bool:
    """Check whether any format requires signature deciphering.

    Returns ``True`` if at least one format entry contains a
    ``signatureCipher`` field, indicating the URL must be signed
    before it can be used for download.

    Args:
        data: The raw player response dict.

    Returns:
        ``True`` if signature deciphering is required, ``False``
        otherwise.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"requires_signature expected a dict, got {type(data).__name__}"
        )

    try:
        streaming = extract_streaming_data(data)
    except StreamResolutionError:
        return False

    for fmt in streaming.get("formats", []) + streaming.get("adaptiveFormats", []):
        if not isinstance(fmt, dict):
            continue
        if fmt.get("signatureCipher") is not None:
            return True
    return False


def get_dash_manifest_url(data: Dict[str, Any]) -> Optional[str]:
    """Extract the DASH manifest URL if present.

    Some player responses include a ``dashManifestUrl`` field that
    provides a full DASH manifest for segmented streaming.

    Args:
        data: The raw player response dict.

    Returns:
        The DASH manifest URL string, or ``None`` if not present.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_dash_manifest_url expected a dict, got {type(data).__name__}"
        )
    return _safe_get(data, "streamingData", "dashManifestUrl", default=None)


def get_hls_manifest_url(data: Dict[str, Any]) -> Optional[str]:
    """Extract the HLS manifest URL if present.

    Some player responses include an ``hlsManifestUrl`` field for
    HLS (HTTP Live Streaming) playback.

    Args:
        data: The raw player response dict.

    Returns:
        The HLS manifest URL string, or ``None`` if not present.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_hls_manifest_url expected a dict, got {type(data).__name__}"
        )
    return _safe_get(data, "streamingData", "hlsManifestUrl", default=None)


# ---------------------------------------------------------------------------
# Low-level raw data pass-through
# ---------------------------------------------------------------------------


def get_raw_streaming_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the raw ``streamingData`` dict without normalisation.

    This is a low-level accessor for callers that need the full
    unmodified streaming data object (including all format entries
    with their original field names).

    Args:
        data: The raw player response dict.

    Returns:
        The raw ``streamingData`` dict, or ``None`` if not present.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_raw_streaming_data expected a dict, got {type(data).__name__}"
        )
    return _safe_get(data, "streamingData", default=None)


def get_raw_video_details(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the raw ``videoDetails`` dict without modification.

    Args:
        data: The raw player response dict.

    Returns:
        The raw ``videoDetails`` dict, or ``None`` if not present.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_raw_video_details expected a dict, got {type(data).__name__}"
        )
    return _safe_get(data, "videoDetails", default=None)


def get_raw_captions(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the raw ``captions`` dict without modification.

    Args:
        data: The raw player response dict.

    Returns:
        The raw ``captions`` dict, or ``None`` if not present.
    """
    if not isinstance(data, dict):
        raise TypeError(
            f"get_raw_captions expected a dict, got {type(data).__name__}"
        )
    return _safe_get(data, "captions", default=None)


# ---------------------------------------------------------------------------
# String-to-bool safe conversion
# ---------------------------------------------------------------------------


def _str_to_bool(value: Any, default: bool = False) -> bool:
    """Safely convert a value to a boolean.

    Handles string representations of booleans such as ``"true"``,
    ``"false"``, ``"1"``, and ``"0"``.

    Args:
        value: The value to convert.
        default: The value returned when conversion fails.

    Returns:
        A boolean.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return default
