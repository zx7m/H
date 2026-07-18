"""
VideoInfo data class - the central data model tying together all YouTube video
metadata, stream formats, and caption information.

This module provides the :class:`VideoInfo` class which acts as the primary
data transfer object for video information throughout the ``ytdownloader``
package.  It is constructed from a raw YouTube player response dict via
:meth:`VideoInfo.from_player_response`, or directly via the constructor, and
provides convenient accessors for format selection, thumbnail lookup, and
metadata serialisation.

Typical usage::

    from ytdownloader.video_info import VideoInfo

    info = VideoInfo.from_player_response(player_response)
    print(info.title, info.author)
    best = info.get_best_format("1080p")
    info.to_dict()  # serialize to plain dict
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from ytdownloader.streaming_data import StreamFormat, get_best_format, get_audio_only_formats, get_video_only_formats, select_formats_by_quality, sort_formats
from ytdownloader.metadata_extractor import (
    build_metadata_dict,
    extract_upload_date,
    format_duration,
    format_view_count,
    format_upload_date,
    format_file_size,
    format_description_preview,
)
from ytdownloader.subtitle_parser import SubtitleTrack, get_caption_tracks
from ytdownloader.player_response import (
    extract_playability_status,
    extract_streaming_data,
    extract_video_details,
    validate_player_response,
)
from ytdownloader.exceptions import (
    MetadataExtractionError,
    StreamResolutionError,
    VideoUnavailableError,
)
from ytdownloader.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "VideoInfo",
    "VideoInfoError",
]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default thumbnail qualities in preference order (best first).
_THUMBNAIL_QUALITY_ORDER: List[str] = [
    "maxres",
    "standard",
    "high",
    "medium",
    "default",
]

#: Acceptable quality alias mappings.
_QUALITY_ALIASES: Dict[str, str] = {
    "best": "best",
    "worst": "worst",
    "highest": "best",
    "lowest": "worst",
    "1080p": "1080p",
    "720p": "720p",
    "480p": "480p",
    "360p": "360p",
    "240p": "240p",
    "144p": "144p",
    "hd": "720p",
    "sd": "480p",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VideoInfoError(Exception):
    """Raised when VideoInfo construction or operation fails."""


# ---------------------------------------------------------------------------
# VideoInfo class
# ---------------------------------------------------------------------------


class VideoInfo:
    """Central data class representing all known information about a YouTube video.

    :class:`VideoInfo` ties together video metadata (title, author, view count,
    etc.), stream formats, caption tracks, and playability status into a single
    object that can be queried, filtered, and serialised.

    Attributes:
        video_id: 11-character YouTube video identifier.
        title: Video title string.
        author: Channel display name.
        channel_id: Channel ID (starts with ``"UC"``).
        duration: Duration in seconds.
        view_count: Total view count.
        like_count: Like count.
        description: Full video description text.
        thumbnail_urls: List of thumbnail dicts with ``url``, ``width``,
            ``height``, and ``quality`` keys.
        keywords: List of keyword / tag strings.
        is_live: ``True`` if the video is a live broadcast.
        is_private: ``True`` if the video is private.
        upload_date: Upload date as a ``YYYYMMDD`` string.
        formats: List of progressive (combined audio+video) :class:`StreamFormat` objects.
        adaptive_formats: List of adaptive (audio-only or video-only) :class:`StreamFormat` objects.
        captions: List of :class:`SubtitleTrack` objects.
        playability_status: Dict with ``status``, ``reason``, and ``errorScreen`` keys.
    """

    __slots__ = (
        "video_id",
        "title",
        "author",
        "channel_id",
        "duration",
        "view_count",
        "like_count",
        "description",
        "thumbnail_urls",
        "keywords",
        "is_live",
        "is_private",
        "upload_date",
        "formats",
        "adaptive_formats",
        "captions",
        "playability_status",
        "_all_formats",
    )

    def __init__(
        self,
        video_id: Optional[str],
        title: Optional[str],
        author: Optional[str],
        channel_id: Optional[str],
        duration: Optional[int],
        view_count: Optional[int],
        like_count: Optional[int],
        description: Optional[str],
        thumbnail_urls: Optional[List[Dict[str, Any]]],
        keywords: Optional[List[str]],
        is_live: bool,
        is_private: bool,
        upload_date: Optional[str],
        formats: Optional[List[StreamFormat]],
        adaptive_formats: Optional[List[StreamFormat]],
        captions: Optional[List[SubtitleTrack]],
        playability_status: Optional[Dict[str, Any]],
    ) -> None:
        self.video_id: Optional[str] = video_id
        self.title: Optional[str] = title
        self.author: Optional[str] = author
        self.channel_id: Optional[str] = channel_id
        self.duration: Optional[int] = duration
        self.view_count: Optional[int] = view_count
        self.like_count: Optional[int] = like_count
        self.description: Optional[str] = description
        self.thumbnail_urls: List[Dict[str, Any]] = thumbnail_urls or []
        self.keywords: List[str] = keywords or []
        self.is_live: bool = is_live
        self.is_private: bool = is_private
        self.upload_date: Optional[str] = upload_date
        self.formats: List[StreamFormat] = formats or []
        self.adaptive_formats: List[StreamFormat] = adaptive_formats or []
        self.captions: List[SubtitleTrack] = captions or []
        self.playability_status: Dict[str, Any] = playability_status or {}
        self._all_formats: Optional[List[StreamFormat]] = None

        logger.debug(
            "VideoInfo initialised: video_id=%s title=%r formats=%d adaptive=%d captions=%d",
            video_id,
            title,
            len(self.formats),
            len(self.adaptive_formats),
            len(self.captions),
        )

    # ------------------------------------------------------------------
    # Class constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_player_response(cls, player_response: Dict[str, Any]) -> VideoInfo:
        """Build a :class:`VideoInfo` from a raw YouTube player response dict.

        The player response is the JavaScript ``ytInitialPlayerResponse``
        object embedded in a YouTube watch page.  This method validates the
        response, then extracts all metadata, stream formats, captions, and
        playability status into a single :class:`VideoInfo` instance.

        Args:
            player_response: The raw dict decoded from
                ``ytInitialPlayerResponse``.

        Returns:
            A fully populated :class:`VideoInfo` instance.

        Raises:
            TypeError: If *player_response* is not a dict.
            VideoUnavailableError: If the video is not playable.
            MetadataExtractionError: If video details are missing or empty.
            StreamResolutionError: If streaming data is missing.
        """
        if not isinstance(player_response, dict):
            raise TypeError(
                f"from_player_response expected a dict, got {type(player_response).__name__}"
            )

        logger.debug("from_player_response: starting construction")

        validate_player_response(player_response)

        video_details = extract_video_details(player_response)

        metadata = build_metadata_dict(video_details)

        streaming_raw = extract_streaming_data(player_response)

        from ytdownloader.streaming_data import parse_streaming_data

        all_parsed = parse_streaming_data(streaming_raw)
        progressive: List[StreamFormat] = []
        adaptive: List[StreamFormat] = []
        for sf in all_parsed:
            if sf.is_combined:
                progressive.append(sf)
            else:
                adaptive.append(sf)

        raw_captions = player_response.get("captions", {})
        captions: List[SubtitleTrack] = get_caption_tracks(player_response)

        playability = extract_playability_status(player_response)

        info = cls(
            video_id=metadata.get("video_id"),
            title=metadata.get("title"),
            author=metadata.get("author"),
            channel_id=metadata.get("channel_id"),
            duration=metadata.get("duration"),
            view_count=metadata.get("view_count"),
            like_count=metadata.get("like_count"),
            description=metadata.get("description"),
            thumbnail_urls=metadata.get("thumbnail_urls", []),
            keywords=metadata.get("keywords", []),
            is_live=metadata.get("is_live", False),
            is_private=metadata.get("is_private", False),
            upload_date=metadata.get("upload_date"),
            formats=progressive,
            adaptive_formats=adaptive,
            captions=captions,
            playability_status=playability,
        )

        logger.info(
            "from_player_response: built VideoInfo for %r (formats=%d, adaptive=%d)",
            info.title,
            len(info.formats),
            len(info.adaptive_formats),
        )
        return info

    # ------------------------------------------------------------------
    # Combined format cache
    # ------------------------------------------------------------------

    def _get_all_formats(self) -> List[StreamFormat]:
        if self._all_formats is None:
            self._all_formats = list(self.formats) + list(self.adaptive_formats)
        return self._all_formats

    # ------------------------------------------------------------------
    # Format selection
    # ------------------------------------------------------------------

    def get_best_format(self, quality: str = "best") -> Optional[StreamFormat]:
        """Return the best available format for the given quality.

        Selection logic:
        1. Resolve *quality* aliases (e.g. ``"hd"`` → ``"720p"``).
        2. Try the combined (progressive) formats first, then fall back to
           all available formats (including DASH adaptive formats).
        3. Within the chosen pool, sort by quality descending and return the
           top result.

        Args:
            quality: Quality preference string.  Supported values:

                * ``"best"`` (default): highest quality available.
                * ``"worst"``: lowest quality available.
                * ``"<N>p"`` such as ``"1080p"``: highest quality at or
                  below the given resolution.
                * An itag number as a string (e.g. ``"18"``).

        Returns:
            The best matching :class:`StreamFormat`, or ``None`` if no
            formats are available.
        """
        resolved = _QUALITY_ALIASES.get(quality, quality)
        all_fmts = self._get_all_formats()
        if not all_fmts:
            logger.warning("get_best_format: no formats available")
            return None

        combined_pool = list(self.formats) if self.formats else []
        full_pool = all_fmts

        pool = combined_pool if combined_pool else full_pool

        try:
            candidates = select_formats_by_quality(pool, quality=resolved)
        except Exception as exc:
            logger.warning("get_best_format: select_formats_by_quality failed: %s", exc)
            candidates = sort_formats(pool, key="quality")

        if candidates:
            return candidates[0]

        logger.warning(
            "get_best_format: no format matched quality=%r", resolved
        )
        return None

    def get_audio_only(self) -> Optional[StreamFormat]:
        """Return the best audio-only format.

        Selects from ``adaptive_formats`` first, then falls back to the
        audio component of combined formats if no pure audio-only formats
        are available.

        Returns:
            The best :class:`StreamFormat` that carries audio only, or
            ``None`` if none is available.
        """
        candidates = get_audio_only_formats(self.adaptive_formats)
        if not candidates:
            candidates = [
                f for f in self.formats if f.is_audio_only
            ]
        if candidates:
            return sort_formats(candidates, key="quality")[0]
        logger.warning("get_audio_only: no audio-only format available")
        return None

    def get_video_only(self) -> Optional[StreamFormat]:
        """Return the best video-only format.

        Selects from ``adaptive_formats`` first, then falls back to the
        video component of combined formats if no pure video-only formats
        are available.

        Returns:
            The best :class:`StreamFormat` that carries video only, or
            ``None`` if none is available.
        """
        candidates = get_video_only_formats(self.adaptive_formats)
        if not candidates:
            candidates = [f for f in self.formats if f.is_video_only]
        if candidates:
            return sort_formats(candidates, key="quality")[0]
        logger.warning("get_video_only: no video-only format available")
        return None

    # ------------------------------------------------------------------
    # Thumbnail access
    # ------------------------------------------------------------------

    def get_thumbnail_url(self, quality: str = "default") -> Optional[str]:
        """Return the thumbnail URL for the requested quality.

        The *quality* argument can be any of:

        * ``"default"`` (default): returns the first available thumbnail.
        * ``"maxres"``, ``"standard"``, ``"high"``, ``"medium"``: returns
          the first thumbnail matching that quality label.
        * Any other string: performs a case-insensitive partial match
          against the ``quality`` field of each thumbnail entry.

        Args:
            quality: Thumbnail quality label.

        Returns:
            The URL string of the best matching thumbnail, or ``None`` if
            no thumbnails are available.
        """
        if not self.thumbnail_urls:
            logger.debug("get_thumbnail_url: no thumbnails available")
            return None

        quality_lower = quality.lower().strip()

        if quality_lower == "default":
            first = self.thumbnail_urls[0]
            return first.get("url")

        for thumb in self.thumbnail_urls:
            thumb_quality = (thumb.get("quality") or "").lower()
            if thumb_quality == quality_lower:
                url = thumb.get("url")
                if url:
                    return url

        for preferred in _THUMBNAIL_QUALITY_ORDER:
            if preferred.startswith(quality_lower) or quality_lower.startswith(preferred):
                for thumb in self.thumbnail_urls:
                    if (thumb.get("quality") or "").lower() == preferred:
                        url = thumb.get("url")
                        if url:
                            return url

        for thumb in self.thumbnail_urls:
            if quality_lower in (thumb.get("quality") or "").lower():
                url = thumb.get("url")
                if url:
                    return url

        return self.thumbnail_urls[0].get("url")

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def has_captions(self) -> bool:
        """Return ``True`` if caption / subtitle tracks are available.

        Returns:
            ``True`` when at least one :class:`SubtitleTrack` is present.
        """
        return bool(self.captions)

    def is_live(self) -> bool:
        """Return ``True`` if the video is a live broadcast.

        The ``is_live`` flag is set from the ``videoDetails.isLive`` field
        in the YouTube player response.

        Returns:
            ``True`` if this is a live stream.
        """
        return bool(self.is_live)

    def is_playable(self) -> bool:
        """Return ``True`` if the video is currently playable.

        A video is considered playable when the ``playability_status``
        dict reports a status of ``"OK"`` or
        ``"LIVE_STREAM_OFFLINE_WITH_CONTENT"``.

        Returns:
            ``True`` when the video can be played.
        """
        status = (self.playability_status or {}).get("status", "ERROR")
        return status in ("OK", "LIVE_STREAM_OFFLINE_WITH_CONTENT")

    def get_duration_str(self) -> Optional[str]:
        """Return the video duration as a human-readable ``HH:MM:SS`` string.

        Durations shorter than one minute omit the hour component and are
        formatted as ``MM:SS``.

        Returns:
            A formatted duration string, or ``None`` when the duration is
            not known.
        """
        if self.duration is None:
            return None
        return format_duration(self.duration)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this :class:`VideoInfo` to a plain dictionary.

        All attributes are included.  Stream format lists are converted via
        each format's :meth:`~StreamFormat.to_dict` method.  Caption tracks
        are represented as plain dicts.

        Returns:
            A dictionary containing every field of this :class:`VideoInfo`.
        """
        return {
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "channel_id": self.channel_id,
            "duration": self.duration,
            "duration_str": self.get_duration_str(),
            "view_count": self.view_count,
            "view_count_str": format_view_count(self.view_count),
            "like_count": self.like_count,
            "description": self.description,
            "description_preview": format_description_preview(self.description),
            "thumbnail_urls": self.thumbnail_urls,
            "thumbnail_url": self.get_thumbnail_url(),
            "keywords": self.keywords,
            "is_live": self.is_live,
            "is_private": self.is_private,
            "upload_date": self.upload_date,
            "upload_date_str": format_upload_date(self.upload_date),
            "formats": [fmt.to_dict() for fmt in self.formats],
            "adaptive_formats": [fmt.to_dict() for fmt in self.adaptive_formats],
            "captions": [
                {
                    "url": track.url,
                    "lang": track.lang,
                    "lang_code": track.lang_code,
                    "is_auto": track.is_auto,
                    "is_translated": track.is_translated,
                    "kind": track.kind,
                }
                for track in self.captions
            ],
            "playability_status": self.playability_status,
            "is_playable": self.is_playable(),
            "has_captions": self.has_captions(),
            "format_count": len(self.formats) + len(self.adaptive_formats),
            "best_format": self.get_best_format().to_dict() if self.get_best_format() else None,
            "audio_only_format": self.get_audio_only().to_dict() if self.get_audio_only() else None,
            "video_only_format": self.get_video_only().to_dict() if self.get_video_only() else None,
        }

    # ------------------------------------------------------------------
    # Human-readable representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        duration_str = self.get_duration_str() or "unknown"
        view_str = format_view_count(self.view_count) or "0 views"
        title = self.title or self.video_id or "unknown"
        parts = [
            f"VideoInfo(video_id={self.video_id!r}",
            f"title={title!r}",
            f"author={self.author!r}",
            f"duration={duration_str!r}",
            f"views={view_str!r}",
        ]
        if self.is_live:
            parts.append("LIVE")
        if self.is_private:
            parts.append("PRIVATE")
        if not self.is_playable():
            status = (self.playability_status or {}).get("status", "UNKNOWN")
            parts.append(f"status={status!r}")
        parts.append(f"formats={len(self.formats) + len(self.adaptive_formats)}")
        if self.captions:
            parts.append(f"captions={len(self.captions)}")
        return f"{' '.join(parts)})"

    # ------------------------------------------------------------------
    # Summary helper
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Return a compact summary dict suitable for display.

        Unlike :meth:`to_dict`, the summary only includes the most
        important fields and omits large lists such as raw format dicts.

        Returns:
            A compact summary dictionary.
        """
        return {
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "channel_id": self.channel_id,
            "duration": self.duration,
            "duration_str": self.get_duration_str(),
            "view_count": self.view_count,
            "view_count_str": format_view_count(self.view_count),
            "like_count": self.like_count,
            "upload_date": self.upload_date,
            "upload_date_str": format_upload_date(self.upload_date),
            "is_live": self.is_live,
            "is_private": self.is_private,
            "is_playable": self.is_playable(),
            "has_captions": self.has_captions(),
            "format_count": len(self.formats) + len(self.adaptive_formats),
            "best_quality": self.get_best_format().quality_label if self.get_best_format() else None,
            "thumbnail_url": self.get_thumbnail_url(),
            "description_preview": format_description_preview(self.description),
            "playability_status": (self.playability_status or {}).get("status"),
        }

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def get_available_qualities(self) -> List[str]:
        """Return a sorted list of distinct quality labels across all formats.

        Returns:
            A sorted list of unique quality label strings (e.g.
            ``["1080p", "720p", "480p"]``).
        """
        labels = {f.quality_label for f in self._get_all_formats() if f.quality_label}
        return sorted(labels)

    def get_available_heights(self) -> List[int]:
        """Return a sorted list of distinct video heights across all formats.

        Returns:
            A sorted list of unique pixel height integers.
        """
        heights = {f.height for f in self._get_all_formats() if f.height}
        return sorted(heights)

    def get_available_extensions(self) -> List[str]:
        """Return a sorted list of distinct file extensions across all formats.

        Returns:
            A sorted list of unique extension strings.
        """
        from ytdownloader.streaming_data import get_available_extensions
        return get_available_extensions(self._get_all_formats())

    def get_format_by_itag(self, itag: int) -> Optional[StreamFormat]:
        """Find a format by its YouTube itag number.

        Searches both progressive and adaptive format lists.

        Args:
            itag: YouTube format identifier to search for.

        Returns:
            The first matching :class:`StreamFormat`, or ``None``.
        """
        from ytdownloader.streaming_data import get_format_by_itag
        result = get_format_by_itag(self.formats, itag)
        if result is None:
            result = get_format_by_itag(self.adaptive_formats, itag)
        return result

    def filter_formats(
        self,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        containers: Optional[Union[List[str], str]] = None,
        vcodecs: Optional[Union[List[str], str]] = None,
        acodecs: Optional[Union[List[str], str]] = None,
    ) -> List[StreamFormat]:
        """Filter available formats by one or more criteria.

        All supplied criteria are combined with logical AND.

        Args:
            min_height: Minimum video height in pixels (inclusive).
            max_height: Maximum video height in pixels (inclusive).
            containers: Allowed container format strings.
            vcodecs: Allowed video codec strings.
            acodecs: Allowed audio codec strings.

        Returns:
            A filtered list of :class:`StreamFormat` objects.
        """
        from ytdownloader.streaming_data import filter_formats
        return filter_formats(
            self._get_all_formats(),
            min_height=min_height,
            max_height=max_height,
            containers=containers,
            vcodecs=vcodecs,
            acodecs=acodecs,
        )

    def get_download_size_estimate(self) -> Optional[int]:
        """Return the estimated total download size in bytes for the best format.

        Returns:
            Estimated file size in bytes, or ``None`` if the best format
            has no size information.
        """
        best = self.get_best_format()
        if best is None:
            return None
        return best.estimated_size

    def get_download_size_str(self) -> Optional[str]:
        """Return the estimated download size as a human-readable string.

        Returns:
            A string such as ``"1.46 MB"``, or ``None`` if unavailable.
        """
        size = self.get_download_size_estimate()
        if size is None:
            return None
        return format_file_size(size)

    def list_captions(self) -> List[Dict[str, Any]]:
        """Return a human-readable list of available caption tracks.

        Returns:
            A list of dicts with keys ``index``, ``lang_code``, ``lang``,
            ``is_auto``, ``is_translated``, and ``kind``.
        """
        from ytdownloader.subtitle_parser import list_available_tracks
        return list_available_tracks(self.captions)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        return bool(self.video_id or self.title)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VideoInfo):
            return NotImplemented
        return self.video_id == other.video_id and self.video_id is not None

    def __hash__(self) -> int:
        return hash(self.video_id) if self.video_id else id(self)

    def __len__(self) -> int:
        return len(self._get_all_formats())

    def __contains__(self, item: object) -> bool:
        if isinstance(item, StreamFormat):
            return item in self._get_all_formats()
        if isinstance(item, int):
            return self.get_format_by_itag(item) is not None
        return False

    def __iter__(self):
        return iter(self._get_all_formats())

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def all_formats(self) -> List[StreamFormat]:
        """All available formats (progressive + adaptive)."""
        return self._get_all_formats()

    @property
    def format_count(self) -> int:
        """Total number of available formats."""
        return len(self._get_all_formats())

    @property
    def best_format(self) -> Optional[StreamFormat]:
        """The best available format (shortcut for ``get_best_format()``)."""
        return self.get_best_format()

    @property
    def thumbnail_url(self) -> Optional[str]:
        """The default thumbnail URL (shortcut for ``get_thumbnail_url()``)."""
        return self.get_thumbnail_url()

    @property
    def duration_str(self) -> Optional[str]:
        """Human-readable duration string (shortcut for ``get_duration_str()``)."""
        return self.get_duration_str()
