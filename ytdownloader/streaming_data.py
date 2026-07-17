"""
Streaming data parser for YouTube ytInitialPlayerResponse.

This module provides a comprehensive parser for the streamingData section of a
YouTube player response, exposing the StreamFormat dataclass and a suite of
utility functions for filtering, sorting, and selecting the best stream
format for a given download context.

Typical usage::

    from ytdownloader.streaming_data import (
        parse_streaming_data,
        filter_formats,
        sort_formats,
        get_best_format,
    )

    formats = parse_streaming_data(player_response["streamingData"])
    best = get_best_format(formats, prefer_video=True, prefer_audio=True)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from ytdownloader.constants import (
    AUDIO_ONLY_ITAGS,
    AUDIO_CODECS,
    CONTAINERS,
    EXT_MIME_MAP,
    ITAG_DETAILS,
    ITAG_QUALITY,
    MIME_EXT_MAP,
    PROGRESSIVE_ITAGS,
    PROTOCOLS,
    QUALITY_HEIGHT_MAP,
    VIDEO_CODECS,
    VIDEO_ONLY_ITAGS,
)
from ytdownloader.exceptions import FormatSelectionError, StreamDataError
from ytdownloader.logger import get_logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# MIME type lookup tables
# ---------------------------------------------------------------------------

_MIME_TYPE_MAP: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {
    "video/mp4": ("mp4", "avc1", "aac"),
    "video/webm": ("webm", "vp9", None),
    "video/x-flv": ("flv", "h263", "mp3"),
    "video/3gpp": ("3gp", "mp4v", "aac"),
    "audio/mp4": ("m4a", None, "aac"),
    "audio/webm": ("weba", None, "opus"),
    "audio/mpeg": ("mp3", None, "mp3"),
    "audio/aac": ("aac", None, "aac"),
    "application/x-mpegurl": ("m3u8", None, None),
}

_EXT_TO_CONTAINER: Dict[str, str] = {}
for _mime_key, (_cont, _, _) in _MIME_TYPE_MAP.items():
    _exts = MIME_EXT_MAP.get(_mime_key, [])
    for _ext in _exts:
        _EXT_TO_CONTAINER[_ext] = _cont
del _mime_key, _cont, _, _exts, _ext

_RE_CODECS_IN_MIME = re.compile(r"codecs\s*=\s*\"([^\"]+)\"")

_DASH_PROTOCOL_SET: Set[str] = {"dash"}
_HLS_PROTOCOL_SET: Set[str] = {"hls", "m3u8"}
_PROGRESSIVE_PROTOCOL_SET: Set[str] = {"http", "https"}

_QUALITY_ORDINAL_MAP: Dict[str, int] = {
    label: idx
    for idx, label in enumerate(reversed(QUALITY_HEIGHT_MAP.keys()), 1)
}

_CONTAINER_SET: Set[str] = {c.lower() for c in CONTAINERS}
_VIDEO_CODEC_SET: Set[str] = {c.lower() for c in VIDEO_CODECS}
_AUDIO_CODEC_SET: Set[str] = {c.lower() for c in AUDIO_CODECS}

# itags known to be audio-only
_AUDIO_ONLY_ITAG_SET: Set[int] = {139, 140, 141, 249, 250, 251, 302, 303, 308}
# itags known to be video-only DASH
_VIDEO_ONLY_ITAG_SET: Set[int] = {
    242, 243, 244, 245, 246, 247, 248, 264, 266,
    271, 272, 278, 313, 315, 330, 331, 332, 333,
    334, 335, 336, 337, 338, 400, 401, 402, 403,
    404, 405, 406, 431, 432, 433, 434, 435, 436,
    482, 483, 484, 485, 486, 487,
}
# itags known to be combined progressive
_COMBINED_ITAG_SET: Set[int] = set(PROGRESSIVE_ITAGS)


# ---------------------------------------------------------------------------
# StreamFormat dataclass
# ---------------------------------------------------------------------------


@dataclass
class StreamFormat:
    """Representation of a single YouTube stream format.

    Attributes:
        itag: The YouTube format identifier (integer).  Used to distinguish
            between different encoding/quality combinations.
        ext: File extension derived from the MIME type or container
            (e.g. ``"mp4"``, ``"webm"``, ``"m4a"``).
        vcodec: Video codec name, or ``"none"`` when the stream has no
            video track, or ``None`` if unknown.
        acodec: Audio codec name, or ``"none"`` when the stream has no
            audio track, or ``None`` if unknown.
        width: Frame width in pixels, or ``None`` for audio-only streams.
        height: Frame height in pixels, or ``None`` for audio-only streams.
        fps: Frames per second, or ``None`` when not reported by YouTube.
        tbr: Total bitrate in kbps (combined audio + video), or ``None``.
        abr: Audio bitrate in kbps, or ``None`` for video-only streams.
        vbr: Video bitrate in kbps, or ``None`` for audio-only streams.
        acontainer: Audio container format string, or ``None``.
        vcontainer: Video container format string, or ``None``.
        mimeType: The raw MIME type string as provided by YouTube (e.g.
            ``"video/webm; codecs=\\"vp9\\""``).
        protocol: Transport protocol string (e.g. ``"https"``, ``"dash"``,
            ``"hls"``).
        url: Direct download URL, or ``None`` when the URL is not yet
            resolved (e.g. it is encrypted via ``signatureCipher``).
        signature_cipher: Raw ``signatureCipher`` query string when the
            format URL requires deciphering, or ``None``.
        content_length: Content length in bytes as an integer, or ``None``
            when the server does not provide the ``Content-Length`` header
            in the player response.
        approx_duration_ms: Approximate duration of the stream in
            milliseconds, or ``None`` when not provided.
        is_dash: ``True`` when the format is a DASH (Dynamic Adaptive
            Streaming over HTTP) adaptive stream.
        is_hls: ``True`` when the format uses HTTP Live Streaming (HLS).
        quality_label: Human-readable quality label string as provided by
            YouTube (e.g. ``"720p"``), or ``None`` for streams that do not
            carry one.
        quality_ordinal: Integer sort key derived from the quality label.
            Higher values represent higher quality.  ``0`` when the quality
            label is absent or unrecognised.
    """

    itag: int
    ext: Optional[str] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None
    tbr: Optional[float] = None
    abr: Optional[float] = None
    vbr: Optional[float] = None
    acontainer: Optional[str] = None
    vcontainer: Optional[str] = None
    mimeType: Optional[str] = None
    protocol: Optional[str] = None
    url: Optional[str] = None
    signature_cipher: Optional[str] = None
    content_length: Optional[int] = None
    approx_duration_ms: Optional[int] = None
    is_dash: bool = False
    is_hls: bool = False
    quality_label: Optional[str] = None
    quality_ordinal: int = 0

    @property
    def has_video(self) -> bool:
        """``True`` if this format contains a video stream."""
        vc = (self.vcodec or "").lower()
        return bool(vc) and vc != "none"

    @property
    def has_audio(self) -> bool:
        """``True`` if this format contains an audio stream."""
        ac = (self.acodec or "").lower()
        return bool(ac) and ac != "none"

    @property
    def is_combined(self) -> bool:
        """``True`` when the format carries both audio and video."""
        return self.has_video and self.has_audio

    @property
    def is_video_only(self) -> bool:
        """``True`` when the format carries video but no audio."""
        return self.has_video and not self.has_audio

    @property
    def is_audio_only(self) -> bool:
        """``True`` when the format carries audio but no video."""
        return self.has_audio and not self.has_video

    @property
    def estimated_size_bytes(self) -> Optional[int]:
        """Approximate file size in bytes.

        Returns ``content_length`` if available, otherwise the value
        returned by :func:`_estimate_size`, or ``None`` when no estimate
        is possible.
        """
        if self.content_length is not None and self.content_length >= 0:
            return self.content_length
        if (
            self.approx_duration_ms is not None
            and self.tbr is not None
            and self.approx_duration_ms > 0
        ):
            return _estimate_size(None, self.approx_duration_ms, self.tbr)
        return None

    @property
    def size_mb(self) -> Optional[float]:
        """Estimated file size in megabytes, or ``None``."""
        size = self.estimated_size_bytes
        if size is None:
            return None
        return round(size / (1 << 20), 2)

    @property
    def duration_seconds(self) -> Optional[float]:
        """Duration in seconds, or ``None``."""
        if self.approx_duration_ms is None:
            return None
        return round(self.approx_duration_ms / 1000.0, 2)

    @property
    def sortable_height(self) -> int:
        """Height as an integer suitable for sorting (0 when absent)."""
        return self.height or 0

    @property
    def display_codecs(self) -> str:
        """Human-readable codec summary string."""
        vc = self.vcodec or "none"
        ac = self.acodec or "none"
        return f"v:{vc}/a:{ac}"

    def __repr__(self) -> str:
        parts = [f"StreamFormat(itag={self.itag}"]
        if self.quality_label:
            parts.append(f", quality={self.quality_label!r}")
        if self.ext:
            parts.append(f", ext={self.ext!r}")
        if self.has_video:
            parts.append(f", video={self.vcodec}@{self.height or '?'}p")
        if self.has_audio:
            parts.append(f", audio={self.acodec}@{self.abr or '?'}kbps")
        if self.protocol:
            parts.append(f", protocol={self.protocol!r}")
        size = self.estimated_size_bytes
        if size is not None:
            parts.append(f", size={_format_size_local(size)}")
        parts.append(")")
        return "".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this format to a plain dictionary.

        Returns:
            A dict containing all public fields of this instance.
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
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "is_combined": self.is_combined,
            "is_video_only": self.is_video_only,
            "is_audio_only": self.is_audio_only,
            "estimated_size_bytes": self.estimated_size_bytes,
            "duration_seconds": self.duration_seconds,
        }

    def matches_itag(self, itag: int) -> bool:
        """Return ``True`` if this format's itag matches the given value.

        Args:
            itag: Itag number to compare.

        Returns:
            ``True`` when ``self.itag == itag``.
        """
        return self.itag == itag

    def matches_codecs(
        self,
        vcodec: Optional[str] = None,
        acodec: Optional[str] = None,
    ) -> bool:
        """Return ``True`` if this format matches the requested codecs.

        Args:
            vcodec: Desired video codec string, or ``None`` to ignore.
            acodec: Desired audio codec string, or ``None`` to ignore.

        Returns:
            ``True`` when all supplied codec constraints are satisfied.
        """
        if vcodec is not None:
            if (self.vcodec or "none").lower() != vcodec.lower():
                return False
        if acodec is not None:
            if (self.acodec or "none").lower() != acodec.lower():
                return False
        return True

    def matches_container(self, container: str) -> bool:
        """Return ``True`` if this format uses the given container.

        Args:
            container: Container format string (e.g. ``"mp4"``).

        Returns:
            ``True`` when the container matches.
        """
        target = container.lower()
        return target in {
            (self.vcontainer or "").lower(),
            (self.acontainer or "").lower(),
            (self.ext or "").lower(),
        }

    def matches_protocol(self, protocol: str) -> bool:
        """Return ``True`` if this format uses the given protocol.

        Args:
            protocol: Protocol string (e.g. ``"https"``).

        Returns:
            ``True`` when the protocol matches.
        """
        return (self.protocol or "").lower() == protocol.lower()

    def matches_height(self, height: int) -> bool:
        """Return ``True`` if this format has the given height.

        Audio-only formats (``height is None``) always return ``False``.

        Args:
            height: Height in pixels.

        Returns:
            ``True`` when ``self.height == height``.
        """
        return self.height == height

    def is_within_resolution(
        self,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        min_width: Optional[int] = None,
        max_width: Optional[int] = None,
    ) -> bool:
        """Return ``True`` if the format's resolution is within bounds.

        Args:
            min_height: Minimum height (inclusive).
            max_height: Maximum height (inclusive).
            min_width: Minimum width (inclusive).
            max_width: Maximum width (inclusive).

        Returns:
            ``True`` when all supplied constraints are satisfied.
        """
        h = self.height or 0
        w = self.width or 0
        if min_height is not None and h < min_height:
            return False
        if max_height is not None and h > max_height:
            return False
        if min_width is not None and w < min_width:
            return False
        if max_width is not None and w > max_width:
            return False
        return True

    def is_downloadable(self) -> bool:
        """Return ``True`` if this format has a usable download URL.

        A format is considered downloadable when either ``url`` is set or
        ``signature_cipher`` is present (the latter requires deciphering
        before download is possible).

        Returns:
            ``True`` when ``url`` or ``signature_cipher`` is non-``None``.
        """
        return self.url is not None or self.signature_cipher is not None

    def bitrate_kbps(self) -> Optional[float]:
        """Return the effective bitrate in kbps.

        Returns ``tbr`` when available, otherwise the sum of ``vbr`` and
        ``abr``, or ``None`` when no bitrate information is present.

        Returns:
            Bitrate in kbps, or ``None``.
        """
        if self.tbr is not None:
            return self.tbr
        v = self.vbr or 0.0
        a = self.abr or 0.0
        if v + a > 0:
            return v + a
        return None

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, StreamFormat):
            return NotImplemented
        return (self.quality_ordinal, self.itag) < (
            other.quality_ordinal,
            other.itag,
        )

    def __le__(self, other: object) -> bool:
        if not isinstance(other, StreamFormat):
            return NotImplemented
        return (self.quality_ordinal, self.itag) <= (
            other.quality_ordinal,
            other.itag,
        )

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, StreamFormat):
            return NotImplemented
        return (self.quality_ordinal, self.itag) > (
            other.quality_ordinal,
            other.itag,
        )

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, StreamFormat):
            return NotImplemented
        return (self.quality_ordinal, self.itag) >= (
            other.quality_ordinal,
            other.itag,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StreamFormat):
            return NotImplemented
        return self.itag == other.itag

    def __hash__(self) -> int:
        return hash(self.itag)


# ---------------------------------------------------------------------------
# Local formatting helpers
# ---------------------------------------------------------------------------


def _format_size_local(num_bytes: Optional[int]) -> str:
    """Format a byte count as a human-readable string.

    Args:
        num_bytes: Raw byte count, or ``None``.

    Returns:
        A string such as ``"1.5 MB"`` or ``"?"``.
    """
    if num_bytes is None:
        return "?"
    abs_bytes = abs(num_bytes)
    if abs_bytes >= 1 << 30:
        return f"{num_bytes / (1 << 30):.2f} GB"
    if abs_bytes >= 1 << 20:
        return f"{num_bytes / (1 << 20):.2f} MB"
    if abs_bytes >= 1 << 10:
        return f"{num_bytes / (1 << 10):.2f} KB"
    return f"{num_bytes} B"


# ---------------------------------------------------------------------------
# MIME type parsing
# ---------------------------------------------------------------------------


def _parse_mime_type(mime: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """Parse a YouTube MIME type string into (container, vcodec, acodec).

    Parses strings such as ``'video/webm; codecs="vp9"'`` into a
    ``(container, vcodec, acodec)`` triple.

    Args:
        mime: Raw MIME type string from the format dict, or ``None``.

    Returns:
        A three-element tuple of ``(container, vcodec, acodec)``.  Any
        component that cannot be determined is returned as ``None``.
    """
    if not mime:
        return (None, None, None)

    mime_lower = mime.lower().strip()
    base_type = mime_lower.split(";")[0].strip()
    container, default_vcodec, default_acodec = _MIME_TYPE_MAP.get(
        base_type, (None, None, None)
    )

    codecs_part = ""
    m = _RE_CODECS_IN_MIME.search(mime_lower)
    if m:
        codecs_part = m.group(1)

    vcodec: Optional[str] = default_vcodec
    acodec: Optional[str] = default_acodec

    if codecs_part:
        codecs = [c.strip() for c in codecs_part.split(",")]
        for codec in codecs:
            codec_lower = codec.lower()
            if codec_lower == "none":
                if base_type.startswith("video/"):
                    vcodec = "none"
                elif base_type.startswith("audio/"):
                    acodec = "none"
                continue
            if codec_lower in _VIDEO_CODEC_SET:
                vcodec = codec_lower
            elif codec_lower in _AUDIO_CODEC_SET:
                acodec = codec_lower

    return (container, vcodec, acodec)


# ---------------------------------------------------------------------------
# Content-length and size estimation
# ---------------------------------------------------------------------------


def _parse_content_length(value: Any) -> Optional[int]:
    """Safely parse a ``contentLength`` value to an integer.

    YouTube may supply the content length as a string of digits rather than
    an integer, and it may also be missing or malformed.

    Args:
        value: The raw ``contentLength`` value from the format dict.

    Returns:
        The content length as an integer, or ``None`` when the value cannot
        be parsed.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and not (value != value) else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = int(stripped)
            return parsed if parsed >= 0 else None
        except (ValueError, OverflowError):
            return None
    return None


def _estimate_size(
    content_length: Optional[int],
    approx_duration_ms: Optional[int],
    tbr: Optional[float] = None,
) -> Optional[int]:
    """Estimate the download file size in bytes.

    When ``content_length`` is available it is returned directly.  Otherwise
    an estimate is computed from the duration and bitrate.

    The formula used is::

        size_bytes = (tbr_kbps * 1000 * duration_seconds) / 8

    Args:
        content_length: Pre-reported content length in bytes, or ``None``.
        approx_duration_ms: Approximate stream duration in milliseconds.
        tbr: Total bitrate in kbps.  Used when ``content_length`` is
            ``None``.  If ``tbr`` is also ``None`` the estimate cannot be
            computed.

    Returns:
        Estimated file size in bytes, or ``None`` when the estimate cannot
        be computed.
    """
    if content_length is not None and content_length >= 0:
        return content_length
    if (
        approx_duration_ms is not None
        and approx_duration_ms > 0
        and tbr is not None
        and tbr >= 0
    ):
        try:
            duration_sec = approx_duration_ms / 1000.0
            bits = tbr * 1000.0 * duration_sec
            return max(0, int(bits / 8.0))
        except (ZeroDivisionError, OverflowError, TypeError):
            return None
    return None


# ---------------------------------------------------------------------------
# Quality ordinal helpers
# ---------------------------------------------------------------------------


def _lookup_quality_ordinal(label: Optional[str]) -> int:
    """Return a sort key for a quality label string.

    Args:
        label: Quality label such as ``"1080p"``, or ``None``.

    Returns:
        Integer ordinal where higher values indicate higher quality.
        Returns ``0`` when the label is ``None`` or unrecognised.
    """
    if label is None:
        return 0
    return _QUALITY_ORDINAL_MAP.get(label.lower(), 0)


def _lookup_extension(container: Optional[str], mime: Optional[str]) -> Optional[str]:
    """Derive a file extension from a container name or MIME type.

    The lookup order is:

    1. *container* if it is a known container in :data:`CONTAINERS`.
    2. The first extension registered for *mime* in :data:`EXT_MIME_MAP`.
    3. ``None``.

    Args:
        container: Container format string such as ``"mp4"`` or ``"webm"``.
        mime: Full MIME type string used as a fallback when *container* is
            ``None``.

    Returns:
        A file extension string, or ``None`` when neither source yields a
        known extension.
    """
    if container:
        cont_lower = container.lower()
        if cont_lower in _CONTAINER_SET:
            return cont_lower
    if mime:
        base = mime.lower().split(";")[0].strip()
        exts = EXT_MIME_MAP.get(base)
        if exts:
            return exts[0]
    return None


def _detect_protocol(
    protocol: Optional[str], mime: Optional[str]
) -> Tuple[bool, bool]:
    """Return ``(is_dash, is_hls)`` flags for a format.

    Args:
        protocol: The protocol string from the format dict.
        mime: The MIME type string, used as a fallback signal for HLS
            (``"application/x-mpegURL"``).

    Returns:
        A two-element tuple ``(is_dash, is_hls)`` of booleans.
    """
    proto_lower = (protocol or "").lower()
    mime_lower = (mime or "").lower()
    is_dash = proto_lower in _DASH_PROTOCOL_SET
    is_hls = (
        proto_lower in _HLS_PROTOCOL_SET
        or "application/x-mpegurl" in mime_lower
    )
    return is_dash, is_hls


def _is_known_audio_itag(itag: int) -> bool:
    """Return ``True`` if *itag* is a known audio-only itag.

    Args:
        itag: YouTube itag number.

    Returns:
        ``True`` when the itag is in the known audio-only set.
    """
    return itag in _AUDIO_ONLY_ITAG_SET


def _is_known_video_itag(itag: int) -> bool:
    """Return ``True`` if *itag* is a known video-only (DASH) itag.

    Args:
        itag: YouTube itag number.

    Returns:
        ``True`` when the itag is in the known video-only set.
    """
    return itag in _VIDEO_ONLY_ITAG_SET


def _is_known_combined_itag(itag: int) -> bool:
    """Return ``True`` if *itag* is a known progressive (combined) itag.

    Args:
        itag: YouTube itag number.

    Returns:
        ``True`` when the itag is in the known progressive set.
    """
    return itag in _COMBINED_ITAG_SET


def _infer_format_type(itag: int) -> str:
    """Infer the format type string from the itag number.

    Args:
        itag: YouTube itag number.

    Returns:
        One of ``"combined"``, ``"video-only"``, ``"audio-only"``, or
        ``"unknown"``.
    """
    if _is_known_combined_itag(itag):
        return "combined"
    if _is_known_video_itag(itag):
        return "video-only"
    if _is_known_audio_itag(itag):
        return "audio-only"
    return "unknown"


# ---------------------------------------------------------------------------
# Single-format parser
# ---------------------------------------------------------------------------


def parse_single_format(fmt: Dict[str, Any]) -> StreamFormat:
    """Parse a single raw format dict from YouTube's player response.

    YouTube's ``streamingData.formats`` and
    ``streamingData.adaptiveFormats`` lists each contain dicts with the
    same general shape.  This function converts one such dict into a
    :class:`StreamFormat`.

    The parser handles the following YouTube format fields:

    * ``itag`` — integer format identifier
    * ``mimeType`` — MIME type string, possibly with codecs annotation
    * ``bitrate`` — total bitrate in bits per second
    * ``audioBitrate`` — audio-only bitrate in bits per second
    * ``videoBitrate`` — video-only bitrate in bits per second
    * ``width`` / ``height`` — frame dimensions
    * ``fps`` — frames per second
    * ``contentLength`` — file size as a string or integer
    * ``approxDurationMs`` — approximate duration in milliseconds
    * ``url`` — direct download URL
    * ``signatureCipher`` — encrypted URL parameters
    * ``protocol`` — transport protocol
    * ``qualityLabel`` — human-readable quality label

    Args:
        fmt: Raw format dict as returned by YouTube's player response API.

    Returns:
        A fully populated :class:`StreamFormat` instance.

    Raises:
        StreamDataError: When the format dict is missing the ``itag`` key
            or when itag is not an integer.
    """
    if not isinstance(fmt, dict):
        raise StreamDataError("Format entry must be a dict, got "
                              f"{type(fmt).__name__}")

    raw_itag = fmt.get("itag")
    if raw_itag is None:
        raise StreamDataError("Format dict is missing required 'itag' key")

    try:
        itag = int(raw_itag)
    except (TypeError, ValueError) as exc:
        raise StreamDataError(
            f"Invalid itag value: {raw_itag!r}; expected an integer"
        ) from exc

    mime: Optional[str] = fmt.get("mimeType")

    container, vcodec, acodec = _parse_mime_type(mime)

    vcontainer: Optional[str] = None
    acontainer: Optional[str] = None
    if mime:
        base = mime.lower().split(";")[0].strip()
        mime_container, _, _ = _MIME_TYPE_MAP.get(base, (None, None, None))
        if mime_container:
            if base.startswith("video/"):
                vcontainer = mime_container
                if not acontainer and acodec and acodec != "none":
                    acontainer = mime_container
            elif base.startswith("audio/"):
                acontainer = mime_container

    if not vcontainer and container and vcodec and vcodec != "none":
        vcontainer = container
    if not acontainer and container and acodec and acodec != "none":
        acontainer = container

    bitrate_raw = fmt.get("bitrate")
    tbr: Optional[float] = None
    if bitrate_raw is not None:
        try:
            tbr_val = float(bitrate_raw)
            tbr = tbr_val if tbr_val >= 0 else None
        except (TypeError, ValueError):
            tbr = None

    audio_bitrate_raw = fmt.get("audioBitrate")
    abr: Optional[float] = None
    if audio_bitrate_raw is not None:
        try:
            abr_val = float(audio_bitrate_raw)
            abr = abr_val if abr_val >= 0 else None
        except (TypeError, ValueError):
            abr = None

    video_bitrate_raw = fmt.get("videoBitrate")
    vbr: Optional[float] = None
    if video_bitrate_raw is not None:
        try:
            vbr_val = float(video_bitrate_raw)
            vbr = vbr_val if vbr_val >= 0 else None
        except (TypeError, ValueError):
            vbr = None

    width_raw = fmt.get("width")
    width: Optional[int] = None
    if width_raw is not None:
        try:
            width = int(width_raw)
            width = width if width >= 0 else None
        except (TypeError, ValueError):
            width = None

    height_raw = fmt.get("height")
    height: Optional[int] = None
    if height_raw is not None:
        try:
            height = int(height_raw)
            height = height if height >= 0 else None
        except (TypeError, ValueError):
            height = None

    fps_raw = fmt.get("fps")
    fps: Optional[int] = None
    if fps_raw is not None:
        try:
            fps = int(fps_raw)
            fps = fps if fps >= 0 else None
        except (TypeError, ValueError):
            fps = None

    content_length = _parse_content_length(fmt.get("contentLength"))

    approx_duration_ms: Optional[int] = None
    duration_raw = fmt.get("approxDurationMs")
    if duration_raw is not None:
        try:
            approx_duration_ms = int(duration_raw)
            approx_duration_ms = (
                approx_duration_ms if approx_duration_ms >= 0 else None
            )
        except (TypeError, ValueError):
            approx_duration_ms = None

    protocol: Optional[str] = fmt.get("protocol")

    url: Optional[str] = fmt.get("url")

    cipher: Optional[str] = (
        fmt.get("signatureCipher") or fmt.get("signature_cipher")
    )

    quality_label: Optional[str] = fmt.get("qualityLabel")
    if quality_label is None:
        itag_info = ITAG_QUALITY.get(itag)
        if itag_info:
            quality_label = itag_info

    is_dash, is_hls = _detect_protocol(protocol, mime)

    ext = _lookup_extension(container, mime)

    quality_ordinal = _lookup_quality_ordinal(quality_label)

    inferred_type = _infer_format_type(itag)

    if not vcodec and inferred_type == "video-only":
        vcodec = "avc1"
    if not acodec and inferred_type == "audio-only":
        acodec = "aac"

    logger.debug(
        "Parsed itag=%d type=%s quality=%s vcodec=%s acodec=%s container=%s "
        "height=%s width=%s fps=%s tbr=%s abr=%s vbr=%s protocol=%s "
        "is_dash=%s is_hls=%s ext=%s",
        itag,
        inferred_type,
        quality_label,
        vcodec,
        acodec,
        container,
        height,
        width,
        fps,
        tbr,
        abr,
        vbr,
        protocol,
        is_dash,
        is_hls,
        ext,
    )

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
        protocol=protocol,
        url=url,
        signature_cipher=cipher,
        content_length=content_length,
        approx_duration_ms=approx_duration_ms,
        is_dash=is_dash,
        is_hls=is_hls,
        quality_label=quality_label,
        quality_ordinal=quality_ordinal,
    )


# ---------------------------------------------------------------------------
# Top-level streaming data parser
# ---------------------------------------------------------------------------


def parse_streaming_data(streaming_data: Dict[str, Any]) -> List[StreamFormat]:
    """Parse all formats from a YouTube ``streamingData`` dict.

    Reads both the ``formats`` (progressive, combined audio+video) and
    ``adaptiveFormats`` (DASH, separate audio and video) lists and returns
    a flat list of :class:`StreamFormat` objects.

    Args:
        streaming_data: The ``streamingData`` section of a YouTube player
            response dict.  Expected to have ``"formats"`` and
            ``"adaptiveFormats"`` keys, each mapping to a list of format
            dicts.

    Returns:
        A list of :class:`StreamFormat` instances.  The list may be empty
        when the streaming data contains no formats.

    Raises:
        StreamDataError: When *streaming_data* is not a dict.
    """
    if not isinstance(streaming_data, dict):
        raise StreamDataError(
            f"streaming_data must be a dict, got {type(streaming_data).__name__}"
        )

    formats_raw: List[Any] = streaming_data.get("formats", []) or []
    adaptive_raw: List[Any] = streaming_data.get("adaptiveFormats", []) or []

    if not isinstance(formats_raw, list):
        logger.warning(
            "streamingData['formats'] is not a list (got %s); treating as empty",
            type(formats_raw).__name__,
        )
        formats_raw = []
    if not isinstance(adaptive_raw, list):
        logger.warning(
            "streamingData['adaptiveFormats'] is not a list (got %s); "
            "treating as empty",
            type(adaptive_raw).__name__,
        )
        adaptive_raw = []

    all_formats: List[StreamFormat] = []

    logger.debug("Parsing %d progressive formats from streamingData.formats",
                 len(formats_raw))
    for idx, fmt in enumerate(formats_raw):
        try:
            parsed = parse_single_format(fmt)
            all_formats.append(parsed)
        except StreamDataError as exc:
            logger.warning(
                "Skipping malformed progressive format at index %d: %s", idx, exc
            )

    logger.debug("Parsing %d adaptive formats from streamingData.adaptiveFormats",
                 len(adaptive_raw))
    for idx, fmt in enumerate(adaptive_raw):
        try:
            parsed = parse_single_format(fmt)
            all_formats.append(parsed)
        except StreamDataError as exc:
            logger.warning(
                "Skipping malformed adaptive format at index %d: %s", idx, exc
            )

    logger.info(
        "parse_streaming_data: parsed %d total stream formats "
        "(%d progressive, %d adaptive)",
        len(all_formats),
        len(formats_raw),
        len(adaptive_raw),
    )
    return all_formats


# ---------------------------------------------------------------------------
# Format filter helpers
# ---------------------------------------------------------------------------


def filter_formats(
    formats: List[StreamFormat],
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
    min_width: Optional[int] = None,
    max_width: Optional[int] = None,
    containers: Optional[List[str]] = None,
    vcodecs: Optional[List[str]] = None,
    acodecs: Optional[List[str]] = None,
    protocols: Optional[List[str]] = None,
) -> List[StreamFormat]:
    """Filter a list of :class:`StreamFormat` objects by the given criteria.

    All criteria are applied conjunctively: a format must satisfy every
    non-``None`` constraint to be included in the result.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.
        min_height: Minimum video height in pixels (inclusive).  Ignored
            for audio-only formats (those with ``height`` of ``None``).
        max_height: Maximum video height in pixels (inclusive).
        min_width: Minimum video width in pixels (inclusive).
        max_width: Maximum video width in pixels (inclusive).
        containers: Allowed container format strings (e.g.
            ``["mp4", "webm"]``).  Matched case-insensitively against
            ``vcontainer``, ``acontainer``, and ``ext``.
        vcodecs: Allowed video codec strings.  Use ``["none"]`` to include
            audio-only formats.
        acodecs: Allowed audio codec strings.  Use ``["none"]`` to include
            video-only formats.
        protocols: Allowed protocol strings (e.g. ``["https", "dash"]``).

    Returns:
        A new list containing only the formats that satisfy all supplied
        constraints.  The relative order of the input list is preserved.
    """
    result: List[StreamFormat] = []

    containers_lower = [c.lower() for c in containers] if containers else None
    vcodecs_lower = [c.lower() for c in vcodecs] if vcodecs else None
    acodecs_lower = [c.lower() for c in acodecs] if acodecs else None
    protocols_lower = [p.lower() for p in protocols] if protocols else None

    for fmt in formats:
        if min_height is not None and (fmt.height or 0) < min_height:
            continue
        if max_height is not None and (fmt.height or 0) > max_height:
            continue
        if min_width is not None and (fmt.width or 0) < min_width:
            continue
        if max_width is not None and (fmt.width or 0) > max_width:
            continue

        if containers_lower is not None:
            fmt_container_keys = {
                (fmt.vcontainer or "").lower(),
                (fmt.acontainer or "").lower(),
                (fmt.ext or "").lower(),
            }
            if not fmt_container_keys.intersection(containers_lower):
                continue

        if vcodecs_lower is not None:
            vc = (fmt.vcodec or "none").lower()
            if vc not in vcodecs_lower:
                continue

        if acodecs_lower is not None:
            ac = (fmt.acodec or "none").lower()
            if ac not in acodecs_lower:
                continue

        if protocols_lower is not None:
            proto = (fmt.protocol or "").lower()
            if proto not in protocols_lower:
                continue

        result.append(fmt)

    logger.debug(
        "filter_formats: %d/%d formats matched criteria "
        "(min_h=%s max_h=%s min_w=%s max_w=%s containers=%s vcodecs=%s "
        "acodecs=%s protocols=%s)",
        len(result),
        len(formats),
        min_height,
        max_height,
        min_width,
        max_width,
        containers_lower,
        vcodecs_lower,
        acodecs_lower,
        protocols_lower,
    )
    return result


def sort_formats(
    formats: List[StreamFormat],
    key: str = "quality",
) -> List[StreamFormat]:
    """Sort a list of :class:`StreamFormat` objects.

    Args:
        formats: List of :class:`StreamFormat` objects to sort.  The
            original list is **not** modified; a new sorted list is
            returned.
        key: Sort key string.  Accepted values:

            * ``"quality"`` — sort by height (descending), then total
              bitrate (descending), then quality ordinal (descending).
            * ``"height"`` — sort by video height (descending).  Audio-only
              formats (height ``None``) are sorted last.
            * ``"bitrate"`` — sort by total bitrate ``tbr`` (descending).
            * ``"abr"`` — sort by audio bitrate (descending).
            * ``"vbr"`` — sort by video bitrate (descending).
            * ``"size"`` — sort by estimated file size (descending).
            * ``"itag"`` — sort by itag number (ascending).

    Returns:
        A new list of :class:`StreamFormat` objects sorted according to
        *key*.
    """
    key_lower = key.lower()

    def sort_key(fmt: StreamFormat) -> Tuple:
        if key_lower == "height":
            return (fmt.height or -1, fmt.quality_ordinal, fmt.itag)
        if key_lower == "bitrate":
            return (fmt.tbr if fmt.tbr is not None else -1.0,
                    fmt.quality_ordinal, fmt.itag)
        if key_lower == "abr":
            return (fmt.abr if fmt.abr is not None else -1.0, fmt.itag)
        if key_lower == "vbr":
            return (fmt.vbr if fmt.vbr is not None else -1.0, fmt.itag)
        if key_lower == "size":
            size = fmt.estimated_size_bytes
            return (size if size is not None else -1, fmt.quality_ordinal, fmt.itag)
        if key_lower == "itag":
            return (fmt.itag,)
        # default "quality"
        return (
            fmt.height or -1,
            fmt.tbr if fmt.tbr is not None else -1.0,
            fmt.quality_ordinal,
            fmt.itag,
        )

    reverse = key_lower not in ("itag",)
    sorted_list = sorted(formats, key=sort_key, reverse=reverse)
    logger.debug("sort_formats(key=%s): sorted %d formats", key, len(sorted_list))
    return sorted_list


# ---------------------------------------------------------------------------
# Best-format selection
# ---------------------------------------------------------------------------


def get_best_format(
    formats: List[StreamFormat],
    prefer_video: bool = True,
    prefer_audio: bool = True,
) -> Optional[StreamFormat]:
    """Select the best format from a list of :class:`StreamFormat` objects.

    The selection strategy is:

    1. Prefer formats that have both video and audio (combined).
    2. When *prefer_video* is ``True`` but no combined format is found,
       fall back to the best video-only format.
    3. When *prefer_audio* is ``True`` but no combined or video-only format
       is found, fall back to the best audio-only format.
    4. If the list is empty or no suitable format is found, ``None`` is
       returned.

    Within each category the best format is chosen by height, then total
    bitrate, then quality ordinal.

    Args:
        formats: List of :class:`StreamFormat` objects to select from.
        prefer_video: When ``True`` video-only formats are considered as
            a fallback when no combined format is available.
        prefer_audio: When ``True`` audio-only formats are considered as
            a fallback.

    Returns:
        The best matching :class:`StreamFormat`, or ``None``.

    Raises:
        FormatSelectionError: When no suitable format can be found and the
            format list is non-empty.
    """
    if not formats:
        logger.warning("get_best_format: empty format list provided")
        return None

    combined = get_combined_formats(formats)
    if combined:
        best = sort_formats(combined, key="quality")[0]
        logger.debug("get_best_format: selected combined itag=%d", best.itag)
        return best

    if prefer_video:
        video_only = get_video_only_formats(formats)
        if video_only:
            best = sort_formats(video_only, key="quality")[0]
            logger.debug("get_best_format: selected video-only itag=%d", best.itag)
            return best

    if prefer_audio:
        audio_only = get_audio_only_formats(formats)
        if audio_only:
            best = sort_formats(audio_only, key="abr")[0]
            logger.debug("get_best_format: selected audio-only itag=%d", best.itag)
            return best

    logger.warning("get_best_format: no suitable format found in %d formats",
                   len(formats))
    raise FormatSelectionError(
        "No suitable stream format found in the provided list"
    )


def get_format_by_itag(
    formats: List[StreamFormat], itag: int
) -> Optional[StreamFormat]:
    """Find a specific format by its YouTube itag number.

    Args:
        formats: List of :class:`StreamFormat` objects to search.
        itag: The itag number to look up.

    Returns:
        The first :class:`StreamFormat` with a matching ``itag``, or
        ``None`` if no match is found.
    """
    for fmt in formats:
        if fmt.itag == itag:
            logger.debug("Found format by itag=%d", itag)
            return fmt
    logger.debug("Format itag=%d not found in %d formats", itag, len(formats))
    return None


# ---------------------------------------------------------------------------
# Category filter helpers
# ---------------------------------------------------------------------------


def get_audio_only_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return all audio-only formats from the given list.

    Audio-only formats are those that have an audio codec other than
    ``"none"`` and no video codec (or a video codec of ``"none"``).

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        A list of :class:`StreamFormat` objects that carry audio only.
    """
    return [f for f in formats if f.is_audio_only]


def get_video_only_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return all video-only formats from the given list.

    Video-only formats are those that have a video codec other than
    ``"none"`` and no audio codec (or an audio codec of ``"none"``).

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        A list of :class:`StreamFormat` objects that carry video only.
    """
    return [f for f in formats if f.is_video_only]


def get_combined_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return all combined (audio + video) formats from the given list.

    Combined formats are progressive streams that carry both an audio and
    a video track in a single file.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        A list of :class:`StreamFormat` objects that carry both audio and
        video.
    """
    return [f for f in formats if f.is_combined]


# ---------------------------------------------------------------------------
# Format information and diagnostics
# ---------------------------------------------------------------------------


def get_format_info(formats: List[StreamFormat]) -> str:
    """Build a human-readable summary string for a list of formats.

    Each format is rendered as a single line with its itag, quality,
    resolution, codecs, container, protocol, and estimated size.

    Args:
        formats: List of :class:`StreamFormat` objects to summarise.

    Returns:
        A multi-line string describing each format.
    """
    lines: List[str] = []
    header = (
        f"{'itag':>5}  {'quality':>7}  {'res':>10}  {'fps':>4}  "
        f"{'codecs':>20}  {'container':>8}  {'protocol':>8}  "
        f"{'size':>10}  url"
    )
    lines.append(header)
    lines.append("-" * max(len(header), 140))
    for fmt in formats:
        res = f"{fmt.width or '?'}x{fmt.height or '?'}"
        fps_str = str(fmt.fps) if fmt.fps else "?"
        codecs_str = f"v:{fmt.vcodec or 'none'}/a:{fmt.acodec or 'none'}"
        container_str = fmt.vcontainer or fmt.acontainer or fmt.ext or "?"
        protocol_str = fmt.protocol or "?"
        size_str = _format_size_local(fmt.estimated_size_bytes)
        url_preview = (fmt.url or fmt.signature_cipher or "?")[:60]
        lines.append(
            f"{fmt.itag:>5}  {fmt.quality_label or '?':>7}  {res:>10}  "
            f"{fps_str:>4}  {codecs_str:>20}  {container_str:>8}  "
            f"{protocol_str:>8}  {size_str:>10}  {url_preview}"
        )
    return "\n".join(lines)


def print_format_list(formats: List[StreamFormat]) -> None:
    """Print a human-readable format list to stdout.

    Args:
        formats: List of :class:`StreamFormat` objects to display.
    """
    print(get_format_info(formats))


def get_category_counts(formats: List[StreamFormat]) -> Dict[str, int]:
    """Return a summary count of formats by category.

    Args:
        formats: List of :class:`StreamFormat` objects to categorise.

    Returns:
        A dict with keys ``"total"``, ``"combined"``, ``"video_only"``,
        ``"audio_only"``, ``"dash"``, and ``"hls"``.
    """
    return {
        "total": len(formats),
        "combined": len(get_combined_formats(formats)),
        "video_only": len(get_video_only_formats(formats)),
        "audio_only": len(get_audio_only_formats(formats)),
        "dash": sum(1 for f in formats if f.is_dash),
        "hls": sum(1 for f in formats if f.is_hls),
    }


def has_encrypted_urls(formats: List[StreamFormat]) -> bool:
    """Check whether any format in the list has an encrypted URL.

    Encrypted URLs require the ``signatureCipher`` to be deciphered before
    the stream can be downloaded.

    Args:
        formats: List of :class:`StreamFormat` objects to check.

    Returns:
        ``True`` if at least one format has a non-``None``
        ``signature_cipher``, ``False`` otherwise.
    """
    return any(fmt.signature_cipher is not None for fmt in formats)


def get_formats_missing_url(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return formats that lack a direct download URL.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        Formats with neither a ``url`` nor a ``signature_cipher``.
    """
    return [fmt for fmt in formats
            if fmt.url is None and fmt.signature_cipher is None]


def iter_by_itag(
    formats: List[StreamFormat],
) -> Dict[int, StreamFormat]:
    """Index a list of formats by itag number.

    When multiple formats share the same itag the last one in the input
    list wins.

    Args:
        formats: List of :class:`StreamFormat` objects to index.

    Returns:
        A dict mapping itag integers to :class:`StreamFormat` objects.
    """
    return {fmt.itag: fmt for fmt in formats}


def group_by_resolution(
    formats: List[StreamFormat],
) -> Dict[Optional[int], List[StreamFormat]]:
    """Group formats by their vertical resolution (height).

    Audio-only formats (height is ``None``) are grouped under ``None``.

    Args:
        formats: List of :class:`StreamFormat` objects to group.

    Returns:
        A dict mapping height integers (or ``None``) to lists of
        :class:`StreamFormat` objects.
    """
    groups: Dict[Optional[int], List[StreamFormat]] = {}
    for fmt in formats:
        groups.setdefault(fmt.height, []).append(fmt)
    return groups


def group_by_container(
    formats: List[StreamFormat],
) -> Dict[str, List[StreamFormat]]:
    """Group formats by their container format.

    Args:
        formats: List of :class:`StreamFormat` objects to group.

    Returns:
        A dict mapping lower-case container name strings to lists of
        :class:`StreamFormat` objects.
    """
    groups: Dict[str, List[StreamFormat]] = {}
    for fmt in formats:
        key = (fmt.vcontainer or fmt.acontainer or fmt.ext or "unknown").lower()
        groups.setdefault(key, []).append(fmt)
    return groups


def group_by_codec(
    formats: List[StreamFormat],
) -> Dict[str, List[StreamFormat]]:
    """Group formats by their video codec.

    Args:
        formats: List of :class:`StreamFormat` objects to group.

    Returns:
        A dict mapping lower-case video codec strings to lists of
        :class:`StreamFormat` objects.
    """
    groups: Dict[str, List[StreamFormat]] = {}
    for fmt in formats:
        key = (fmt.vcodec or "none").lower()
        groups.setdefault(key, []).append(fmt)
    return groups


def get_unique_itags(formats: List[StreamFormat]) -> List[int]:
    """Return a sorted list of unique itag numbers in the given list.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A sorted list of unique itag integers.
    """
    return sorted({fmt.itag for fmt in formats})


def filter_by_resolution_range(
    formats: List[StreamFormat],
    min_height: Optional[int] = None,
    max_height: Optional[int] = None,
) -> List[StreamFormat]:
    """Filter formats to those whose height falls within the given range.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.
        min_height: Minimum height in pixels (inclusive).
        max_height: Maximum height in pixels (inclusive).

    Returns:
        A list of :class:`StreamFormat` objects within the height range.
        Audio-only formats (height ``None``) are always excluded.
    """
    return [
        fmt
        for fmt in formats
        if fmt.height is not None
        and (min_height is None or fmt.height >= min_height)
        and (max_height is None or fmt.height <= max_height)
    ]


def prefer_format(
    formats: List[StreamFormat],
    preferred_itag: Optional[int] = None,
    preferred_container: Optional[str] = None,
    preferred_vcodec: Optional[str] = None,
    preferred_acodec: Optional[str] = None,
) -> Optional[StreamFormat]:
    """Select a format matching a set of preferences, falling back gracefully.

    The selection proceeds in this order:

    1. An exact match on ``preferred_itag`` (if given).
    2. The best format among those matching the preferred container,
       video codec, and audio codec (in that priority order).
    3. The overall best format via :func:`get_best_format`.

    Args:
        formats: List of :class:`StreamFormat` objects to select from.
        preferred_itag: Preferred itag number.
        preferred_container: Preferred container format string.
        preferred_vcodec: Preferred video codec string.
        preferred_acodec: Preferred audio codec string.

    Returns:
        The best matching :class:`StreamFormat`, or ``None`` when the
        format list is empty.
    """
    if not formats:
        return None

    if preferred_itag is not None:
        exact = get_format_by_itag(formats, preferred_itag)
        if exact is not None:
            logger.debug("Selected preferred itag=%d", preferred_itag)
            return exact

    pool = list(formats)

    if preferred_container:
        target = preferred_container.lower()
        matching = [
            fmt for fmt in pool
            if target in {
                (fmt.vcontainer or "").lower(),
                (fmt.acontainer or "").lower(),
                (fmt.ext or "").lower(),
            }
        ]
        if matching:
            pool = matching

    if preferred_vcodec:
        target = preferred_vcodec.lower()
        matching = [
            fmt for fmt in pool
            if (fmt.vcodec or "none").lower() == target
        ]
        if matching:
            pool = matching

    if preferred_acodec:
        target = preferred_acodec.lower()
        matching = [
            fmt for fmt in pool
            if (fmt.acodec or "none").lower() == target
        ]
        if matching:
            pool = matching

    if len(pool) == 1:
        logger.debug("Selected single preferred format itag=%d", pool[0].itag)
        return pool[0]

    if pool:
        best = sort_formats(pool, key="quality")[0]
        logger.debug("Selected best of preferred pool itag=%d", best.itag)
        return best

    return get_best_format(formats)


def resolve_urls(
    formats: List[StreamFormat],
    cipher_map: Optional[Dict[str, str]] = None,
) -> List[StreamFormat]:
    """Resolve encrypted URLs using a cipher mapping.

    For formats that carry a ``signature_cipher`` but no direct ``url``,
    this function looks up the decipher URL in *cipher_map* (keyed by
    raw cipher string) and populates the ``url`` field.

    Args:
        formats: List of :class:`StreamFormat` objects to resolve.
        cipher_map: Optional mapping of raw ``signature_cipher`` strings
            to their resolved URLs.  When ``None`` or when a cipher is
            not in the map, the format is left unchanged.

    Returns:
        A new list of :class:`StreamFormat` objects with resolved URLs
        where possible.
    """
    if not cipher_map:
        return formats

    resolved: List[StreamFormat] = []
    for fmt in formats:
        if fmt.url or not fmt.signature_cipher:
            resolved.append(fmt)
            continue
        resolved_url = cipher_map.get(fmt.signature_cipher)
        if resolved_url:
            logger.debug("Resolved cipher for itag=%d", fmt.itag)
            new_fmt = StreamFormat(
                **{
                    **fmt.__dict__,
                    "url": resolved_url,
                    "signature_cipher": None,
                }
            )
            resolved.append(new_fmt)
        else:
            logger.warning("Could not resolve cipher for itag=%d", fmt.itag)
            resolved.append(fmt)
    return resolved


# ---------------------------------------------------------------------------
# Stream type predicates
# ---------------------------------------------------------------------------


def is_dash_stream(fmt: StreamFormat) -> bool:
    """Return whether a format is a DASH adaptive stream.

    Args:
        fmt: The :class:`StreamFormat` to inspect.

    Returns:
        ``True`` if the format is a DASH stream.
    """
    return fmt.is_dash


def is_hls_stream(fmt: StreamFormat) -> bool:
    """Return whether a format uses HLS.

    Args:
        fmt: The :class:`StreamFormat` to inspect.

    Returns:
        ``True`` if the format is an HLS stream.
    """
    return fmt.is_hls


def is_progressive_stream(fmt: StreamFormat) -> bool:
    """Return whether a format is a progressive (non-adaptive) stream.

    Progressive streams carry both audio and video in a single file and
    use plain HTTP/HTTPS rather than DASH or HLS.

    Args:
        fmt: The :class:`StreamFormat` to inspect.

    Returns:
        ``True`` if the format is a progressive stream.
    """
    return (
        fmt.is_combined
        and not fmt.is_dash
        and not fmt.is_hls
    )


def get_dash_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return all DASH formats from the given list.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        A list of :class:`StreamFormat` objects that use DASH.
    """
    return [fmt for fmt in formats if fmt.is_dash]


def get_hls_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return all HLS formats from the given list.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        A list of :class:`StreamFormat` objects that use HLS.
    """
    return [fmt for fmt in formats if fmt.is_hls]


def get_progressive_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return all progressive (combined, non-DASH, non-HLS) formats.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        A list of :class:`StreamFormat` objects that are progressive
        streams.
    """
    return [fmt for fmt in formats if is_progressive_stream(fmt)]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_format(fmt: StreamFormat) -> List[str]:
    """Validate a :class:`StreamFormat` instance and return any issues.

    The validation checks that the format has a usable URL, a valid itag,
    and coherent codec/type declarations.

    Args:
        fmt: The :class:`StreamFormat` to validate.

    Returns:
        A list of human-readable issue strings.  The list is empty when
        the format passes all checks.
    """
    issues: List[str] = []
    if not isinstance(fmt.itag, int) or fmt.itag <= 0:
        issues.append(f"Invalid itag: {fmt.itag!r}")
    if fmt.url is None and fmt.signature_cipher is None:
        issues.append("No download URL or signatureCipher present")
    if fmt.has_video and fmt.height is None:
        issues.append("Video format is missing height")
    if fmt.has_video and fmt.width is None:
        issues.append("Video format is missing width")
    if fmt.is_dash and fmt.is_hls:
        issues.append("Format is marked as both DASH and HLS")
    if fmt.tbr is not None and fmt.tbr < 0:
        issues.append(f"Negative total bitrate: {fmt.tbr}")
    if fmt.content_length is not None and fmt.content_length < 0:
        issues.append(f"Negative content length: {fmt.content_length}")
    return issues


def is_format_valid(fmt: StreamFormat) -> bool:
    """Return ``True`` if the format passes all validation checks.

    Args:
        fmt: The :class:`StreamFormat` to validate.

    Returns:
        ``True`` when :func:`validate_format` returns an empty list.
    """
    return len(validate_format(fmt)) == 0


def filter_valid_formats(
    formats: List[StreamFormat],
) -> List[StreamFormat]:
    """Return only the formats that pass :func:`validate_format`.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        Formats with no validation issues.
    """
    return [fmt for fmt in formats if is_format_valid(fmt)]


def get_invalid_formats(
    formats: List[StreamFormat],
) -> Dict[StreamFormat, List[str]]:
    """Return formats that fail validation alongside their issue lists.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A dict mapping each invalid :class:`StreamFormat` to its list of
        issue strings.
    """
    result: Dict[StreamFormat, List[str]] = {}
    for fmt in formats:
        issues = validate_format(fmt)
        if issues:
            result[fmt] = issues
    return result


# ---------------------------------------------------------------------------
# Format merging and pairing helpers
# ---------------------------------------------------------------------------


def pair_audio_video(
    video_formats: List[StreamFormat],
    audio_formats: List[StreamFormat],
) -> List[Tuple[StreamFormat, StreamFormat]]:
    """Pair each video-only format with the best matching audio format.

    The best audio format for each video is selected by preferring the
    highest audio bitrate, then the highest quality ordinal.

    Args:
        video_formats: List of video-only :class:`StreamFormat` objects.
        audio_formats: List of audio-only :class:`StreamFormat` objects.

    Returns:
        A list of ``(video_format, audio_format)`` tuples, one per video
        format.  When *audio_formats* is empty, each tuple contains the
        video format paired with ``None``.
    """
    if not audio_formats:
        return [(v, None) for v in video_formats]

    sorted_audio = sort_formats(audio_formats, key="abr")
    best_audio = sorted_audio[0] if sorted_audio else None

    return [(v, best_audio) for v in video_formats]


def find_complementary_audio(
    video_fmt: StreamFormat,
    audio_formats: List[StreamFormat],
) -> Optional[StreamFormat]:
    """Find the best audio format to pair with a given video format.

    Selection prefers:

    1. Same container as the video format's container.
    2. Highest audio bitrate.
    3. Highest quality ordinal.

    Args:
        video_fmt: The video-only :class:`StreamFormat` to pair.
        audio_formats: List of candidate audio-only formats.

    Returns:
        The best matching audio :class:`StreamFormat`, or ``None`` when
        *audio_formats* is empty.
    """
    if not audio_formats:
        return None

    video_container = (video_fmt.vcontainer or video_fmt.ext or "").lower()

    same_container = [
        f for f in audio_formats
        if (f.acontainer or f.ext or "").lower() == video_container
    ]
    pool = same_container if same_container else audio_formats
    sorted_pool = sort_formats(pool, key="abr")
    return sorted_pool[0] if sorted_pool else None


def merge_format_lists(
    list_a: List[StreamFormat],
    list_b: List[StreamFormat],
) -> List[StreamFormat]:
    """Merge two format lists, deduplicating by itag.

    When both lists contain formats with the same itag the format from
    *list_a* takes precedence.

    Args:
        list_a: Primary list of :class:`StreamFormat` objects.
        list_b: Secondary list of :class:`StreamFormat` objects.

    Returns:
        A merged list with unique itags, preserving the order of *list_a*
        followed by any unique items from *list_b*.
    """
    seen_itags: Set[int] = set()
    merged: List[StreamFormat] = []

    for fmt in list_a:
        if fmt.itag not in seen_itags:
            seen_itags.add(fmt.itag)
            merged.append(fmt)

    for fmt in list_b:
        if fmt.itag not in seen_itags:
            seen_itags.add(fmt.itag)
            merged.append(fmt)

    return merged


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_formats(
    formats: List[StreamFormat],
) -> List[StreamFormat]:
    """Remove duplicate formats (same itag) from the list.

    When duplicates are found the first occurrence is kept.

    Args:
        formats: List of :class:`StreamFormat` objects to deduplicate.

    Returns:
        A new list with duplicate itags removed.
    """
    seen: Set[int] = set()
    unique: List[StreamFormat] = []
    for fmt in formats:
        if fmt.itag not in seen:
            seen.add(fmt.itag)
            unique.append(fmt)
    return unique


# ---------------------------------------------------------------------------
# Format metadata helpers
# ---------------------------------------------------------------------------


def get_available_heights(formats: List[StreamFormat]) -> List[int]:
    """Return a sorted list of unique video heights present in the list.

    Audio-only formats (with ``height`` of ``None``) are excluded.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A sorted list of unique height integers.
    """
    return sorted({fmt.height for fmt in formats if fmt.height is not None})


def get_available_containers(formats: List[StreamFormat]) -> List[str]:
    """Return a sorted list of unique container formats in the list.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A sorted list of unique lower-case container strings.
    """
    containers: Set[str] = set()
    for fmt in formats:
        for c in (fmt.vcontainer, fmt.acontainer, fmt.ext):
            if c:
                containers.add(c.lower())
    return sorted(containers)


def get_available_codecs(
    formats: List[StreamFormat],
) -> Tuple[List[str], List[str]]:
    """Return sorted lists of unique video and audio codecs.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A two-element tuple ``(video_codecs, audio_codecs)`` where each
        element is a sorted list of lower-case codec strings.
    """
    vcodecs: Set[str] = set()
    acodecs: Set[str] = set()
    for fmt in formats:
        if fmt.vcodec and fmt.vcodec.lower() != "none":
            vcodecs.add(fmt.vcodec.lower())
        if fmt.acodec and fmt.acodec.lower() != "none":
            acodecs.add(fmt.acodec.lower())
    return sorted(vcodecs), sorted(acodecs)


def get_max_resolution(formats: List[StreamFormat]) -> Tuple[Optional[int], Optional[int]]:
    """Return the maximum width and height across all video formats.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A two-element tuple ``(max_width, max_height)`` where each element
        is ``None`` when no video formats are present.
    """
    max_w: Optional[int] = None
    max_h: Optional[int] = None
    for fmt in formats:
        if fmt.width is not None:
            max_w = fmt.width if max_w is None else max(max_w, fmt.width)
        if fmt.height is not None:
            max_h = fmt.height if max_h is None else max(max_h, fmt.height)
    return max_w, max_h


def get_min_resolution(formats: List[StreamFormat]) -> Tuple[Optional[int], Optional[int]]:
    """Return the minimum width and height across all video formats.

    Args:
        formats: List of :class:`StreamFormat` objects to inspect.

    Returns:
        A two-element tuple ``(min_width, min_height)`` where each element
        is ``None`` when no video formats are present.
    """
    min_w: Optional[int] = None
    min_h: Optional[int] = None
    for fmt in formats:
        if fmt.width is not None:
            min_w = fmt.width if min_w is None else min(min_w, fmt.width)
        if fmt.height is not None:
            min_h = fmt.height if min_h is None else min(min_h, fmt.height)
    return min_w, min_h


def select_highest_quality(
    formats: List[StreamFormat],
    prefer_container: Optional[str] = None,
) -> Optional[StreamFormat]:
    """Select the format with the highest quality from the list.

    This is a convenience wrapper around :func:`get_best_format` that
    always selects the highest quality combined or video format.

    Args:
        formats: List of :class:`StreamFormat` objects to select from.
        prefer_container: Optional container preference used as a
            secondary sort criterion.

    Returns:
        The highest-quality :class:`StreamFormat`, or ``None``.
    """
    if not formats:
        return None
    video_formats = get_video_only_formats(formats) + get_combined_formats(formats)
    if not video_formats:
        return get_best_format(formats, prefer_video=False, prefer_audio=True)
    if prefer_container:
        pool = [
            f for f in video_formats
            if (f.vcontainer or f.ext or "").lower() == prefer_container.lower()
        ]
        if pool:
            video_formats = pool
    return sort_formats(video_formats, key="quality")[0]


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    "StreamFormat",
    "StreamDataError",
    "parse_streaming_data",
    "parse_single_format",
    "_parse_mime_type",
    "_parse_content_length",
    "_estimate_size",
    "filter_formats",
    "sort_formats",
    "get_best_format",
    "get_format_by_itag",
    "get_audio_only_formats",
    "get_video_only_formats",
    "get_combined_formats",
    "get_format_info",
    "print_format_list",
    "get_category_counts",
    "has_encrypted_urls",
    "get_formats_missing_url",
    "iter_by_itag",
    "group_by_resolution",
    "group_by_container",
    "group_by_codec",
    "get_unique_itags",
    "filter_by_resolution_range",
    "prefer_format",
    "resolve_urls",
    "is_dash_stream",
    "is_hls_stream",
    "is_progressive_stream",
    "get_dash_formats",
    "get_hls_formats",
    "get_progressive_formats",
    "validate_format",
    "is_format_valid",
    "filter_valid_formats",
    "get_invalid_formats",
    "pair_audio_video",
    "find_complementary_audio",
    "merge_format_lists",
    "deduplicate_formats",
    "get_available_heights",
    "get_available_containers",
    "get_available_codecs",
    "get_max_resolution",
    "get_min_resolution",
    "select_highest_quality",
]
