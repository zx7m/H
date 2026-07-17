"""
Stream URL resolver for YouTube streaming data.

Given a YouTube watch page HTML or ``ytInitialPlayerResponse`` dict this module
parses ``streamingData.formats`` and ``streamingData.adaptiveFormats``, resolves
any cipher and n-parameter on the stream URLs, and returns a normalised list of
:class:`StreamFormat` objects ready for download or quality selection.

Public API
----------
    - :class:`StreamFormat` — dataclass representing one available stream format.
    - :func:`parse_streaming_data` — parse all formats from a player-response dict.
    - :func:`get_best_format` — select the best format for a given quality.
    - :func:`filter_formats` / :func:`sort_formats` — collection helpers.
    - :func:`get_format_by_itag` / :func:`get_audio_only_formats` /
      :func:`get_video_only_formats` / :func:`get_combined_formats` — filters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    ITAG_DETAILS,
    ITAG_QUALITY,
    N_PARAM_NAME,
    QUALITY_HEIGHT_MAP,
)
from .exceptions import StreamResolutionError
from .cipher import decipher_url, parse_signature_cipher
from .n_resolver import resolve_n_param


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StreamFormat dataclass
# ---------------------------------------------------------------------------


@dataclass
class StreamFormat:
    """Normalised representation of a single YouTube stream format.

    Attributes:
        itag: YouTube format itag number.
        ext: File extension (e.g. ``"mp4"``, ``"webm"``).
        vcodec: Video codec name, or ``"none"`` if audio-only.
        acodec: Audio codec name, or ``"none"`` if video-only.
        width: Video width in pixels, or ``None`` for audio-only.
        height: Video height in pixels, or ``None`` for audio-only.
        fps: Frames per second, or ``None``.
        tbr: Total bitrate in kbps, or ``None``.
        abr: Audio bitrate in kbps, or ``None``.
        vbr: Video bitrate in kbps, or ``None``.
        acontainer: Audio container format.
        vcontainer: Video container format.
        mimeType: Raw MIME type string (e.g. ``"video/webm; codecs=\\"vp9\\""``).
        protocol: Streaming protocol (``"http"``, ``"https"``, ``"dash"``, ``"hls"``).
        url: Direct, resolved stream URL.
        signature_cipher: Raw ``signatureCipher`` value if present, else ``None``.
        content_length: File size in bytes, or ``None`` if unknown.
        approx_duration_ms: Approximate duration in milliseconds, or ``None``.
        is_dash: ``True`` if this is a DASH (adaptive) stream.
        is_hls: ``True`` if this is an HLS stream.
        quality_label: Human-readable quality string (e.g. ``"720p"``).
        quality_ordinal: Integer quality ranking (higher = better).
        has_video: ``True`` if the stream contains a video track.
        has_audio: ``True`` if the stream contains an audio track.
        filesize_approx: Approximate file size in bytes, or ``None``.
    """

    itag: int
    ext: str
    vcodec: str
    acodec: str
    width: Optional[int]
    height: Optional[int]
    fps: Optional[int]
    tbr: Optional[float]
    abr: Optional[float]
    vbr: Optional[float]
    acontainer: str
    vcontainer: str
    mimeType: str
    protocol: str
    url: str
    signature_cipher: Optional[str]
    content_length: Optional[int]
    approx_duration_ms: Optional[int]
    is_dash: bool
    is_hls: bool
    quality_label: str
    quality_ordinal: int
    has_video: bool
    has_audio: bool
    filesize_approx: Optional[int] = None

    @property
    def mime_type(self) -> str:
        """MIME type with underscore naming (alias for ``mimeType``)."""
        return self.mimeType

    def __repr__(self) -> str:
        return (
            f"StreamFormat(itag={self.itag}, ext={self.ext!r}, "
            f"quality={self.quality_label!r}, resolution={self.width}x{self.height}, "
            f"vcodec={self.vcodec!r}, acodec={self.acodec!r}, "
            f"has_video={self.has_video}, has_audio={self.has_audio}, "
            f"url={self.url[:80]!r}...)"
        )


# ---------------------------------------------------------------------------
# MIME parsing helpers
# ---------------------------------------------------------------------------


def _parse_mime_type(mime: str) -> Tuple[str, str, str]:
    """Parse a MIME type string into (container, vcodec, acodec).

    Args:
        mime: MIME type string such as ``"video/webm; codecs=\\"vp9\\""``.

    Returns:
        A 3-tuple of ``(container, vcodec, acodec)``.  Codec values are
        lowercased; ``"none"`` is returned when a codec is absent.
    """
    container = "unknown"
    vcodec = "none"
    acodec = "none"

    if not mime:
        return container, vcodec, acodec

    parts = mime.split(";")
    main = parts[0].strip().lower()
    if "/" in main:
        container = main.split("/")[1]

    for part in parts[1:]:
        part = part.strip()
        if part.startswith("codecs="):
            codecs_str = part[7:].strip().strip('"').strip("'")
            codecs = [c.strip() for c in codecs_str.split(",")]
            for codec in codecs:
                codec_lower = codec.lower()
                if codec_lower in ("vp8", "vp9", "avc1", "avc2", "h263", "mp4v"):
                    vcodec = codec_lower
                elif codec_lower != "none":
                    acodec = codec_lower

    return container, vcodec, acodec


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------


def _parse_content_length(value: Any) -> Optional[int]:
    """Safely convert a ``contentLength`` value to an integer.

    Args:
        value: Raw value from the format dict — typically a string but may be
            an int or ``None``.

    Returns:
        Integer byte count, or ``None`` if the value cannot be parsed.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _estimate_size(
    content_length: Optional[int],
    duration_ms: Optional[int],
    tbr: Optional[float],
) -> Optional[int]:
    """Estimate the file size when ``contentLength`` is missing.

    Uses ``duration * tbr / 8`` (tbr is in kbps, result in kB → bytes).

    Args:
        content_length: Known content length in bytes, or ``None``.
        duration_ms: Duration in milliseconds, or ``None``.
        tbr: Total bitrate in kbps, or ``None``.

    Returns:
        Estimated size in bytes, or ``None`` if the inputs are insufficient.
    """
    if content_length is not None:
        return content_length
    if duration_ms is None or tbr is None:
        return None
    try:
        seconds = duration_ms / 1000.0
        return int(seconds * tbr * 1000 / 8.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Quality helpers
# ---------------------------------------------------------------------------


def _quality_ordinal(height: Optional[int], quality_label: str) -> int:
    """Return an integer quality rank for sorting.

    Higher ordinal means higher quality.  Falls back to 0 for unknown formats.

    Args:
        height: Video height in pixels, or ``None``.
        quality_label: Quality label string such as ``"720p"``.

    Returns:
        Integer quality ordinal.
    """
    if height:
        return height
    known = QUALITY_HEIGHT_MAP.get(quality_label)
    if known:
        return known
    # audio-only: use bitrate heuristics
    return 0


def _derive_ext(mime_type: str, itag: int) -> str:
    """Derive a file extension from the MIME type or itag details.

    Args:
        mime_type: Raw MIME type string.
        itag: Format itag number (used as fallback via ITAG_DETAILS).

    Returns:
        Lower-case extension string without leading dot.
    """
    if not mime_type:
        return "mp4"
    main = mime_type.split(";")[0].strip().lower()
    mime_ext_map = {
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/x-flv": "flv",
        "video/3gpp": "3gp",
        "audio/mp4": "m4a",
        "audio/webm": "webm",
        "audio/mpeg": "mp3",
        "audio/aac": "aac",
        "application/x-mpegURL": "m3u8",
    }
    if main in mime_ext_map:
        return mime_ext_map[main]
    # Fallback to ITAG_DETAILS
    detail = ITAG_DETAILS.get(itag, {})
    return detail.get("container", "mp4")


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


def _resolve_stream_url(fmt: Dict[str, Any], js_url: Optional[str]) -> str:
    """Resolve the direct stream URL for a single format dict.

    Handles three cases:

    1. ``signatureCipher`` is present — parse, decipher the signature, and
       optionally resolve the ``n`` parameter.
    2. ``url`` is present and contains an ``n`` parameter — resolve it.
    3. ``url`` is present and needs no further processing — use as-is.

    Args:
        fmt: Raw format dict from ``streamingData.formats`` or
            ``streamingData.adaptiveFormats``.
        js_url: Player JS URL used for cipher and n-parameter resolution.
            Pass ``None`` to skip resolution (returns raw URL/cipher).

    Returns:
        A direct, usable stream URL string.

    Raises:
        StreamResolutionError: If neither ``url`` nor ``signatureCipher`` is
            present and ``js_url`` is unavailable.
    """
    cipher_value = fmt.get("signatureCipher")
    raw_url = fmt.get("url")

    if cipher_value:
        if not js_url:
            raise StreamResolutionError(
                "signatureCipher present but no js_url provided for deciphering."
            )
        try:
            deciphered = decipher_url(cipher_value, js_url)
        except Exception as exc:
            logger.warning("Cipher deciphering failed: %s", exc)
            raise StreamResolutionError(
                f"Failed to decipher signatureCipher: {exc}"
            ) from exc

        if "n=" in deciphered or "n=" in cipher_value:
            try:
                cipher_data = parse_signature_cipher(cipher_value)
                raw_n = cipher_data.get("n")
                if raw_n and js_url:
                    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
                    parsed = urlparse(deciphered)
                    qp = parse_qs(parsed.query)
                    resolved_n = resolve_n_param(js_url, raw_n)
                    qp["n"] = [resolved_n]
                    new_query = urlencode(qp, doseq=True)
                    deciphered = urlunparse((
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        parsed.params,
                        new_query,
                        parsed.fragment,
                    ))
            except Exception as exc:
                logger.warning("n-parameter resolution failed for cipher URL: %s", exc)

        return deciphered

    if raw_url:
        resolved_url = raw_url
        if "n=" in resolved_url and js_url:
            try:
                from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

                parsed = urlparse(resolved_url)
                qp = parse_qs(parsed.query)
                raw_n = qp.get("n", [None])[0]
                if raw_n:
                    resolved_n = resolve_n_param(js_url, raw_n)
                    qp["n"] = [resolved_n]
                    new_query = urlencode(qp, doseq=True)
                    resolved_url = urlunparse((
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        parsed.params,
                        new_query,
                        parsed.fragment,
                    ))
            except Exception as exc:
                logger.warning("n-parameter resolution failed for URL: %s", exc)

        return resolved_url

    raise StreamResolutionError(
        "Format dict has neither 'url' nor 'signatureCipher' field."
    )


# ---------------------------------------------------------------------------
# Format parsing
# ---------------------------------------------------------------------------


def _parse_single_format(fmt: Dict[str, Any], js_url: Optional[str]) -> StreamFormat:
    """Parse a single raw format dict into a :class:`StreamFormat`.

    Args:
        fmt: Raw format dict from YouTube's player response.
        js_url: Player JS URL for cipher/n-parameter resolution.

    Returns:
        A fully populated :class:`StreamFormat`.

    Raises:
        StreamResolutionError: If the format dict is missing required fields.
    """
    itag = fmt.get("itag")
    if itag is None:
        raise StreamResolutionError("Format dict missing 'itag' field.")

    mime_type = fmt.get("mimeType", "")
    container, vcodec, acodec = _parse_mime_type(mime_type)
    ext = _derive_ext(mime_type, itag)

    width = fmt.get("width")
    height = fmt.get("height")
    fps = fmt.get("fps")
    tbr = fmt.get("tbr")
    abr = fmt.get("abr")
    vbr = fmt.get("vbr")

    acontainer = container
    vcontainer = container

    has_video = vcodec != "none"
    has_audio = acodec != "none"

    protocol = fmt.get("protocol", "http")
    is_dash = protocol in ("dash", "m3u8")
    is_hls = protocol in ("hls", "m3u8")

    quality_label = ITAG_QUALITY.get(itag, "unknown")
    if not quality_label or quality_label == "unknown":
        if height:
            quality_label = f"{height}p"
        elif has_audio and not has_video:
            quality_label = f"{int(abr)}kbps" if abr else "audio"
        else:
            quality_label = "unknown"

    quality_ordinal = _quality_ordinal(height, quality_label)

    content_length = _parse_content_length(fmt.get("contentLength"))
    approx_duration_ms = fmt.get("approxDurationMs")
    if approx_duration_ms is not None:
        try:
            approx_duration_ms = int(approx_duration_ms)
        except (ValueError, TypeError):
            approx_duration_ms = None

    try:
        url = _resolve_stream_url(fmt, js_url)
    except StreamResolutionError:
        raise
    except Exception as exc:
        raise StreamResolutionError(
            f"Failed to resolve stream URL for itag {itag}: {exc}"
        ) from exc

    signature_cipher = fmt.get("signatureCipher")

    filesize_approx = _estimate_size(content_length, approx_duration_ms, tbr)

    return StreamFormat(
        itag=int(itag),
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
        mimeType=mime_type,
        protocol=protocol,
        url=url,
        signature_cipher=signature_cipher,
        content_length=content_length,
        approx_duration_ms=approx_duration_ms,
        is_dash=is_dash,
        is_hls=is_hls,
        quality_label=quality_label,
        quality_ordinal=quality_ordinal,
        has_video=has_video,
        has_audio=has_audio,
        filesize_approx=filesize_approx,
    )


def parse_streaming_data(
    data: Dict[str, Any],
    js_url: Optional[str] = None,
) -> List[StreamFormat]:
    """Parse all stream formats from a YouTube player response dict.

    Processes both ``streamingData.formats`` (progressive/combined) and
    ``streamingData.adaptiveFormats`` (DASH audio/video-only) entries.

    Args:
        data: The ``ytInitialPlayerResponse`` dict or at least the
            ``streamingData`` sub-dict.
        js_url: Player JS URL used for cipher and n-parameter resolution.
            Pass ``None`` to skip resolution and return raw URLs.

    Returns:
        A list of :class:`StreamFormat` objects, one per available format.

    Raises:
        StreamResolutionError: If the ``streamingData`` key is missing or
            a format cannot be parsed.
    """
    streaming_data = data.get("streamingData", data)
    formats_raw = streaming_data.get("formats", [])
    adaptive_raw = streaming_data.get("adaptiveFormats", [])

    all_raw = list(formats_raw) + list(adaptive_raw)
    if not all_raw:
        logger.warning("No stream formats found in streamingData.")
        return []

    result: List[StreamFormat] = []
    errors: List[str] = []

    for idx, fmt_dict in enumerate(all_raw):
        try:
            sf = _parse_single_format(fmt_dict, js_url)
            result.append(sf)
        except StreamResolutionError as exc:
            errors.append(f"Format {idx} (itag={fmt_dict.get('itag', '?')}): {exc}")
            logger.debug("Skipped format %d: %s", idx, exc)

    if errors:
        logger.warning(
            "Encountered %d format resolution errors out of %d total:\n%s",
            len(errors),
            len(all_raw),
            "\n".join(errors),
        )

    return result


# ---------------------------------------------------------------------------
# Collection helpers
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
    """Filter a list of formats by the given criteria.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.
        min_height: Minimum video height in pixels (inclusive).
        max_height: Maximum video height in pixels (inclusive).
        min_width: Minimum video width in pixels (inclusive).
        max_width: Maximum video width in pixels (inclusive).
        containers: Allowed container names (e.g. ``["mp4", "webm"]``).
        vcodecs: Allowed video codec names.
        acodecs: Allowed audio codec names.
        protocols: Allowed protocol names.

    Returns:
        A new list of formats matching all provided criteria.
    """
    out: List[StreamFormat] = []
    for sf in formats:
        if min_height is not None and (sf.height or 0) < min_height:
            continue
        if max_height is not None and (sf.height or 0) > max_height:
            continue
        if min_width is not None and (sf.width or 0) < min_width:
            continue
        if max_width is not None and (sf.width or 0) > max_width:
            continue
        if containers is not None and sf.vcontainer not in containers:
            continue
        if vcodecs is not None and sf.vcodec not in vcodecs:
            continue
        if acodecs is not None and sf.acodec not in acodecs:
            continue
        if protocols is not None and sf.protocol not in protocols:
            continue
        out.append(sf)
    return out


def sort_formats(formats: List[StreamFormat], key: str = "quality") -> List[StreamFormat]:
    """Sort formats by the given key.

    Supported keys:

    - ``"quality"`` — sort by ``quality_ordinal`` descending (best first).
    - ``"height"`` — sort by video height descending.
    - ``"tbr"`` — sort by total bitrate descending.
    - ``"size"`` — sort by file size descending.
    - ``"itag"`` — sort by itag number ascending.

    Args:
        formats: List of :class:`StreamFormat` objects to sort.
        key: Sort key name.

    Returns:
        A new sorted list.
    """
    reverse = key not in ("itag",)
    key_map = {
        "quality": lambda sf: sf.quality_ordinal,
        "height": lambda sf: sf.height or 0,
        "tbr": lambda sf: sf.tbr or 0,
        "size": lambda sf: sf.filesize_approx or sf.content_length or 0,
        "itag": lambda sf: sf.itag,
    }
    sort_fn = key_map.get(key, key_map["quality"])
    return sorted(formats, key=sort_fn, reverse=reverse)


def get_format_by_itag(formats: List[StreamFormat], itag: int) -> Optional[StreamFormat]:
    """Find a format by its itag number.

    Args:
        formats: List of :class:`StreamFormat` objects to search.
        itag: Itag number to look for.

    Returns:
        The matching :class:`StreamFormat`, or ``None`` if not found.
    """
    for sf in formats:
        if sf.itag == itag:
            return sf
    return None


def get_audio_only_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return formats that contain audio but no video.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        Filtered list of audio-only formats.
    """
    return [sf for sf in formats if sf.has_audio and not sf.has_video]


def get_video_only_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return formats that contain video but no audio.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        Filtered list of video-only formats.
    """
    return [sf for sf in formats if sf.has_video and not sf.has_audio]


def get_combined_formats(formats: List[StreamFormat]) -> List[StreamFormat]:
    """Return formats that contain both audio and video tracks.

    Args:
        formats: List of :class:`StreamFormat` objects to filter.

    Returns:
        Filtered list of combined (progressive) formats.
    """
    return [sf for sf in formats if sf.has_video and sf.has_audio]


# ---------------------------------------------------------------------------
# Best-format selection
# ---------------------------------------------------------------------------


def get_best_format(
    formats: List[StreamFormat],
    quality: str = "best",
    prefer_video: bool = True,
    prefer_audio: bool = True,
    preferred_container: Optional[str] = None,
    preferred_vcodec: Optional[str] = None,
    preferred_acodec: Optional[str] = None,
) -> StreamFormat:
    """Select the best format from a list.

    Selection logic:

    1. If both ``prefer_video`` and ``prefer_audio`` are ``True``, prefer
       combined (progressive) formats first, then fall back to merging a
       video-only + audio-only pair.
    2. If ``quality`` is a specific label (e.g. ``"720p"``), select the
       highest itag with height matching or exceeding that label.
    3. If ``quality`` is ``"best"``, select the highest-quality format
       matching all other preferences.
    4. If ``quality`` is ``"worst"``, select the lowest-quality format.

    Args:
        formats: List of :class:`StreamFormat` candidates.
        quality: Desired quality label — ``"best"``, ``"worst"``, or a
            specific label such as ``"720p"``.
        prefer_video: If ``True``, video tracks are preferred.
        prefer_audio: If ``True``, audio tracks are preferred.
        preferred_container: Preferred container format (e.g. ``"mp4"``).
        preferred_vcodec: Preferred video codec (e.g. ``"avc1"``).
        preferred_acodec: Preferred audio codec (e.g. ``"aac"``).

    Returns:
        The best matching :class:`StreamFormat`.

    Raises:
        FormatSelectionError: If no suitable format can be found.
    """
    from .exceptions import FormatSelectionError

    if not formats:
        raise FormatSelectionError("No formats available to select from.")

    # Apply container/codec preference filtering.
    candidates = list(formats)

    if preferred_container:
        preferred_container = preferred_container.lower()
        preferred_candidates = [
            sf for sf in candidates
            if sf.vcontainer.lower() == preferred_container
            or sf.acontainer.lower() == preferred_container
        ]
        if preferred_candidates:
            candidates = preferred_candidates

    if preferred_vcodec:
        preferred_vcodec = preferred_vcodec.lower()
        preferred_candidates = [
            sf for sf in candidates
            if sf.vcodec.lower() == preferred_vcodec
        ]
        if preferred_candidates:
            candidates = preferred_candidates

    if preferred_acodec:
        preferred_acodec = preferred_acodec.lower()
        preferred_candidates = [
            sf for sf in candidates
            if sf.acodec.lower() == preferred_acodec
        ]
        if preferred_candidates:
            candidates = preferred_candidates

    # Quality-based selection.
    quality_lower = quality.lower().strip()

    if quality_lower == "best":
        combined = get_combined_formats(candidates)
        if combined and prefer_video and prefer_audio:
            best = max(combined, key=lambda sf: sf.quality_ordinal)
            logger.debug("Selected best combined format: itag=%d (%s).", best.itag, best.quality_label)
            return best

        if prefer_video:
            video_only = get_video_only_formats(candidates)
            if video_only:
                best_video = max(video_only, key=lambda sf: sf.quality_ordinal)
                audio_only = get_audio_only_formats(candidates)
                if audio_only and prefer_audio:
                    best_audio = max(audio_only, key=lambda sf: sf.abr or 0)
                    logger.debug(
                        "Selected best video+audio pair: itag=%d + itag=%d.",
                        best_video.itag,
                        best_audio.itag,
                    )
                    return best_video
                logger.debug("Selected best video-only format: itag=%d (%s).", best_video.itag, best_video.quality_label)
                return best_video

        if prefer_audio:
            audio_only = get_audio_only_formats(candidates)
            if audio_only:
                best_audio = max(audio_only, key=lambda sf: sf.abr or 0)
                logger.debug("Selected best audio-only format: itag=%d.", best_audio.itag)
                return best_audio

        # Fallback: return the overall best.
        best = max(candidates, key=lambda sf: sf.quality_ordinal)
        logger.debug("Selected fallback best format: itag=%d (%s).", best.itag, best.quality_label)
        return best

    if quality_lower == "worst":
        worst = min(candidates, key=lambda sf: sf.quality_ordinal)
        logger.debug("Selected worst format: itag=%d (%s).", worst.itag, worst.quality_label)
        return worst

    # Specific quality label — try to match height.
    target_height = QUALITY_HEIGHT_MAP.get(quality_lower)
    if target_height is not None:
        matching = [
            sf for sf in candidates
            if (sf.height or 0) <= target_height
        ]
        if not matching:
            matching = candidates
        combined = get_combined_formats(matching)
        pool = combined if (combined and prefer_video and prefer_audio) else matching
        best = max(pool, key=lambda sf: sf.quality_ordinal)
        logger.debug(
            "Selected format for quality=%s: itag=%d (%s, height=%s).",
            quality,
            best.itag,
            best.quality_label,
            best.height,
        )
        return best

    # Unknown quality string — fall back to best.
    best = max(candidates, key=lambda sf: sf.quality_ordinal)
    logger.debug("Selected best format (unknown quality=%s): itag=%d.", quality, best.itag)
    return best


# ---------------------------------------------------------------------------
# High-level convenience: resolve from HTML or player-response dict
# ---------------------------------------------------------------------------


def resolve_streams(
    source: Any,
    js_url: Optional[str] = None,
) -> List[StreamFormat]:
    """Resolve all stream formats from a YouTube page source or player response.

    Args:
        source: Either the raw HTML string of a YouTube watch page or the
            ``ytInitialPlayerResponse`` dict extracted from it.
        js_url: Player JS URL for cipher and n-parameter resolution.  When
            *source* is a dict, ``source["assets"]["js"]`` is used if
            *js_url* is not provided.

    Returns:
        A list of :class:`StreamFormat` objects.

    Raises:
        StreamResolutionError: If the source cannot be parsed or no formats
            are found.
    """
    import re
    import json

    player_response: Optional[Dict[str, Any]] = None

    if isinstance(source, dict):
        player_response = source
    elif isinstance(source, str):
        match = re.search(
            r"ytInitialPlayerResponse\s*=\s*({.*?})\s*[;,\n]",
            source,
            re.DOTALL,
        )
        if match:
            try:
                player_response = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError) as exc:
                raise StreamResolutionError(
                    "Failed to parse ytInitialPlayerResponse JSON."
                ) from exc
        else:
            raise StreamResolutionError(
                "Could not locate ytInitialPlayerResponse in the provided HTML."
            )

    if player_response is None:
        raise StreamResolutionError("No player response data available.")

    if js_url is None:
        assets = player_response.get("assets", {})
        js_url = assets.get("js")

    return parse_streaming_data(player_response, js_url=js_url)
