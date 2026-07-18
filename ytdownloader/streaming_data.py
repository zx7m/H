"""
Comprehensive streaming data parser for YouTube video format extraction and manipulation.

This module provides utilities for parsing YouTube's streaming data (from
``ytInitialPlayerResponse``), converting raw format dicts into structured
``StreamFormat`` dataclass instances, and performing common filtering, sorting,
and selection operations on collections of formats.

The primary entry point is :func:`parse_streaming_data`, which accepts the raw
``streamingData`` dict extracted from YouTube's player response and returns a
list of :class:`StreamFormat` objects representing every available format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ytdownloader.exceptions import StreamResolutionError
from ytdownloader.logger import get_logger
from ytdownloader.constants import (
    AUDIO_CODECS,
    AUDIO_ONLY_ITAGS,
    CONTAINERS,
    ITAG_DETAILS,
    ITAG_QUALITY,
    MAX_QUALITY,
    MIN_QUALITY,
    PROGRESSIVE_ITAGS,
    PROTOCOLS,
    QUALITY_HEIGHT_MAP,
    VIDEO_CODECS,
    VIDEO_ONLY_ITAGS,
)

_logger = get_logger(__name__)


__all__ = [
    "StreamDataError",
    "StreamFormat",
    "parse_streaming_data",
    "parse_single_format",
    "filter_formats",
    "sort_formats",
    "get_best_format",
    "get_format_by_itag",
    "get_audio_only_formats",
    "get_video_only_formats",
    "get_combined_formats",
    "_parse_mime_type",
    "_parse_content_length",
    "_estimate_size",
    "quality_sort_key",
    "get_format_summary",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StreamDataError(StreamResolutionError):
    """Raised when streaming data parsing encounters an unrecoverable error."""


# ---------------------------------------------------------------------------
# StreamFormat dataclass
# ---------------------------------------------------------------------------


@dataclass
class StreamFormat:
    """Structured representation of a single YouTube stream format.

    Attributes:
        itag: YouTube format identifier (e.g. ``18``, ``251``).
        ext: File extension (e.g. ``"mp4"``, ``"webm"``).
        vcodec: Video codec name (e.g. ``"avc1"``, ``"vp9"``) or ``"none"``.
        acodec: Audio codec name (e.g. ``"aac"``, ``"opus"``) or ``"none"``.
        width: Video width in pixels, or ``None`` for audio-only formats.
        height: Video height in pixels, or ``None`` for audio-only formats.
        fps: Frames per second, or ``None`` if not reported.
        tbr: Total bitrate in kbps (combined audio + video), or ``None``.
        abr: Audio bitrate in kbps, or ``None`` for audio-less formats.
        vbr: Video bitrate in kbps, or ``None`` for video-less formats.
        acontainer: Audio container format (e.g. ``"mp4"``, ``"webm"``).
        vcontainer: Video container format (e.g. ``"mp4"``, ``"webm"``).
        mimeType: Raw MIME type string from the format dict.
        protocol: Streaming protocol (e.g. ``"https"``, ``"dash"``, ``"hls"``).
        url: Resolved stream URL, or ``None`` if unavailable.
        signature_cipher: Raw ``signatureCipher`` value, or ``None``.
        content_length: File size in bytes, or ``None`` if unknown.
        approx_duration_ms: Approximate duration in milliseconds, or ``None``.
        is_dash: ``True`` if the format uses DASH segmented streaming.
        is_hls: ``True`` if the format uses HLS segmented streaming.
        quality_label: Human-readable quality string (e.g. ``"1080p"``).
        quality_ordinal: Integer rank used for format comparison (higher = better).
    """

    itag: int = 0
    ext: str = ""
    vcodec: str = "none"
    acodec: str = "none"
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    tbr: Optional[float] = None
    abr: Optional[float] = None
    vbr: Optional[float] = None
    acontainer: str = ""
    vcontainer: str = ""
    mimeType: str = ""
    protocol: str = ""
    url: str = ""
    signature_cipher: Optional[str] = None
    content_length: Optional[int] = None
    approx_duration_ms: Optional[int] = None
    is_dash: bool = False
    is_hls: bool = False
    quality_label: str = ""
    quality_ordinal: int = 0

    def __post_init__(self) -> None:
        """Derive missing fields and validate after initialization."""
        if not self.ext and self.mimeType:
            self.ext = _ext_to_ext(self.mimeType)

        if self.itag and self.quality_ordinal == 0:
            itag_info = ITAG_DETAILS.get(self.itag, {})
            if itag_info:
                if not self.vcontainer:
                    self.vcontainer = itag_info.get("container", "")
                if not self.acontainer:
                    self.acontainer = itag_info.get("container", "")
                if self.vcodec == "none" and itag_info.get("vcodec"):
                    self.vcodec = itag_info["vcodec"]
                if self.acodec == "none" and itag_info.get("acodec"):
                    self.acodec = itag_info["acodec"]
                if not self.protocol:
                    self.protocol = itag_info.get("protocol", self.protocol)

        if not self.quality_ordinal:
            self.quality_ordinal = _compute_quality_ordinal(
                quality_label=self.quality_label,
                itag=self.itag,
                tbr=self.tbr,
                height=self.height,
            )

    @property
    def is_video_only(self) -> bool:
        """``True`` when this format carries video but no audio."""
        return self.vcodec not in (None, "", "none") and self.acodec in (
            None,
            "",
            "none",
        )

    @property
    def is_audio_only(self) -> bool:
        """``True`` when this format carries audio but no video."""
        return self.acodec not in (None, "", "none") and self.vcodec in (
            None,
            "",
            "none",
        )

    @property
    def is_combined(self) -> bool:
        """``True`` when this format carries both audio and video."""
        return self.vcodec not in (None, "", "none") and self.acodec not in (
            None,
            "",
            "none",
        )

    @property
    def estimated_size(self) -> Optional[int]:
        """Estimated file size in bytes.

        Falls back to :func:`_estimate_size` when ``content_length`` is absent.
        """
        if self.content_length is not None:
            return self.content_length
        if self.approx_duration_ms is not None and self.tbr is not None:
            duration_s = self.approx_duration_ms / 1000.0
            return _estimate_size(None, duration_s, self.tbr)
        return None

    @property
    def approx_duration_s(self) -> Optional[float]:
        """Approximate duration in seconds, or ``None`` if unknown."""
        if self.approx_duration_ms is not None:
            return self.approx_duration_ms / 1000.0
        return None

    @property
    def effective_bitrate(self) -> Optional[float]:
        """Best available bitrate (prefers total bitrate, then video bitrate)."""
        if self.tbr is not None:
            return self.tbr
        if self.vbr is not None:
            return self.vbr
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this format to a plain dictionary.

        Returns:
            A dictionary containing all public attributes of this format.
        """
        return {
            "itag": self.itag,
            "ext": self.ext,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "tbr": self.tbr,
            "abr": self.abr,
            "vbr": self.vbr,
            "acontainer": self.acontainer,
            "vcontainer": self.vcontainer,
            "mimeType": self.mimeType,
            "protocol": self.protocol,
            "url": self.url,
            "signature_cipher": self.signature_cipher,
            "content_length": self.content_length,
            "approx_duration_ms": self.approx_duration_ms,
            "is_dash": self.is_dash,
            "is_hls": self.is_hls,
            "quality_label": self.quality_label,
            "quality_ordinal": self.quality_ordinal,
            "is_video_only": self.is_video_only,
            "is_audio_only": self.is_audio_only,
            "is_combined": self.is_combined,
            "estimated_size": self.estimated_size,
        }

    def __repr__(self) -> str:
        parts = [f"StreamFormat(itag={self.itag}"]
        if self.quality_label:
            parts.append(f" quality={self.quality_label!r}")
        if self.height:
            parts.append(f" {self.width}x{self.height}")
        if self.fps:
            parts.append(f"@{self.fps}fps")
        if self.vcodec != "none":
            parts.append(f" vcodec={self.vcodec}")
        if self.acodec != "none":
            parts.append(f" acodec={self.acodec}")
        if self.protocol:
            parts.append(f" protocol={self.protocol}")
        if self.ext:
            parts.append(f" ext={self.ext}")
        parts.append(")")
        return "".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ext_to_ext(mime_type: str) -> str:
    """Infer file extension from a MIME type string.

    Args:
        mime_type: Raw MIME type (e.g. ``"video/webm; codecs=vp9"``).

    Returns:
        The most likely file extension, or an empty string if unknown.
    """
    container = _parse_mime_type(mime_type)[0]
    mime_to_ext: Dict[str, str] = {
        "mp4": "mp4",
        "webm": "webm",
        "flv": "flv",
        "3gp": "3gp",
        "m4a": "m4a",
        "weba": "weba",
        "mpeg": "mp3",
        "aac": "aac",
        "mp4a": "m4a",
        "opus": "webm",
        "vorbis": "webm",
    }
    return mime_to_ext.get(container, container)


def _compute_quality_ordinal(
    quality_label: str,
    itag: int,
    tbr: Optional[float],
    height: Optional[int],
) -> int:
    """Compute a sortable integer rank for a format.

    Higher values indicate better quality.  The rank is derived from the
    quality label (pixel height) first, then total bitrate, then itag as a
    final tie-breaker.

    Args:
        quality_label: Human-readable quality string (e.g. ``"1080p"``).
        itag: YouTube format identifier.
        tbr: Total bitrate in kbps.
        height: Video height in pixels.

    Returns:
        A positive integer representing format quality rank.
    """
    rank = 0
    if quality_label:
        match = re.search(r"(\d+)", quality_label)
        if match:
            rank = int(match.group(1)) * 10000

    if rank == 0 and height is not None:
        rank = height * 10000

    if tbr is not None:
        rank += int(tbr * 10)

    rank += itag
    return rank


def _parse_mime_type(mime: str) -> Tuple[str, str, str]:
    """Parse a raw MIME type string into its components.

    Handles formats such as:
    - ``"video/webm; codecs=vp9"``
    - ``"video/mp4; codecs=avc1.640028"``
    - ``"audio/webm; codecs=opus"``
    - ``"video/3gpp; codecs=mp4v.20.9"``

    Args:
        mime: Raw MIME type string from the format dict.

    Returns:
        A 3-tuple ``(container, vcodec, acodec)`` where each element is a
        lowercase string, or an empty string when the codec type is absent.
    """
    container = ""
    vcodec = ""
    acodec = ""

    if not mime:
        return container, vcodec, acodec

    mime_lower = mime.lower().strip()
    main_type = mime_lower.split(";")[0].strip()

    known_containers = {
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/x-flv": "flv",
        "video/3gpp": "3gp",
        "audio/mp4": "mp4",
        "audio/webm": "webm",
        "audio/aac": "aac",
        "audio/mpeg": "mp3",
        "application/x-mpegURL": "m3u8",
    }
    container = known_containers.get(main_type, main_type.split("/")[-1])

    codecs_match = re.search(r"codecs[=\"']+([^\"';]+)", mime, re.IGNORECASE)
    if codecs_match:
        codecs_str = codecs_match.group(1).strip().strip('"')
        codecs = [c.strip().strip('"').lower() for c in codecs_str.split(",")]
        for codec in codecs:
            if codec in VIDEO_CODECS:
                vcodec = codec
            elif codec in AUDIO_CODECS:
                acodec = codec
            elif codec.startswith("avc1") or codec.startswith("avc"):
                vcodec = "avc1"
            elif codec.startswith("vp9"):
                vcodec = "vp9"
            elif codec.startswith("vp8"):
                vcodec = "vp8"
            elif codec.startswith("h263"):
                vcodec = "h263"
            elif codec.startswith("mp4v"):
                vcodec = "mp4v"
            elif codec.startswith("opus"):
                acodec = "opus"
            elif codec.startswith("vorbis"):
                acodec = "vorbis"
            elif codec.startswith("mp4a") or codec.startswith("aac"):
                acodec = "aac"
            elif codec.startswith("mp3"):
                acodec = "mp3"

    if main_type.startswith("audio/") and not vcodec and not acodec:
        acodec = "aac"
    elif main_type.startswith("video/") and not vcodec and not acodec:
        vcodec = container

    return container, vcodec, acodec


def _parse_content_length(value: Any) -> Optional[int]:
    """Safely parse a ``contentLength`` value to an integer.

    YouTube's API sometimes returns the content length as a string, a number,
    or ``None``.  This helper normalizes all cases and returns ``None`` when
    the value is missing or non-numeric.

    Args:
        value: Raw content length value from the format dict.

    Returns:
        File size in bytes as an integer, or ``None`` if unparseable.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            pass
        try:
            return int(float(stripped))
        except ValueError:
            _logger.warning("Unable to parse contentLength value: %r", value)
            return None
    return None


def _estimate_size(
    content_length: Optional[int],
    duration_s: float,
    bitrate_kbps: Optional[float] = None,
) -> int:
    """Estimate the file size for a stream.

    When the actual ``content_length`` is not known, this function estimates
    the size from the bitrate and duration.  The bitrate is inferred from
    ``tbr`` (total bitrate) or ``vbr`` (video bitrate) if not explicitly
    provided.

    Args:
        content_length: Known file size in bytes, or ``None`` to estimate.
        duration_s: Duration of the stream in seconds.
        bitrate_kbps: Bitrate in kilobits per second.  When ``None`` the
            function uses ``content_length / duration_s * 8 / 1000`` if both
            ``content_length`` and ``duration_s`` are available.

    Returns:
        Estimated file size in bytes.  Returns ``0`` when the data is
        insufficient to produce a meaningful estimate.
    """
    if content_length is not None and content_length > 0:
        return content_length

    if duration_s <= 0:
        return 0

    if bitrate_kbps is not None and bitrate_kbps > 0:
        bits = bitrate_kbps * 1000 * duration_s
        return max(int(bits / 8), 0)

    if content_length is not None and duration_s > 0:
        inferred_bps = content_length / duration_s
        inferred_kbps = inferred_bps * 8 / 1000
        bits = inferred_kbps * 1000 * duration_s
        return max(int(bits / 8), 0)

    return 0


def _resolve_url(fmt: Dict[str, Any]) -> str:
    """Extract the stream URL from a format dict.

    YouTube may expose the URL directly as ``url`` or inside the
    ``signatureCipher`` parameter.  This helper extracts whichever is present.

    Args:
        fmt: Raw format dictionary.

    Returns:
        The stream URL, or an empty string if neither field is present.
    """
    if fmt.get("url"):
        return str(fmt["url"])

    cipher = fmt.get("signatureCipher") or fmt.get("signature_cipher")
    if cipher:
        url_match = re.search(r"(?:url|URL)=([^&]+)", cipher)
        if url_match:
            import urllib.parse
            return urllib.parse.unquote(url_match.group(1))

    return ""


def _detect_segmentation(fmt: Dict[str, Any]) -> Tuple[bool, bool]:
    """Determine whether a format uses DASH or HLS streaming.

    Args:
        fmt: Raw format dictionary.

    Returns:
        A 2-tuple ``(is_dash, is_hls)``.
    """
    proto = str(fmt.get("protocol", "")).lower()
    is_dash = "dash" in proto or "m3u8" in proto
    is_hls = "hls" in proto or "m3u8" in proto
    return is_dash, is_hls


# ---------------------------------------------------------------------------
# Core parsing functions
# ---------------------------------------------------------------------------


def parse_single_format(fmt: Dict[str, Any]) -> StreamFormat:
    """Convert a single raw format dictionary into a :class:`StreamFormat`.

    This function extracts all known fields from a YouTube format entry,
    applies safe type conversions, and returns a fully populated
    ``StreamFormat`` instance.

    Args:
        fmt: Raw format dictionary from ``streamingData.formats`` or
            ``streamingData.adaptiveFormats``.

    Returns:
        A :class:`StreamFormat` populated from the supplied dict.

    Raises:
        StreamDataError: If the format dict is missing mandatory fields or
            contains values that cannot be coerced to the expected types.
    """
    if not isinstance(fmt, dict):
        raise StreamDataError(f"Expected a dict for format, got {type(fmt).__name__}")

    try:
        itag_raw = fmt.get("itag")
        if itag_raw is None:
            raise StreamDataError("Format dict is missing 'itag'")
        itag = int(itag_raw)

        mime = str(fmt.get("mimeType", ""))
        container, vcodec, acodec = _parse_mime_type(mime)

        width = fmt.get("width")
        if width is not None:
            width = int(width)

        height = fmt.get("height")
        if height is not None:
            height = int(height)

        fps = fmt.get("fps")
        if fps is not None:
            fps = int(fps)

        tbr = fmt.get("averageBitrate") or fmt.get("tbr")
        if tbr is not None:
            tbr = float(tbr)

        abr = fmt.get("audioBitrate") or fmt.get("abr")
        if abr is not None:
            abr = float(abr)

        vbr = fmt.get("videoBitrate") or fmt.get("vbr")
        if vbr is not None:
            vbr = float(vbr)

        content_length = _parse_content_length(
            fmt.get("contentLength") or fmt.get("content_length")
        )

        approx_duration_ms = fmt.get("approxDurationMs") or fmt.get("approx_duration_ms")
        if approx_duration_ms is not None:
            approx_duration_ms = int(approx_duration_ms)

        quality_label = str(fmt.get("qualityLabel") or fmt.get("quality_label") or "")
        quality_ordinal = int(fmt.get("qualityOrdinal", 0) or 0)

        is_dash, is_hls = _detect_segmentation(fmt)

        url = _resolve_url(fmt)
        signature_cipher = fmt.get("signatureCipher") or fmt.get("signature_cipher") or None
        if signature_cipher is not None:
            signature_cipher = str(signature_cipher)

        if not container:
            container = _extract_container_from_itag(itag)

        if vcodec and acodec:
            acontainer = container
            vcontainer = container
        elif vcodec:
            acontainer = ""
            vcontainer = container
        elif acodec:
            acontainer = container
            vcontainer = ""
        else:
            itag_detail = ITAG_DETAILS.get(itag, {})
            if itag_detail:
                det_vcodec = itag_detail.get("vcodec", "")
                det_acodec = itag_detail.get("acodec", "")
                if not vcodec and det_vcodec and det_vcodec != "none":
                    vcodec = det_vcodec
                if not acodec and det_acodec and det_acodec != "none":
                    acodec = det_acodec
            acontainer = container
            vcontainer = container

        if not quality_label:
            quality_label = ITAG_QUALITY.get(itag, "")

        ext = str(fmt.get("ext", "") or container)

        return StreamFormat(
            itag=itag,
            ext=ext,
            vcodec=vcodec,
            acodec=acodec,
            width=width,
            height=height,
            fps=fps,
            tbr=tbr,
            abr=abr,
            vbr=vbr,
            acontainer=acontainer,
            vcontainer=vcontainer,
            mimeType=mime,
            protocol=str(fmt.get("protocol", "")),
            url=url,
            signature_cipher=signature_cipher,
            content_length=content_length,
            approx_duration_ms=approx_duration_ms,
            is_dash=is_dash,
            is_hls=is_hls,
            quality_label=quality_label,
            quality_ordinal=quality_ordinal,
        )
    except StreamDataError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise StreamDataError(
            f"Failed to parse format with itag={fmt.get('itag', 'unknown')}: {exc}"
        ) from exc


def _extract_container_from_itag(itag: int) -> str:
    """Look up the container format for a known itag.

    Args:
        itag: YouTube format identifier.

    Returns:
        Container format string, or empty string if itag is unknown.
    """
    detail = ITAG_DETAILS.get(itag, {})
    return detail.get("container", "")


def parse_streaming_data(streaming_data: Dict[str, Any]) -> List[StreamFormat]:
    """Parse all formats from a YouTube ``streamingData`` object.

    Iterates over both ``formats`` (progressive) and ``adaptiveFormats``
    (DASH/audio-only/video-only) lists within the ``streamingData`` dict,
    converting each entry into a :class:`StreamFormat`.

    Args:
        streaming_data: Raw ``streamingData`` dictionary extracted from
            ``ytInitialPlayerResponse``.  Expected to have the keys
            ``"formats"`` and/or ``"adaptiveFormats"``, each mapping to a
            list of format dicts.

    Returns:
        A list of :class:`StreamFormat` objects.  The list may be empty if
        ``streaming_data`` is ``None`` or contains no recognized formats.
    """
    if not streaming_data or not isinstance(streaming_data, dict):
        _logger.warning("parse_streaming_data called with empty or non-dict input")
        return []

    formats: List[StreamFormat] = []

    raw_formats = streaming_data.get("formats") or []
    raw_adaptive = streaming_data.get("adaptiveFormats") or []

    all_raw = list(raw_formats) + list(raw_adaptive)

    _logger.debug(
        "Parsing %d format entries (%d progressive, %d adaptive)",
        len(all_raw),
        len(raw_formats),
        len(raw_adaptive),
    )

    errors = 0
    for i, raw_fmt in enumerate(all_raw):
        try:
            sf = parse_single_format(raw_fmt)
            formats.append(sf)
        except StreamDataError as exc:
            errors += 1
            _logger.warning("Skipping format at index %d: %s", i, exc)

    if errors:
        _logger.warning(
            "Encountered %d parse errors out of %d format entries", errors, len(all_raw)
        )

    _logger.info("Parsed %d stream formats successfully", len(formats))
    return formats


# ---------------------------------------------------------------------------
# Format filtering and classification
# ---------------------------------------------------------------------------


def filter_formats(
    formats: Sequence[StreamFormat],
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    containers: Optional[Union[List[str], Tuple[str, ...], str]] = None,
    vcodecs: Optional[Union[List[str], Tuple[str, ...], str]] = None,
    acodecs: Optional[Union[List[str], Tuple[str, ...], str]] = None,
    protocols: Optional[Union[List[str], Tuple[str, ...], str]] = None,
) -> List[StreamFormat]:
    """Filter a list of formats by one or more criteria.

    All supplied criteria are combined with logical AND.  A criterion that
    is ``None`` or an empty collection is ignored.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        min_height: Minimum video height in pixels (inclusive).
        max_height: Maximum video height in pixels (inclusive).
        min_width: Minimum video width in pixels (inclusive).
        max_width: Maximum video width in pixels (inclusive).
        containers: Allowed container format strings (e.g. ``"mp4"``).
            Can be a single string or a list/tuple of strings.
        vcodecs: Allowed video codec strings.
            Can be a single string or a list/tuple of strings.
        acodecs: Allowed audio codec strings.
            Can be a single string or a list/tuple of strings.
        protocols: Allowed protocol strings.
            Can be a single string or a list/tuple of strings.

    Returns:
        A new list containing only formats that match all specified criteria.
    """
    if not formats:
        return []

    container_set = _to_set(containers)
    vcodec_set = _to_set(vcodecs)
    acodec_set = _to_set(acodecs)
    protocol_set = _to_set(protocols)

    result: List[StreamFormat] = []
    for fmt in formats:
        if min_height is not None and (fmt.height or 0) < min_height:
            continue
        if max_height is not None and (fmt.height or 0) > max_height:
            continue
        if min_width is not None and (fmt.width or 0) < min_width:
            continue
        if max_width is not None and (fmt.width or 0) > max_width:
            continue
        if container_set and (fmt.vcontainer or fmt.acontainer) not in container_set:
            continue
        if vcodec_set and fmt.vcodec not in vcodec_set:
            continue
        if acodec_set and fmt.acodec not in acodec_set:
            continue
        if protocol_set and fmt.protocol not in protocol_set:
            continue
        result.append(fmt)

    _logger.debug(
        "filter_formats returned %d/%d formats",
        len(result),
        len(formats),
    )
    return result


def _to_set(value: Optional[Union[List[str], Tuple[str, ...], str]]) -> set:
    """Coerce an optional string or collection to a frozenset.

    Args:
        value: ``None``, a single string, or a list/tuple of strings.

    Returns:
        A ``frozenset`` of lowercase strings, or an empty frozenset if
        ``value`` is ``None`` or empty.
    """
    if not value:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value.lower()})
    return frozenset(v.lower() for v in value)


# ---------------------------------------------------------------------------
# Format sorting
# ---------------------------------------------------------------------------


def quality_sort_key(fmt: StreamFormat) -> Tuple[int, int, int]:
    """Compute a sort key for :func:`sort_formats`.

    Returns a tuple ``(height_ordinal, bitrate_ordinal, itag)`` suitable for
    sorting formats from highest to lowest quality.

    Args:
        fmt: The format to compute the sort key for.

    Returns:
        A 3-tuple of integers.
    """
    height = fmt.height or 0
    bitrate = int(fmt.tbr or 0) * 10
    itag = fmt.itag
    return (height, bitrate, itag)


def sort_formats(
    formats: Sequence[StreamFormat],
    key: str = "quality",
) -> List[StreamFormat]:
    """Sort a list of formats by the specified criteria.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        key: Sort key.  Accepted values are:

            - ``"quality"``: Sort by video height, then bitrate (highest first).
            - ``"bitrate"``: Sort by total bitrate (highest first).
            - ``"itag"``: Sort by itag number (ascending).
            - ``"size"``: Sort by estimated file size (largest first).
            - ``"fps"``: Sort by frame rate (highest first).
            - ``"quality_label"``: Sort by quality label string.

    Returns:
        A new list of formats sorted according to *key* in descending order
        (best quality first), except ``"itag"`` which sorts ascending.
    """
    if not formats:
        return []

    key_funcs = {
        "quality": lambda f: (f.height or 0, int(f.tbr or 0), f.itag),
        "bitrate": lambda f: (int(f.tbr or 0), f.itag),
        "itag": lambda f: f.itag,
        "size": lambda f: (f.estimated_size or 0, f.itag),
        "fps": lambda f: (f.fps or 0, f.itag),
        "quality_label": lambda f: (f.quality_label, f.itag),
    }

    sort_func = key_funcs.get(key, key_funcs["quality"])

    reverse = key != "itag"
    sorted_fmts = sorted(formats, key=sort_func, reverse=reverse)

    _logger.debug(
        "sort_formats(key=%s) sorted %d formats", key, len(sorted_fmts)
    )
    return sorted_fmts


# ---------------------------------------------------------------------------
# Format selection helpers
# ---------------------------------------------------------------------------


def get_best_format(
    formats: Sequence[StreamFormat],
    prefer_video: bool = True,
    prefer_audio: bool = True,
) -> Optional[StreamFormat]:
    """Select the highest-quality format from a list.

    Selection logic:

    1. If *prefer_video* and *prefer_audio* are both ``True``, prefer
       combined formats, then video-only, then audio-only.
    2. If only *prefer_video* is ``True``, prefer video-only or combined.
    3. If only *prefer_audio* is ``True``, prefer audio-only or combined.
    4. If neither preference is set, return the highest-ranked format by
       :func:`quality_sort_key`.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        prefer_video: When ``True``, video-carrying formats are preferred.
        prefer_audio: When ``True``, audio-carrying formats are preferred.

    Returns:
        The best :class:`StreamFormat`, or ``None`` if *formats* is empty.
    """
    if not formats:
        return None

    combined = [f for f in formats if f.is_combined]
    video_only = [f for f in formats if f.is_video_only]
    audio_only = [f for f in formats if f.is_audio_only]

    if prefer_video and prefer_audio:
        if combined:
            return sorted(combined, key=quality_sort_key, reverse=True)[0]
        candidates = video_only + audio_only
        if candidates:
            return sorted(candidates, key=quality_sort_key, reverse=True)[0]
    elif prefer_video:
        candidates = combined + video_only
        if candidates:
            return sorted(candidates, key=quality_sort_key, reverse=True)[0]
    elif prefer_audio:
        candidates = combined + audio_only
        if candidates:
            return sorted(candidates, key=quality_sort_key, reverse=True)[0]

    return sorted(formats, key=quality_sort_key, reverse=True)[0]


def get_format_by_itag(
    formats: Sequence[StreamFormat],
    itag: int,
) -> Optional[StreamFormat]:
    """Find a format by its YouTube itag number.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        itag: YouTube format identifier to search for.

    Returns:
        The first matching :class:`StreamFormat`, or ``None`` if not found.
    """
    for fmt in formats:
        if fmt.itag == itag:
            return fmt
    _logger.debug("No format found with itag=%d", itag)
    return None


def get_audio_only_formats(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Filter a list to only audio-only formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A list of :class:`StreamFormat` objects that carry audio but no video.
    """
    result = [f for f in formats if f.is_audio_only]
    _logger.debug("get_audio_only_formats returned %d/%d formats", len(result), len(formats))
    return result


def get_video_only_formats(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Filter a list to only video-only formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A list of :class:`StreamFormat` objects that carry video but no audio.
    """
    result = [f for f in formats if f.is_video_only]
    _logger.debug("get_video_only_formats returned %d/%d formats", len(result), len(formats))
    return result


def get_combined_formats(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Filter a list to only combined (audio + video) formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A list of :class:`StreamFormat` objects that carry both audio and video.
    """
    result = [f for f in formats if f.is_combined]
    _logger.debug("get_combined_formats returned %d/%d formats", len(result), len(formats))
    return result


# ---------------------------------------------------------------------------
# Format summary and introspection utilities
# ---------------------------------------------------------------------------


def get_format_summary(formats: Sequence[StreamFormat]) -> Dict[str, Any]:
    """Produce a summary of a format list.

    Useful for diagnostic logging and debugging.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A dictionary with aggregate statistics.
    """
    if not formats:
        return {
            "count": 0,
            "combined": 0,
            "video_only": 0,
            "audio_only": 0,
            "dash": 0,
            "hls": 0,
            "progressive": 0,
            "highest_height": None,
            "highest_bitrate": None,
            "total_size_bytes": 0,
        }

    combined_count = sum(1 for f in formats if f.is_combined)
    video_only_count = sum(1 for f in formats if f.is_video_only)
    audio_only_count = sum(1 for f in formats if f.is_audio_only)
    dash_count = sum(1 for f in formats if f.is_dash)
    hls_count = sum(1 for f in formats if f.is_hls)
    progressive_count = sum(
        1 for f in formats if not f.is_dash and not f.is_hls
    )

    heights = [f.height or 0 for f in formats if f.height]
    bitrates = [f.tbr or 0 for f in formats if f.tbr]
    sizes = [f.estimated_size or 0 for f in formats if f.estimated_size]

    return {
        "count": len(formats),
        "combined": combined_count,
        "video_only": video_only_count,
        "audio_only": audio_only_count,
        "dash": dash_count,
        "hls": hls_count,
        "progressive": progressive_count,
        "highest_height": max(heights) if heights else None,
        "highest_bitrate": max(bitrates) if bitrates else None,
        "total_size_bytes": sum(sizes),
        "available_qualities": sorted(
            {f.quality_label for f in formats if f.quality_label}
        ),
    }


def get_quality_ordinal(quality_label: str) -> int:
    """Map a quality label string to its pixel height.

    Args:
        quality_label: A string such as ``"1080p"`` or ``"480p"``.

    Returns:
        The pixel height as an integer, or ``0`` if the label is unknown.
    """
    return QUALITY_HEIGHT_MAP.get(quality_label, 0)


def group_formats_by_type(
    formats: Sequence[StreamFormat],
) -> Dict[str, List[StreamFormat]]:
    """Group formats by their audio/video type category.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A dictionary with keys ``"combined"``, ``"video_only"``,
        ``"audio_only"``, and ``"unknown"`` mapping to lists of formats.
    """
    groups: Dict[str, List[StreamFormat]] = {
        "combined": [],
        "video_only": [],
        "audio_only": [],
        "unknown": [],
    }
    for fmt in formats:
        if fmt.is_combined:
            groups["combined"].append(fmt)
        elif fmt.is_video_only:
            groups["video_only"].append(fmt)
        elif fmt.is_audio_only:
            groups["audio_only"].append(fmt)
        else:
            groups["unknown"].append(fmt)
    return groups


def group_formats_by_protocol(
    formats: Sequence[StreamFormat],
) -> Dict[str, List[StreamFormat]]:
    """Group formats by their streaming protocol.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A dictionary mapping protocol strings to lists of formats.
    """
    groups: Dict[str, List[StreamFormat]] = {}
    for fmt in formats:
        proto = fmt.protocol or "unknown"
        groups.setdefault(proto, []).append(fmt)
    return groups


def group_formats_by_container(
    formats: Sequence[StreamFormat],
) -> Dict[str, List[StreamFormat]]:
    """Group formats by their container format.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A dictionary mapping container strings to lists of formats.
    """
    groups: Dict[str, List[StreamFormat]] = {}
    for fmt in formats:
        container = fmt.vcontainer or fmt.acontainer or "unknown"
        groups.setdefault(container, []).append(fmt)
    return groups


def select_formats_by_quality(
    formats: Sequence[StreamFormat],
    quality: str = "best",
) -> List[StreamFormat]:
    """Select formats matching a quality requirement string.

    Supported *quality* values:

    - ``"best"``: All formats, sorted by quality (highest first).
    - ``"worst"``: All formats, sorted by quality (lowest first).
    - A pixel resolution such as ``"1080p"``, ``"720p"``, ``"480p"``, etc.
      Formats with a height equal to or less than the target are returned,
      with the highest quality match first.
    - An itag number string such as ``"18"``: returns a single-element list
      if the itag is found, otherwise an empty list.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        quality: Quality selection string.

    Returns:
        A list of matching :class:`StreamFormat` objects.
    """
    if not formats:
        return []

    if quality == "best":
        return sort_formats(formats, key="quality")

    if quality == "worst":
        return sort_formats(formats, key="quality")[::-1]

    if quality.isdigit():
        itag = int(quality)
        fmt = get_format_by_itag(formats, itag)
        return [fmt] if fmt is not None else []

    match = re.match(r"(\d+)p", quality)
    if match:
        target_height = int(match.group(1))
        candidates = [
            f for f in formats if (f.height or 0) <= target_height and (f.height or 0) > 0
        ]
        return sort_formats(candidates, key="quality")

    _logger.warning("Unrecognized quality string: %r", quality)
    return sort_formats(formats, key="quality")


def list_formats(
    formats: Sequence[StreamFormat],
    *,
    show_details: bool = False,
) -> str:
    """Produce a human-readable summary of available formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        show_details: When ``True``, include additional per-format details
            such as bitrate and estimated size.

    Returns:
        A formatted string suitable for printing to the console.
    """
    if not formats:
        return "No formats available."

    lines = [f"Available formats ({len(formats)}):"]

    sorted_fmts = sort_formats(formats, key="quality")
    for i, fmt in enumerate(sorted_fmts, 1):
        parts = [f"  [{i}] itag={fmt.itag}"]
        if fmt.quality_label:
            parts.append(f"{fmt.quality_label}")
        if fmt.height:
            parts.append(f"{fmt.width}x{fmt.height}")
        if fmt.fps:
            parts.append(f"{fmt.fps}fps")
        if fmt.vcodec != "none":
            parts.append(f"vcodec={fmt.vcodec}")
        if fmt.acodec != "none":
            parts.append(f"acodec={fmt.acodec}")
        if fmt.protocol:
            parts.append(f"protocol={fmt.protocol}")
        if fmt.ext:
            parts.append(f"ext={fmt.ext}")
        if show_details:
            if fmt.tbr is not None:
                parts.append(f"tbr={fmt.tbr:.0f}kbps")
            if fmt.abr is not None:
                parts.append(f"abr={fmt.abr:.0f}kbps")
            if fmt.estimated_size:
                size_mb = fmt.estimated_size / (1024 * 1024)
                parts.append(f"~{size_mb:.1f}MB")
        type_label = "combined" if fmt.is_combined else (
            "video-only" if fmt.is_video_only else (
                "audio-only" if fmt.is_audio_only else "unknown"
            )
        )
        parts.append(f"({type_label})")
        lines.append(" ".join(parts))

    return "\n".join(lines)


def is_format_supported(fmt: StreamFormat) -> bool:
    """Check whether a format uses a supported codec and container combination.

    Args:
        fmt: The :class:`StreamFormat` to evaluate.

    Returns:
        ``True`` when the format uses a known video or audio codec and a
        recognized container format.
    """
    if fmt.vcodec not in (None, "", "none") and fmt.vcodec not in VIDEO_CODECS:
        return False
    if fmt.acodec not in (None, "", "none") and fmt.acodec not in AUDIO_CODECS:
        return False
    valid_containers = set(CONTAINERS)
    if fmt.vcontainer and fmt.vcontainer not in valid_containers:
        return False
    if fmt.acontainer and fmt.acontainer not in valid_containers:
        return False
    return True


def filter_supported_formats(
    formats: Sequence[StreamFormat],
) -> List[StreamFormat]:
    """Return only formats that use supported codec and container combinations.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A filtered list of :class:`StreamFormat` objects that pass
        :func:`is_format_supported`.
    """
    result = [f for f in formats if is_format_supported(f)]
    _logger.debug(
        "filter_supported_formats kept %d/%d formats",
        len(result),
        len(formats),
    )
    return result


def get_dash_formats(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Filter to only DASH or HLS segmented formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A list of formats that use DASH or HLS streaming.
    """
    result = [f for f in formats if f.is_dash or f.is_hls]
    return result


def get_progressive_formats(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Filter to only progressive (non-segmented) formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A list of formats that do not use DASH or HLS streaming.
    """
    result = [f for f in formats if not f.is_dash and not f.is_hls]
    return result


def merge_formats(
    video_format: StreamFormat,
    audio_format: StreamFormat,
) -> StreamFormat:
    """Merge a video-only and audio-only format into a combined format object.

    The resulting :class:`StreamFormat` carries both audio and video codec
    information from the source formats and uses the URL from the video
    format.  This is a data-level merge; actual media merging requires
    :mod:`ytdownloader.merger`.

    Args:
        video_format: A video-only :class:`StreamFormat`.
        audio_format: An audio-only :class:`StreamFormat`.

    Returns:
        A new :class:`StreamFormat` representing the combined stream.

    Raises:
        StreamDataError: If either format is not of the expected type.
    """
    if not video_format.is_video_only:
        raise StreamDataError(
            f"Expected video-only format (itag={video_format.itag}), "
            f"got vcodec={video_format.vcodec}, acodec={video_format.acodec}"
        )
    if not audio_format.is_audio_only:
        raise StreamDataError(
            f"Expected audio-only format (itag={audio_format.itag}), "
            f"got vcodec={audio_format.vcodec}, acodec={audio_format.acodec}"
        )

    merged = StreamFormat(
        itag=video_format.itag,
        ext=video_format.ext or audio_format.ext,
        vcodec=video_format.vcodec,
        acodec=audio_format.acodec,
        width=video_format.width,
        height=video_format.height,
        fps=video_format.fps,
        tbr=(video_format.tbr or 0) + (audio_format.tbr or 0),
        abr=audio_format.abr,
        vbr=video_format.vbr,
        acontainer=audio_format.acontainer or audio_format.ext,
        vcontainer=video_format.vcontainer or video_format.ext,
        mimeType=video_format.mimeType or audio_format.mimeType,
        protocol=video_format.protocol or audio_format.protocol,
        url=video_format.url,
        signature_cipher=video_format.signature_cipher,
        content_length=_estimate_size(
            video_format.content_length,
            video_format.approx_duration_s or 0,
            video_format.tbr,
        ),
        approx_duration_ms=video_format.approx_duration_ms,
        is_dash=video_format.is_dash,
        is_hls=video_format.is_hls,
        quality_label=video_format.quality_label,
        quality_ordinal=max(video_format.quality_ordinal, audio_format.quality_ordinal),
    )
    _logger.debug(
        "Merged itag=%d (video) + itag=%d (audio) into combined format",
        video_format.itag,
        audio_format.itag,
    )
    return merged


def find_mergeable_pair(
    formats: Sequence[StreamFormat],
    quality: str = "best",
) -> Optional[Tuple[StreamFormat, StreamFormat]]:
    """Find the best video-only / audio-only pair for merging.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        quality: Quality selection string passed to
            :func:`select_formats_by_quality` for each stream type.

    Returns:
        A ``(video_format, audio_format)`` tuple, or ``None`` if no
        mergeable pair is available.
    """
    video_only = get_video_only_formats(formats)
    audio_only = get_audio_only_formats(formats)

    if not video_only or not audio_only:
        return None

    video_candidates = select_formats_by_quality(video_only, quality=quality)
    audio_candidates = select_formats_by_quality(audio_only, quality=quality)

    if not video_candidates or not audio_candidates:
        return None

    return video_candidates[0], audio_candidates[0]


def validate_format(fmt: StreamFormat) -> List[str]:
    """Validate a :class:`StreamFormat` and return a list of issues.

    This function does not raise exceptions.  Callers can use the returned
    list to determine whether the format is usable.

    Args:
        fmt: The :class:`StreamFormat` to validate.

    Returns:
        A list of human-readable issue strings.  An empty list means the
        format passed all checks.
    """
    issues: List[str] = []

    if fmt.itag <= 0:
        issues.append(f"Invalid itag: {fmt.itag}")

    if not fmt.url and not fmt.signature_cipher:
        issues.append("No URL or signatureCipher present")

    if fmt.vcodec not in (None, "", "none") and fmt.vcodec not in VIDEO_CODECS:
        issues.append(f"Unknown video codec: {fmt.vcodec}")

    if fmt.acodec not in (None, "", "none") and fmt.acodec not in AUDIO_CODECS:
        issues.append(f"Unknown audio codec: {fmt.acodec}")

    if fmt.is_video_only and fmt.is_audio_only:
        issues.append("Format claims both video-only and audio-only")

    if fmt.height is not None and fmt.height <= 0:
        issues.append(f"Invalid video height: {fmt.height}")

    if fmt.width is not None and fmt.width <= 0:
        issues.append(f"Invalid video width: {fmt.width}")

    if fmt.content_length is not None and fmt.content_length < 0:
        issues.append(f"Negative contentLength: {fmt.content_length}")

    return issues


# ---------------------------------------------------------------------------
# Additional introspection utilities
# ---------------------------------------------------------------------------


def get_available_heights(formats: Sequence[StreamFormat]) -> List[int]:
    """Return a sorted list of distinct video heights present in *formats*.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A sorted list of unique height values.
    """
    heights = {f.height for f in formats if f.height}
    return sorted(heights)


def get_available_itags(formats: Sequence[StreamFormat]) -> List[int]:
    """Return a sorted list of distinct itag values in *formats*.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A sorted list of unique itag numbers.
    """
    return sorted({f.itag for f in formats})


def get_available_extensions(formats: Sequence[StreamFormat]) -> List[str]:
    """Return a sorted list of distinct file extensions in *formats*.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A sorted list of unique extension strings.
    """
    exts = {f.ext for f in formats if f.ext}
    return sorted(exts)


def get_available_quality_labels(
    formats: Sequence[StreamFormat],
) -> List[str]:
    """Return a sorted list of distinct quality labels in *formats*.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A sorted list of unique quality label strings.
    """
    labels = {f.quality_label for f in formats if f.quality_label}
    return sorted(labels)


def has_audio(formats: Sequence[StreamFormat]) -> bool:
    """Check whether any format in the list carries audio.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        ``True`` if at least one format has an audio codec.
    """
    return any(f.acodec not in (None, "", "none") for f in formats)


def has_video(formats: Sequence[StreamFormat]) -> bool:
    """Check whether any format in the list carries video.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        ``True`` if at least one format has a video codec.
    """
    return any(f.vcodec not in (None, "", "none") for f in formats)


def has_dash(formats: Sequence[StreamFormat]) -> bool:
    """Check whether any format uses DASH streaming.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        ``True`` if at least one format is marked as DASH.
    """
    return any(f.is_dash for f in formats)


def has_hls(formats: Sequence[StreamFormat]) -> bool:
    """Check whether any format uses HLS streaming.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        ``True`` if at least one format is marked as HLS.
    """
    return any(f.is_hls for f in formats)


def count_formats(formats: Sequence[StreamFormat]) -> Dict[str, int]:
    """Count formats by type category.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A dictionary with keys ``"total"``, ``"combined"``,
        ``"video_only"``, ``"audio_only"``, ``"dash"``, ``"hls"``, and
        ``"progressive"``.
    """
    return {
        "total": len(formats),
        "combined": len(get_combined_formats(formats)),
        "video_only": len(get_video_only_formats(formats)),
        "audio_only": len(get_audio_only_formats(formats)),
        "dash": len(get_dash_formats(formats)),
        "hls": len(get_hls_formats(formats)),
        "progressive": len(get_progressive_formats(formats)),
    }


def get_hls_formats(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Filter to only HLS formats.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A list of formats that use HLS streaming.
    """
    return [f for f in formats if f.is_hls]


def get_formats_by_container(
    formats: Sequence[StreamFormat],
    container: str,
) -> List[StreamFormat]:
    """Filter formats by a specific container format.

    The filter matches either the video or audio container.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        container: Container format string (e.g. ``"mp4"``, ``"webm"``).

    Returns:
        A list of formats that use the specified container.
    """
    container_lower = container.lower()
    return [
        f
        for f in formats
        if f.vcontainer.lower() == container_lower
        or f.acontainer.lower() == container_lower
    ]


def get_formats_by_codec(
    formats: Sequence[StreamFormat],
    vcodec: Optional[str] = None,
    acodec: Optional[str] = None,
) -> List[StreamFormat]:
    """Filter formats by video and/or audio codec.

    Args:
        formats: Input list of :class:`StreamFormat` objects.
        vcodec: Required video codec, or ``None`` to ignore.
        acodec: Required audio codec, or ``None`` to ignore.

    Returns:
        A list of formats that match the specified codecs.
    """
    result = []
    for f in formats:
        if vcodec is not None and f.vcodec.lower() != vcodec.lower():
            continue
        if acodec is not None and f.acodec.lower() != acodec.lower():
            continue
        result.append(f)
    return result
