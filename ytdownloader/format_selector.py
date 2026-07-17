"""
Smart format selection engine for YouTube stream resolution.

This module provides intelligent quality-based selection of YouTube stream
formats.  It handles DASH vs progressive streams, combined vs separate
audio/video formats, and provides detailed logging for every selection
decision so operators can diagnose why a particular format was chosen.

The primary entry point is :func:`select_format`, which accepts a list of
:class:`~ytdownloader.streaming_data.StreamFormat` objects and a quality
string (e.g. ``"best"``, ``"720p"``, ``"1080p"``) and returns the single
best matching format.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ytdownloader.exceptions import FormatSelectionError
from ytdownloader.logger import get_logger
from ytdownloader.streaming_data import StreamFormat
from ytdownloader.constants import (
    AUDIO_ONLY_ITAGS,
    DEFAULT_AUDIO_FORMAT_PREFERENCE,
    DEFAULT_VIDEO_FORMAT_PREFERENCE,
    MAX_QUALITY,
    MIN_QUALITY,
    PROGRESSIVE_ITAGS,
    QUALITY_HEIGHT_MAP,
    VIDEO_ONLY_ITAGS,
)

_logger = get_logger(__name__)


__all__ = [
    "FormatSelectionResult",
    "FormatSelectionError",
    "select_format",
    "_select_by_quality",
    "_select_best_combined",
    "_select_best_video",
    "_select_best_audio",
    "_fallback_chain",
    "list_available_formats",
    "get_quality_selector",
    "resolve_merge_pair",
    "categorize_formats",
    "filter_by_quality_threshold",
    "sort_by_selection_rank",
    "SELECTION_STRATEGIES",
    "SelectionStrategy",
]


# ---------------------------------------------------------------------------
# Selection result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FormatSelectionResult:
    """Structured result of a format selection operation.

    Attributes:
        format: The selected :class:`StreamFormat`, or ``None`` if no
            suitable format was found.
        reason: Human-readable explanation of why this format was chosen.
        strategy: Name of the selection strategy that produced this result.
        candidates_considered: Total number of formats evaluated.
        candidates_combined: Number of combined formats found.
        candidates_video_only: Number of video-only formats found.
        candidates_audio_only: Number of audio-only formats found.
        candidates_dash: Number of DASH/HLS formats found.
        candidates_progressive: Number of progressive formats found.
        fallback_used: ``True`` when the result came from a fallback chain
            rather than the primary selection path.
        quality_requested: The quality string passed by the caller.
        quality_matched: The quality label of the returned format, or
            ``"none"`` if no format was returned.
    """

    format: Optional[StreamFormat] = None
    reason: str = ""
    strategy: str = ""
    candidates_considered: int = 0
    candidates_combined: int = 0
    candidates_video_only: int = 0
    candidates_audio_only: int = 0
    candidates_dash: int = 0
    candidates_progressive: int = 0
    fallback_used: bool = False
    quality_requested: str = "best"
    quality_matched: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this result to a plain dictionary."""
        return {
            "format": self.format.to_dict() if self.format else None,
            "reason": self.reason,
            "strategy": self.strategy,
            "candidates_considered": self.candidates_considered,
            "candidates_combined": self.candidates_combined,
            "candidates_video_only": self.candidates_video_only,
            "candidates_audio_only": self.candidates_audio_only,
            "candidates_dash": self.candidates_dash,
            "candidates_progressive": self.candidates_progressive,
            "fallback_used": self.fallback_used,
            "quality_requested": self.quality_requested,
            "quality_matched": self.quality_matched,
        }

    def __repr__(self) -> str:
        if self.format:
            return (
                f"FormatSelectionResult(format={self.format!r}, "
                f"strategy={self.strategy!r}, reason={self.reason!r})"
            )
        return f"FormatSelectionResult(format=None, strategy={self.strategy!r}, reason={self.reason!r})"


# ---------------------------------------------------------------------------
# Selection strategy helpers
# ---------------------------------------------------------------------------


class SelectionStrategy:
    """Namespace of named selection strategy constants."""

    BEST_COMBINED = "best_combined"
    BEST_VIDEO_ONLY = "best_video_only"
    BEST_AUDIO_ONLY = "best_audio_only"
    BY_QUALITY = "by_quality"
    FALLBACK = "fallback"
    DASH_MERGE = "dash_merge"
    PROGRESSIVE = "progressive"
    HIGHEST_BITRATE = "highest_bitrate"


SELECTION_STRATEGIES: List[str] = [
    SelectionStrategy.BEST_COMBINED,
    SelectionStrategy.BEST_VIDEO_ONLY,
    SelectionStrategy.BEST_AUDIO_ONLY,
    SelectionStrategy.BY_QUALITY,
    SelectionStrategy.FALLBACK,
    SelectionStrategy.DASH_MERGE,
    SelectionStrategy.PROGRESSIVE,
    SelectionStrategy.HIGHEST_BITRATE,
]


# ---------------------------------------------------------------------------
# Internal categorisation helpers
# ---------------------------------------------------------------------------


def categorize_formats(
    formats: Sequence[StreamFormat],
) -> Dict[str, List[StreamFormat]]:
    """Categorize a list of formats into combined, video-only, and audio-only.

    Args:
        formats: Input list of :class:`StreamFormat` objects.

    Returns:
        A dictionary with keys ``"combined"``, ``"video_only"``,
        ``"audio_only"``, and ``"unknown"``, each mapping to a list of
        formats in that category.
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


def _categorize_streams(
    streams: Sequence[StreamFormat],
) -> Tuple[List[StreamFormat], List[StreamFormat], List[StreamFormat]]:
    """Split streams into combined, video-only, and audio-only lists.

    This is a convenience helper used internally by selection functions.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.

    Returns:
        A 3-tuple ``(combined, video_only, audio_only)`` of lists.
    """
    combined: List[StreamFormat] = []
    video_only: List[StreamFormat] = []
    audio_only: List[StreamFormat] = []
    for s in streams:
        if s.is_combined:
            combined.append(s)
        elif s.is_video_only:
            video_only.append(s)
        elif s.is_audio_only:
            audio_only.append(s)
    return combined, video_only, audio_only


def _count_stream_attributes(
    streams: Sequence[StreamFormat],
) -> Dict[str, int]:
    """Return counts of formats by type and protocol.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.

    Returns:
        A dictionary with keys ``combined``, ``video_only``, ``audio_only``,
        ``dash``, ``progressive``, and ``total``.
    """
    combined_count = 0
    video_only_count = 0
    audio_only_count = 0
    dash_count = 0
    progressive_count = 0
    for s in streams:
        if s.is_combined:
            combined_count += 1
        elif s.is_video_only:
            video_only_count += 1
        elif s.is_audio_only:
            audio_only_count += 1
        if s.is_dash or s.is_hls:
            dash_count += 1
        else:
            progressive_count += 1
    return {
        "combined": combined_count,
        "video_only": video_only_count,
        "audio_only": audio_only_count,
        "dash": dash_count,
        "progressive": progressive_count,
        "total": len(streams),
    }


# ---------------------------------------------------------------------------
# Sort / rank helpers
# ---------------------------------------------------------------------------


def _combined_sort_key(fmt: StreamFormat) -> Tuple[int, int, int]:
    """Sort key for combined formats: (height, bitrate, itag)."""
    return (fmt.height or 0, int(fmt.tbr or 0), fmt.itag)


def _video_sort_key(fmt: StreamFormat) -> Tuple[int, int, int]:
    """Sort key for video-only formats: (height, bitrate, itag)."""
    return (fmt.height or 0, int(fmt.vbr or 0), fmt.itag)


def _audio_sort_key(fmt: StreamFormat) -> Tuple[int, int]:
    """Sort key for audio-only formats: (bitrate, itag)."""
    return (int(fmt.abr or 0), fmt.itag)


def sort_by_selection_rank(formats: Sequence[StreamFormat]) -> List[StreamFormat]:
    """Sort formats by a composite selection rank.

    The rank is derived from ``quality_ordinal`` when available, falling
    back to the effective bitrate and finally the itag as a tie-breaker.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.

    Returns:
        A new list of formats sorted from highest to lowest rank.
    """
    def rank(fmt: StreamFormat) -> Tuple[int, int, int]:
        return (
            fmt.quality_ordinal,
            int(fmt.effective_bitrate or 0) * 10,
            fmt.itag,
        )

    return sorted(formats, key=rank, reverse=True)


# ---------------------------------------------------------------------------
# Threshold filtering
# ---------------------------------------------------------------------------


def filter_by_quality_threshold(
    formats: Sequence[StreamFormat],
    max_height: Optional[int] = None,
    min_height: Optional[int] = None,
) -> List[StreamFormat]:
    """Filter formats to those whose height falls within the given bounds.

    Formats without a reported height (audio-only) are included only when
    both ``min_height`` and ``max_height`` are ``None``.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.
        max_height: Maximum video height in pixels (inclusive).  When
            ``None`` no upper bound is applied.
        min_height: Minimum video height in pixels (inclusive).  When
            ``None`` no lower bound is applied.

    Returns:
        A new list of :class:`StreamFormat` objects that satisfy the bounds.
    """
    if max_height is None and min_height is None:
        return list(formats)

    result: List[StreamFormat] = []
    for fmt in formats:
        h = fmt.height
        if h is None:
            if max_height is None and min_height is None:
                result.append(fmt)
            continue
        if min_height is not None and h < min_height:
            continue
        if max_height is not None and h > max_height:
            continue
        result.append(fmt)
    return result


# ---------------------------------------------------------------------------
# Quality label parsing
# ---------------------------------------------------------------------------


_QUALITY_PATTERN = re.compile(r"^(\d+)(?:p|P)$")


def _parse_quality_label(label: str) -> Tuple[str, Optional[int]]:
    """Parse a quality string into (kind, value).

    Supported kinds:

    * ``"best"`` — ``("best", None)``
    * ``"worst"`` — ``("worst", None)``
    * itag string like ``"18"`` — ``("itag", 18)``
    * resolution like ``"1080p"`` — ``("resolution", 1080)``
    * anything else — ``("unknown", None)``

    Args:
        label: Quality string to parse.

    Returns:
        A 2-tuple ``(kind, value)``.
    """
    cleaned = label.strip().lower()
    if cleaned in ("best", "worst"):
        return cleaned, None
    if cleaned.isdigit():
        return "itag", int(cleaned)
    match = _QUALITY_PATTERN.match(cleaned)
    if match:
        return "resolution", int(match.group(1))
    return "unknown", None


def _quality_to_max_height(quality_str: str) -> Optional[int]:
    """Convert a quality string to a maximum pixel height.

    For ``"best"`` this returns ``None`` (no ceiling).  For ``"480p"`` it
    returns ``480``, etc.

    Args:
        quality_str: Quality label string.

    Returns:
        Maximum allowed height in pixels, or ``None``.
    """
    kind, value = _parse_quality_label(quality_str)
    if kind == "resolution" and value is not None:
        return value
    if kind == "best":
        return None
    return None


# ---------------------------------------------------------------------------
# Selection strategies
# ---------------------------------------------------------------------------


def _select_best_combined(
    formats: Sequence[StreamFormat],
    sort_key: Any = None,
) -> Optional[StreamFormat]:
    """Select the best combined (audio + video) format.

    If *sort_key* is provided it is used to rank formats; otherwise the
    default :func:`_combined_sort_key` is used.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.
        sort_key: Optional callable used as a sort key.

    Returns:
        The highest-ranked combined format, or ``None`` if none found.
    """
    combined = [f for f in formats if f.is_combined]
    if not combined:
        _logger.debug("_select_best_combined: no combined formats available")
        return None
    key = sort_key or _combined_sort_key
    best = sorted(combined, key=key, reverse=True)[0]
    _logger.debug(
        "_select_best_combined: selected itag=%d quality=%s",
        best.itag,
        best.quality_label,
    )
    return best


def _select_best_video(
    formats: Sequence[StreamFormat],
    sort_key: Any = None,
) -> Optional[StreamFormat]:
    """Select the best video-only format.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.
        sort_key: Optional callable used as a sort key.

    Returns:
        The highest-ranked video-only format, or ``None`` if none found.
    """
    video_only = [f for f in formats if f.is_video_only]
    if not video_only:
        _logger.debug("_select_best_video: no video-only formats available")
        return None
    key = sort_key or _video_sort_key
    best = sorted(video_only, key=key, reverse=True)[0]
    _logger.debug(
        "_select_best_video: selected itag=%d quality=%s",
        best.itag,
        best.quality_label,
    )
    return best


def _select_best_audio(
    formats: Sequence[StreamFormat],
    sort_key: Any = None,
) -> Optional[StreamFormat]:
    """Select the best audio-only format.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.
        sort_key: Optional callable used as a sort key.

    Returns:
        The highest-ranked audio-only format, or ``None`` if none found.
    """
    audio_only = [f for f in formats if f.is_audio_only]
    if not audio_only:
        _logger.debug("_select_best_audio: no audio-only formats available")
        return None
    key = sort_key or _audio_sort_key
    best = sorted(audio_only, key=key, reverse=True)[0]
    _logger.debug(
        "_select_best_audio: selected itag=%d quality=%s",
        best.itag,
        best.quality_label,
    )
    return best


def _fallback_chain(
    primary: Optional[StreamFormat],
    alternatives: Sequence[StreamFormat],
    label: str = "fallback",
) -> Optional[StreamFormat]:
    """Return *primary* if available, otherwise the best alternative.

    Each alternative is logged at DEBUG level.  If no alternatives are
    available the function returns ``None``.

    Args:
        primary: The preferred :class:`StreamFormat`, or ``None``.
        alternatives: Sequence of fallback :class:`StreamFormat` objects.
        label: Human-readable label for the fallback group, used in log
            messages.

    Returns:
        The primary format, or the best alternative, or ``None``.
    """
    if primary is not None:
        _logger.debug(
            "_fallback_chain(%s): using primary itag=%d", label, primary.itag
        )
        return primary

    _logger.debug(
        "_fallback_chain(%s): primary unavailable, checking %d alternatives",
        label,
        len(alternatives),
    )
    if not alternatives:
        _logger.debug("_fallback_chain(%s): no alternatives available", label)
        return None

    best_alt = sorted(alternatives, key=_combined_sort_key, reverse=True)[0]
    _logger.debug(
        "_fallback_chain(%s): selected alternative itag=%d quality=%s",
        label,
        best_alt.itag,
        best_alt.quality_label,
    )
    return best_alt


# ---------------------------------------------------------------------------
# Quality-based selection
# ---------------------------------------------------------------------------


def _select_by_quality(
    streams: Sequence[StreamFormat],
    quality_str: str,
) -> Optional[StreamFormat]:
    """Select a single format matching the requested quality string.

    The quality string may be one of:

    * ``"best"`` — highest quality available
    * ``"worst"`` — lowest quality available
    * A resolution such as ``"480p"``, ``"720p"``, ``"1080p"`` — finds the
      highest itag whose height is at most the specified value
    * An itag number string such as ``"18"`` — exact itag match

    For resolution-based selection the preference order is:

    1. Combined formats with height at most the target
    2. Video-only formats with height at most the target
    3. Audio-only formats (only when *quality_str* is ``"best"`` or when no
       video candidates exist)

    DASH streams are handled separately from progressive streams; progressive
    combined formats are preferred over DASH when both are available.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.
        quality_str: Quality selection string.

    Returns:
        The best matching :class:`StreamFormat`, or ``None`` if no format
        matches the requirement.

    Raises:
        FormatSelectionError: If *streams* is empty or if *quality_str* is
            unrecognised and no sensible default applies.
    """
    if not streams:
        raise FormatSelectionError("Cannot select format from an empty stream list")

    kind, value = _parse_quality_label(quality_str)

    _logger.info(
        "_select_by_quality: quality=%r kind=%s value=%s, total_streams=%d",
        quality_str,
        kind,
        value,
        len(streams),
    )

    if kind == "best":
        return _select_best_combined(streams)

    if kind == "worst":
        sorted_all = sorted(streams, key=_combined_sort_key)
        worst = sorted_all[0]
        _logger.info(
            "_select_by_quality(worst): selected itag=%d quality=%s",
            worst.itag,
            worst.quality_label,
        )
        return worst

    if kind == "itag":
        target_itag = value
        for fmt in streams:
            if fmt.itag == target_itag:
                _logger.info(
                    "_select_by_quality(itag=%d): matched itag=%d quality=%s",
                    target_itag,
                    fmt.itag,
                    fmt.quality_label,
                )
                return fmt
        _logger.warning(
            "_select_by_quality: no format found with itag=%d", target_itag
        )
        return None

    if kind == "resolution":
        target_height = value
        max_h = target_height

        combined, video_only, audio_only = _categorize_streams(streams)

        combined_candidates = [
            f for f in combined if (f.height or 0) <= max_h and (f.height or 0) > 0
        ]
        video_candidates = [
            f for f in video_only if (f.height or 0) <= max_h and (f.height or 0) > 0
        ]

        if combined_candidates:
            best = sorted(combined_candidates, key=_combined_sort_key, reverse=True)[0]
            _logger.info(
                "_select_by_quality(%dp): selected combined itag=%d height=%s",
                target_height,
                best.itag,
                best.height,
            )
            return best

        if video_candidates:
            best = sorted(video_candidates, key=_video_sort_key, reverse=True)[0]
            _logger.info(
                "_select_by_quality(%dp): selected video-only itag=%d height=%s",
                target_height,
                best.itag,
                best.height,
            )
            return best

        all_video_candidates = [
            f for f in streams
            if (f.is_combined or f.is_video_only) and (f.height or 0) > 0
            and (f.height or 0) <= max_h
        ]
        if all_video_candidates:
            best = sorted(all_video_candidates, key=_combined_sort_key, reverse=True)[0]
            _logger.info(
                "_select_by_quality(%dp): selected fallback itag=%d height=%s",
                target_height,
                best.itag,
                best.height,
            )
            return best

        progressive_combined = [
            f for f in combined if not f.is_dash and not f.is_hls
            and (f.height or 0) <= max_h and (f.height or 0) > 0
        ]
        if progressive_combined:
            best = sorted(progressive_combined, key=_combined_sort_key, reverse=True)[0]
            _logger.info(
                "_select_by_quality(%dp): selected progressive itag=%d height=%s",
                target_height,
                best.itag,
                best.height,
            )
            return best

        dash_combined = [
            f for f in combined
            if (f.is_dash or f.is_hls) and (f.height or 0) <= max_h and (f.height or 0) > 0
        ]
        if dash_combined:
            best = sorted(dash_combined, key=_combined_sort_key, reverse=True)[0]
            _logger.info(
                "_select_by_quality(%dp): selected DASH combined itag=%d height=%s",
                target_height,
                best.itag,
                best.height,
            )
            return best

        highest_available = sorted(
            [f for f in streams if (f.height or 0) > 0],
            key=_combined_sort_key,
            reverse=True,
        )
        if highest_available:
            best = highest_available[0]
            _logger.info(
                "_select_by_quality(%dp): no exact match, selected closest itag=%d height=%s",
                target_height,
                best.itag,
                best.height,
            )
            return best

        _logger.warning(
            "_select_by_quality(%dp): no video formats available", target_height
        )
        return None

    _logger.warning("_select_by_quality: unrecognised quality string %r", quality_str)
    return _select_best_combined(streams)


# ---------------------------------------------------------------------------
# DASH merge pair resolution
# ---------------------------------------------------------------------------


def resolve_merge_pair(
    streams: Sequence[StreamFormat],
    quality_str: str = "best",
) -> Optional[Tuple[StreamFormat, StreamFormat]]:
    """Find the best video-only / audio-only pair suitable for merging.

    The function first attempts to find a pair matching the requested
    quality.  When the quality is a resolution (e.g. ``"720p"``) the
    highest video-only format whose height does not exceed the target is
    paired with the best available audio-only format.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.
        quality_str: Quality selection string.

    Returns:
        A ``(video_format, audio_format)`` tuple, or ``None`` if no
        mergeable pair can be constructed.
    """
    if not streams:
        return None

    _, video_only, audio_only = _categorize_streams(streams)

    if not video_only or not audio_only:
        _logger.debug(
            "resolve_merge_pair: insufficient streams (video_only=%d, audio_only=%d)",
            len(video_only),
            len(audio_only),
        )
        return None

    kind, value = _parse_quality_label(quality_str)

    if kind == "resolution" and value is not None:
        target_height = value
        eligible_video = [
            f for f in video_only if (f.height or 0) <= target_height and (f.height or 0) > 0
        ]
    else:
        eligible_video = list(video_only)

    if not eligible_video:
        _logger.debug("resolve_merge_pair: no eligible video-only formats")
        return None

    best_video = sorted(eligible_video, key=_video_sort_key, reverse=True)[0]
    best_audio = sorted(audio_only, key=_audio_sort_key, reverse=True)[0]

    _logger.info(
        "resolve_merge_pair: selected video itag=%d + audio itag=%d for quality=%s",
        best_video.itag,
        best_audio.itag,
        quality_str,
    )
    return best_video, best_audio


# ---------------------------------------------------------------------------
# Quality selector registry
# ---------------------------------------------------------------------------


def get_quality_selector(quality_str: str) -> str:
    """Map a quality string to the name of the selection strategy to use.

    Args:
        quality_str: Quality label string (e.g. ``"best"``, ``"720p"``).

    Returns:
        One of the :data:`SelectionStrategy` constant values.
    """
    kind, _ = _parse_quality_label(quality_str)
    if kind == "best":
        return SelectionStrategy.BEST_COMBINED
    if kind == "resolution":
        return SelectionStrategy.BY_QUALITY
    if kind == "itag":
        return SelectionStrategy.BY_QUALITY
    return SelectionStrategy.BEST_COMBINED


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


def select_format(
    streams: Sequence[StreamFormat],
    quality: str = "best",
    allow_audio_only: bool = False,
    allow_video_only: bool = False,
) -> FormatSelectionResult:
    """Select the best stream format for download.

    This is the primary entry point for format selection.  It evaluates
    all available streams, applies the requested quality filter, and
    returns the best format together with a detailed selection record.

    Selection priority:

    1. Combined (progressive) formats are preferred over DASH combined.
    2. When combined formats are unavailable the function attempts to
       build a merge pair from video-only and audio-only DASH streams.
    3. Audio-only formats are only returned when *allow_audio_only* is
       ``True`` or when no video-capable format exists.
    4. Video-only formats are returned when *allow_video_only* is ``True``
       and no combined format is available.

    DASH streams are treated differently from progressive streams:
    progressive combined formats are always preferred over DASH combined
    formats.  When falling back to DASH merge pairs the function selects
    the highest bitrate audio track compatible with the chosen video
    stream.

    Every selection decision is logged at INFO or DEBUG level.

    Args:
        streams: A sequence of :class:`StreamFormat` objects representing
            all available formats for the current video.
        quality: Quality requirement string.  Accepted values are
            ``"best"``, ``"worst"``, a resolution such as ``"480p"``,
            ``"720p"``, ``"1080p"``, or an itag number string such as
            ``"18"``.
        allow_audio_only: When ``True``, audio-only formats are eligible
            for selection when no video format is available.  Defaults
            to ``False``.
        allow_video_only: When ``True``, video-only formats are eligible
            for selection when no combined format is available.  Defaults
            to ``False``.

    Returns:
        A :class:`FormatSelectionResult` describing the chosen format and
        the selection process.

    Raises:
        FormatSelectionError: If *streams* is empty.
    """
    if not streams:
        raise FormatSelectionError(
            "Cannot select format from an empty stream list"
        )

    result = FormatSelectionResult(quality_requested=quality)

    combined, video_only, audio_only = _categorize_streams(streams)
    attrs = _count_stream_attributes(streams)
    result.candidates_considered = attrs["total"]
    result.candidates_combined = attrs["combined"]
    result.candidates_video_only = attrs["video_only"]
    result.candidates_audio_only = attrs["audio_only"]
    result.candidates_dash = attrs["dash"]
    result.candidates_progressive = attrs["progressive"]

    _logger.info(
        "select_format: quality=%r allow_audio_only=%s allow_video_only=%s "
        "combined=%d video_only=%d audio_only=%d dash=%d progressive=%d",
        quality,
        allow_audio_only,
        allow_video_only,
        result.candidates_combined,
        result.candidates_video_only,
        result.candidates_audio_only,
        result.candidates_dash,
        result.candidates_progressive,
    )

    kind, value = _parse_quality_label(quality)
    max_h = _quality_to_max_height(quality)

    if kind == "itag":
        target_itag = value
        for fmt in streams:
            if fmt.itag == target_itag:
                result.format = fmt
                result.strategy = SelectionStrategy.BY_QUALITY
                result.reason = f"Exact itag match: {fmt.itag}"
                result.quality_matched = fmt.quality_label
                _logger.info(
                    "select_format(itag=%d): selected itag=%d quality=%s",
                    target_itag,
                    fmt.itag,
                    fmt.quality_label,
                )
                return result
        result.reason = f"No format found with itag={target_itag}"
        result.strategy = SelectionStrategy.FALLBACK
        _logger.warning("select_format: no format with itag=%d", target_itag)
        return result

    progressive_combined = [f for f in combined if not f.is_dash and not f.is_hls]
    dash_combined = [f for f in combined if f.is_dash or f.is_hls]
    progressive_video = [f for f in video_only if not f.is_dash and not f.is_hls]
    dash_video = [f for f in video_only if f.is_dash or f.is_hls]

    if kind == "resolution" and value is not None:
        target_height = value

        def _within_h(fmt: StreamFormat) -> bool:
            h = fmt.height or 0
            return 0 < h <= target_height

        pc = [f for f in progressive_combined if _within_h(f)]
        dc = [f for f in dash_combined if _within_h(f)]
        pv = [f for f in progressive_video if _within_h(f)]
        dv = [f for f in dash_video if _within_h(f)]
        pa = [f for f in audio_only if _within_h(f)]
        da = [f for f in audio_only if _within_h(f)]
    else:
        pc = list(progressive_combined)
        dc = list(dash_combined)
        pv = list(progressive_video)
        dv = list(dash_video)
        pa = list(audio_only)
        da = list(audio_only)

    selected: Optional[StreamFormat] = None
    strategy_used = ""
    fallback = False

    if pc:
        selected = sorted(pc, key=_combined_sort_key, reverse=True)[0]
        strategy_used = SelectionStrategy.PROGRESSIVE
        result.reason = (
            f"Selected progressive combined format itag={selected.itag} "
            f"quality={selected.quality_label} height={selected.height}"
        )
    elif dc:
        selected = sorted(dc, key=_combined_sort_key, reverse=True)[0]
        strategy_used = SelectionStrategy.DASH_MERGE
        result.reason = (
            f"Selected DASH combined format itag={selected.itag} "
            f"quality={selected.quality_label} height={selected.height}"
        )
    elif pv and allow_video_only:
        selected = sorted(pv, key=_video_sort_key, reverse=True)[0]
        strategy_used = SelectionStrategy.BEST_VIDEO_ONLY
        result.reason = (
            f"Selected progressive video-only format itag={selected.itag} "
            f"quality={selected.quality_label} height={selected.height}"
        )
    elif dv and allow_video_only:
        selected = sorted(dv, key=_video_sort_key, reverse=True)[0]
        strategy_used = SelectionStrategy.BEST_VIDEO_ONLY
        result.reason = (
            f"Selected DASH video-only format itag={selected.itag} "
            f"quality={selected.quality_label} height={selected.height}"
        )
    elif (pa or da) and allow_audio_only:
        audio_pool = pa if pa else da
        selected = sorted(audio_pool, key=_audio_sort_key, reverse=True)[0]
        strategy_used = SelectionStrategy.BEST_AUDIO_ONLY
        result.reason = (
            f"Selected audio-only format itag={selected.itag} "
            f"quality={selected.quality_label} (no video available)"
        )
    else:
        if pc:
            selected = sorted(pc, key=_combined_sort_key, reverse=True)[0]
            strategy_used = SelectionStrategy.FALLBACK
            fallback = True
            result.reason = (
                f"Fallback: progressive combined itag={selected.itag} "
                f"quality={selected.quality_label}"
            )
        elif dc:
            selected = sorted(dc, key=_combined_sort_key, reverse=True)[0]
            strategy_used = SelectionStrategy.FALLBACK
            fallback = True
            result.reason = (
                f"Fallback: DASH combined itag={selected.itag} "
                f"quality={selected.quality_label}"
            )
        elif progressive_video and allow_video_only:
            selected = sorted(progressive_video, key=_video_sort_key, reverse=True)[0]
            strategy_used = SelectionStrategy.FALLBACK
            fallback = True
            result.reason = (
                f"Fallback: progressive video-only itag={selected.itag} "
                f"quality={selected.quality_label}"
            )
        elif dash_video and allow_video_only:
            selected = sorted(dash_video, key=_video_sort_key, reverse=True)[0]
            strategy_used = SelectionStrategy.FALLBACK
            fallback = True
            result.reason = (
                f"Fallback: DASH video-only itag={selected.itag} "
                f"quality={selected.quality_label}"
            )
        elif audio_only:
            selected = sorted(audio_only, key=_audio_sort_key, reverse=True)[0]
            strategy_used = SelectionStrategy.FALLBACK
            fallback = True
            result.reason = (
                f"Fallback: audio-only itag={selected.itag} "
                f"quality={selected.quality_label} (no video available)"
            )
        else:
            result.reason = "No suitable format found"
            result.strategy = SelectionStrategy.FALLBACK
            _logger.warning(
                "select_format: no suitable format found for quality=%r", quality
            )
            return result

    result.format = selected
    result.strategy = strategy_used
    result.fallback_used = fallback
    result.quality_matched = selected.quality_label if selected else "none"

    _logger.info(
        "select_format: selected itag=%d quality=%s strategy=%s fallback=%s reason=%s",
        selected.itag if selected else -1,
        result.quality_matched,
        strategy_used,
        fallback,
        result.reason,
    )

    return result


# ---------------------------------------------------------------------------
# Human-readable format listing
# ---------------------------------------------------------------------------


def list_available_formats(
    streams: Sequence[StreamFormat],
    *,
    show_details: bool = False,
    group_by_type: bool = True,
) -> str:
    """Produce a human-readable summary of available stream formats.

    The output is formatted for display to end users and includes quality
    labels, codecs, resolution, and optional bitrate/size information.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.
        show_details: When ``True``, include per-format bitrate, estimated
            size, and protocol information.
        group_by_type: When ``True``, group formats by their type
            (combined, video-only, audio-only) with section headers.

    Returns:
        A formatted multi-line string suitable for console output.
    """
    if not streams:
        return "No formats available."

    lines: List[str] = []

    if group_by_type:
        groups = categorize_formats(streams)
        type_labels = {
            "combined": "Combined (audio + video)",
            "video_only": "Video-only",
            "audio_only": "Audio-only",
            "unknown": "Unknown",
        }
        for group_name in ("combined", "video_only", "audio_only", "unknown"):
            group = groups[group_name]
            if not group:
                continue
            lines.append(f"\n{type_labels[group_name]} ({len(group)}):")
            lines.append("-" * 60)
            sorted_group = sorted(group, key=_combined_sort_key, reverse=True)
            for i, fmt in enumerate(sorted_group, 1):
                lines.append(_format_single(fmt, index=i, show_details=show_details))
    else:
        sorted_streams = sorted(streams, key=_combined_sort_key, reverse=True)
        lines.append(f"Available formats ({len(sorted_streams)}):")
        lines.append("-" * 60)
        for i, fmt in enumerate(sorted_streams, 1):
            lines.append(_format_single(fmt, index=i, show_details=show_details))

    return "\n".join(lines)


def _format_single(
    fmt: StreamFormat,
    *,
    index: int = 0,
    show_details: bool = False,
) -> str:
    """Format a single :class:`StreamFormat` as a human-readable line.

    Args:
        fmt: The format to format.
        index: 1-based index for display.
        show_details: When ``True``, include extra fields.

    Returns:
        A formatted string describing the format.
    """
    parts: List[str] = []
    if index:
        parts.append(f"[{index}]")

    parts.append(f"itag={fmt.itag}")

    if fmt.quality_label:
        parts.append(fmt.quality_label)

    if fmt.height and fmt.width:
        parts.append(f"{fmt.width}x{fmt.height}")
    elif fmt.height:
        parts.append(f"{fmt.height}p")

    if fmt.fps:
        parts.append(f"@{fmt.fps}fps")

    if fmt.vcodec and fmt.vcodec != "none":
        parts.append(f"vcodec={fmt.vcodec}")

    if fmt.acodec and fmt.acodec != "none":
        parts.append(f"acodec={fmt.acodec}")

    if show_details:
        if fmt.tbr is not None:
            parts.append(f"tbr={fmt.tbr:.0f}kbps")
        if fmt.abr is not None:
            parts.append(f"abr={fmt.abr:.0f}kbps")
        if fmt.vbr is not None:
            parts.append(f"vbr={fmt.vbr:.0f}kbps")
        if fmt.estimated_size:
            size_mb = fmt.estimated_size / (1024 * 1024)
            parts.append(f"~{size_mb:.1f}MB")
        if fmt.protocol:
            parts.append(f"protocol={fmt.protocol}")
        if fmt.ext:
            parts.append(f"ext={fmt.ext}")
        if fmt.is_dash:
            parts.append("(DASH)")
        elif fmt.is_hls:
            parts.append("(HLS)")
        else:
            parts.append("(progressive)")

    type_label = "combined" if fmt.is_combined else (
        "video-only" if fmt.is_video_only else (
            "audio-only" if fmt.is_audio_only else "unknown"
        )
    )
    parts.append(f"({type_label})")

    return "  " + " ".join(parts)


# ---------------------------------------------------------------------------
# Edge-case selection helpers
# ---------------------------------------------------------------------------


def _select_for_live_stream(
    streams: Sequence[StreamFormat],
) -> Optional[StreamFormat]:
    """Select the best format for a live stream.

    Live streams typically only offer a limited set of formats, often
    DASH-based.  This function prefers the highest-bitrate combined
    DASH format, falling back to the best video-only DASH stream plus
    the best audio-only stream.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.

    Returns:
        The best :class:`StreamFormat` for live playback, or ``None``.
    """
    combined, video_only, audio_only = _categorize_streams(streams)
    dash_combined = [f for f in combined if f.is_dash or f.is_hls]
    dash_video = [f for f in video_only if f.is_dash or f.is_hls]

    if dash_combined:
        best = sorted(dash_combined, key=_combined_sort_key, reverse=True)[0]
        _logger.info(
            "_select_for_live_stream: selected DASH combined itag=%d quality=%s",
            best.itag,
            best.quality_label,
        )
        return best

    if dash_video and audio_only:
        best_v = sorted(dash_video, key=_video_sort_key, reverse=True)[0]
        best_a = sorted(audio_only, key=_audio_sort_key, reverse=True)[0]
        _logger.info(
            "_select_for_live_stream: selected DASH pair video_itag=%d audio_itag=%d",
            best_v.itag,
            best_a.itag,
        )
        return best_v

    all_streams = list(dash_combined) + list(dash_video) + list(audio_only)
    if all_streams:
        best = sorted(all_streams, key=_combined_sort_key, reverse=True)[0]
        _logger.info(
            "_select_for_live_stream: fallback itag=%d quality=%s",
            best.itag,
            best.quality_label,
        )
        return best

    _logger.warning("_select_for_live_stream: no suitable live format found")
    return None


def _select_for_audio_only_request(
    streams: Sequence[StreamFormat],
    quality: str = "best",
) -> Optional[StreamFormat]:
    """Select the best audio-only format from *streams*.

    This is used internally when the caller has explicitly requested
    audio-only output or when no video format is available.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.
        quality: Quality selection string.

    Returns:
        The best audio-only :class:`StreamFormat`, or ``None``.
    """
    _, _, audio_only = _categorize_streams(streams)
    if not audio_only:
        _logger.debug("_select_for_audio_only_request: no audio-only formats")
        return None

    kind, value = _parse_quality_label(quality)
    if kind == "resolution" and value is not None:
        candidates = [
            f for f in audio_only
            if (f.abr or 0) > 0
        ]
    else:
        candidates = list(audio_only)

    if not candidates:
        return None

    best = sorted(candidates, key=_audio_sort_key, reverse=True)[0]
    _logger.info(
        "_select_for_audio_only_request: selected itag=%d abr=%s",
        best.itag,
        best.abr,
    )
    return best


def _select_preferred_container(
    formats: Sequence[StreamFormat],
    preferred_containers: Optional[List[str]] = None,
) -> List[StreamFormat]:
    """Filter *formats* to those with a preferred container format.

    When *preferred_containers* is ``None`` or empty, all formats are
    returned unchanged.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.
        preferred_containers: Ordered list of container strings.  Formats
            whose container matches earlier entries in the list are
            preferred.

    Returns:
        A list of :class:`StreamFormat` objects sorted by container
        preference.
    """
    if not preferred_containers:
        return list(formats)

    container_rank = {c: i for i, c in enumerate(preferred_containers)}
    ranked = []
    unranked = []
    for fmt in formats:
        container = fmt.vcontainer or fmt.acontainer or ""
        if container in container_rank:
            ranked.append((container_rank[container], fmt))
        else:
            unranked.append(fmt)

    ranked.sort(key=lambda x: x[0])
    result = [f for _, f in ranked] + unranked
    _logger.debug(
        "_select_preferred_container: preferred=%s ranked=%d unranked=%d",
        preferred_containers,
        len(ranked),
        len(unranked),
    )
    return result


def _apply_codec_preference(
    formats: Sequence[StreamFormat],
    preferred_vcodecs: Optional[List[str]] = None,
    preferred_acodecs: Optional[List[str]] = None,
) -> List[StreamFormat]:
    """Sort *formats* by codec preference.

    Formats using preferred codecs are moved to the front of the list.
    When *preferred_vcodecs* or *preferred_acodecs* is ``None`` the
    corresponding codec preference is not applied.

    Args:
        formats: Input sequence of :class:`StreamFormat` objects.
        preferred_vcodecs: Ordered list of preferred video codecs.
        preferred_acodecs: Ordered list of preferred audio codecs.

    Returns:
        A new list of formats sorted by codec preference.
    """
    if not preferred_vcodecs and not preferred_acodecs:
        return list(formats)

    vcodec_rank = {c: i for i, c in enumerate(preferred_vcodecs or [])}
    acodec_rank = {c: i for i, c in enumerate(preferred_acodecs or [])}

    def codec_rank(fmt: StreamFormat) -> Tuple[int, int, int]:
        v_rank = vcodec_rank.get(fmt.vcodec, 999) if fmt.vcodec else 999
        a_rank = acodec_rank.get(fmt.acodec, 999) if fmt.acodec else 999
        return (v_rank, a_rank, fmt.itag)

    return sorted(formats, key=codec_rank)


def select_format_with_preferences(
    streams: Sequence[StreamFormat],
    quality: str = "best",
    allow_audio_only: bool = False,
    allow_video_only: bool = False,
    preferred_containers: Optional[List[str]] = None,
    preferred_vcodecs: Optional[List[str]] = None,
    preferred_acodecs: Optional[List[str]] = None,
) -> FormatSelectionResult:
    """Extended format selection with container and codec preferences.

    This function is identical to :func:`select_format` but additionally
    applies container and codec preference filters before selecting the
    final format.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.
        quality: Quality selection string.
        allow_audio_only: Whether audio-only formats are eligible.
        allow_video_only: Whether video-only formats are eligible.
        preferred_containers: Ordered list of preferred container formats.
        preferred_vcodecs: Ordered list of preferred video codecs.
        preferred_acodecs: Ordered list of preferred audio codecs.

    Returns:
        A :class:`FormatSelectionResult` describing the chosen format.
    """
    if not streams:
        raise FormatSelectionError(
            "Cannot select format from an empty stream list"
        )

    filtered = list(streams)

    if preferred_containers:
        filtered = _select_preferred_container(filtered, preferred_containers)

    if preferred_vcodecs or preferred_acodecs:
        filtered = _apply_codec_preference(
            filtered,
            preferred_vcodecs=preferred_vcodecs,
            preferred_acodecs=preferred_acodecs,
        )

    _logger.debug(
        "select_format_with_preferences: after preference filtering %d formats",
        len(filtered),
    )

    return select_format(
        filtered,
        quality=quality,
        allow_audio_only=allow_audio_only,
        allow_video_only=allow_video_only,
    )


# ---------------------------------------------------------------------------
# Format validation for selection
# ---------------------------------------------------------------------------


def _is_format_selectable(fmt: StreamFormat) -> bool:
    """Check whether a format is a valid candidate for selection.

    A format is selectable when it has a valid itag and at least one
    stream type (audio or video) is present.

    Args:
        fmt: The :class:`StreamFormat` to evaluate.

    Returns:
        ``True`` when the format can be selected for download.
    """
    if fmt.itag <= 0:
        return False
    if not fmt.is_combined and not fmt.is_video_only and not fmt.is_audio_only:
        return False
    return True


def filter_selectable(
    streams: Sequence[StreamFormat],
) -> List[StreamFormat]:
    """Return only formats that are valid candidates for selection.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.

    Returns:
        A filtered list of selectable :class:`StreamFormat` objects.
    """
    result = [f for f in streams if _is_format_selectable(f)]
    _logger.debug(
        "filter_selectable: %d/%d formats are selectable",
        len(result),
        len(streams),
    )
    return result


# ---------------------------------------------------------------------------
# Batch selection utilities
# ---------------------------------------------------------------------------


def select_formats_batch(
    stream_lists: Sequence[Sequence[StreamFormat]],
    quality: str = "best",
    allow_audio_only: bool = False,
    allow_video_only: bool = False,
) -> List[FormatSelectionResult]:
    """Apply :func:`select_format` to multiple format lists.

    This is a convenience wrapper for batch-processing multiple videos.

    Args:
        stream_lists: A sequence of format lists, one per video.
        quality: Quality selection string.
        allow_audio_only: Whether audio-only formats are eligible.
        allow_video_only: Whether video-only formats are eligible.

    Returns:
        A list of :class:`FormatSelectionResult` objects, one per input
        list.  Entries corresponding to empty input lists contain a
        result with ``format=None`` and ``reason`` set to an error
        message.
    """
    results: List[FormatSelectionResult] = []
    for i, streams in enumerate(stream_lists, 1):
        if not streams:
            result = FormatSelectionResult(
                quality_requested=quality,
                reason=f"Empty stream list at index {i}",
                strategy=SelectionStrategy.FALLBACK,
            )
            _logger.warning("select_formats_batch: empty stream list at index %d", i)
        else:
            result = select_format(
                streams,
                quality=quality,
                allow_audio_only=allow_audio_only,
                allow_video_only=allow_video_only,
            )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Quality string helpers
# ---------------------------------------------------------------------------


def is_valid_quality(quality_str: str) -> bool:
    """Return ``True`` when *quality_str* is a recognised quality label.

    Recognised formats are ``"best"``, ``"worst"``, itag numbers, and
    resolution strings such as ``"720p"``.

    Args:
        quality_str: The quality string to validate.

    Returns:
        ``True`` when the string is a valid quality specifier.
    """
    kind, _ = _parse_quality_label(quality_str)
    return kind != "unknown"


def normalise_quality(quality_str: str) -> str:
    """Normalise a quality string to a canonical form.

    The canonical form is lowercase with a trailing ``"p"`` for resolution
    strings (e.g. ``"720P"`` becomes ``"720p"``).  ``"best"`` and
    ``"worst"`` are returned as-is.  Non-standard strings are returned
    unchanged.

    Args:
        quality_str: The quality string to normalise.

    Returns:
        The normalised quality string.
    """
    kind, value = _parse_quality_label(quality_str)
    if kind == "resolution" and value is not None:
        return f"{value}p"
    return quality_str.strip().lower()


def get_quality_rank(quality_str: str) -> int:
    """Return an integer rank for a quality string for sorting purposes.

    Higher ranks indicate higher quality.  ``"best"`` returns the maximum
    possible rank.  ``"worst"`` returns ``0``.

    Args:
        quality_str: The quality string to rank.

    Returns:
        An integer quality rank.
    """
    kind, value = _parse_quality_label(quality_str)
    if kind == "best":
        return 99999
    if kind == "worst":
        return 0
    if kind == "resolution" and value is not None:
        return value * 100
    if kind == "itag" and value is not None:
        return value
    return 0


# ---------------------------------------------------------------------------
# Debug / diagnostic helpers
# ---------------------------------------------------------------------------


def describe_selection(
    result: FormatSelectionResult,
) -> str:
    """Produce a human-readable description of a :class:`FormatSelectionResult`.

    Args:
        result: The selection result to describe.

    Returns:
        A multi-line string describing the selection outcome.
    """
    lines = [
        f"Selection result:",
        f"  Strategy:   {result.strategy}",
        f"  Reason:     {result.reason}",
        f"  Fallback:   {result.fallback_used}",
        f"  Quality:    {result.quality_requested} -> {result.quality_matched}",
        f"  Candidates: {result.candidates_considered} total "
        f"({result.candidates_combined} combined, "
        f"{result.candidates_video_only} video-only, "
        f"{result.candidates_audio_only} audio-only, "
        f"{result.candidates_dash} dash, "
        f"{result.candidates_progressive} progressive)",
    ]
    if result.format:
        lines.append(f"  Selected:   {result.format!r}")
    else:
        lines.append("  Selected:   <none>")
    return "\n".join(lines)


def get_selection_summary(
    streams: Sequence[StreamFormat],
    quality: str = "best",
    allow_audio_only: bool = False,
    allow_video_only: bool = False,
) -> Dict[str, Any]:
    """Produce a summary dictionary for a hypothetical selection without
    mutating any state.

    This is useful for diagnostic logging and testing.

    Args:
        streams: Input sequence of :class:`StreamFormat` objects.
        quality: Quality selection string.
        allow_audio_only: Whether audio-only formats are eligible.
        allow_video_only: Whether video-only formats are eligible.

    Returns:
        A dictionary with selection-relevant statistics.
    """
    combined, video_only, audio_only = _categorize_streams(streams)
    attrs = _count_stream_attributes(streams)
    best_combined = _select_best_combined(streams)
    best_video = _select_best_video(streams)
    best_audio = _select_best_audio(streams)

    return {
        "total_streams": len(streams),
        "combined": len(combined),
        "video_only": len(video_only),
        "audio_only": len(audio_only),
        "dash": attrs["dash"],
        "progressive": attrs["progressive"],
        "best_combined": best_combined.to_dict() if best_combined else None,
        "best_video_only": best_video.to_dict() if best_video else None,
        "best_audio_only": best_audio.to_dict() if best_audio else None,
        "available_qualities": sorted(
            {f.quality_label for f in streams if f.quality_label}
        ),
        "available_heights": sorted({f.height for f in streams if f.height}),
        "is_valid_quality": is_valid_quality(quality),
        "normalised_quality": normalise_quality(quality),
        "quality_rank": get_quality_rank(quality),
    }
