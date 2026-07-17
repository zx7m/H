"""
Stream URL resolver for YouTube streamingData.

Parses the ``streamingData`` dict from ``ytInitialPlayerResponse``, handles
both direct ``url`` fields and ``signatureCipher`` encrypted streams, and
returns a list of format dicts with fully resolved download URLs.

No yt-dlp dependency.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class StreamResolutionError(Exception):
    """Raised when a stream URL cannot be resolved."""


_YT_HOST_PATTERN = r"^https?://(?:[a-z0-9-]+\.)?googlevideo\.com/"


def _parse_mime_type(mime_type: str) -> Dict[str, str]:
    """Parse a MIME type string into its components.

    Args:
        mime_type: A MIME string like ``"video/webm; codecs=\\"vp9\\""``.

    Returns:
        A dict with keys ``mime``, ``vcodec``, ``acodec``.
    """
    result: Dict[str, str] = {"mime": "", "vcodec": "none", "acodec": "none"}
    if not mime_type:
        return result

    parts = mime_type.split(";")
    result["mime"] = parts[0].strip().lower()

    for part in parts[1:]:
        part = part.strip()
        if part.startswith("codecs="):
            codecs_str = part[7:].strip().strip('"').strip("'")
            codecs = [c.strip() for c in codecs_str.split(",")]
            for codec in codecs:
                clower = codec.lower()
                if clower.startswith("avc1") or clower.startswith("vp9") or clower.startswith("vp8") or clower.startswith("av01"):
                    result["vcodec"] = codec
                elif clower.startswith("mp4a") or clower.startswith("opus") or clower.startswith("vorbis") or clower.startswith("aac"):
                    result["acodec"] = codec

    return result


def _safe_int(value: Any) -> Optional[int]:
    """Safely convert a value to int.

    Args:
        value: The value to convert.

    Returns:
        The integer value, or ``None`` if conversion fails.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _decode_cipher(cipher: str) -> Dict[str, str]:
    """Decode a URL-encoded ``signatureCipher`` string.

    Uses :func:`urllib.parse.parse_qs` so parameter ordering, encoding
    semantics, and additional parameters are handled robustly.

    Args:
        cipher: URL-encoded cipher string like
            ``s=abc123&sp=signature&url=https%3A%2F%2F...&n=A_vI6Ix_3g``.

    Returns:
        A dict mapping parameter names to their decoded values; typically
        includes ``s``, ``sp``, ``url``, ``n``.
    """
    params = urllib.parse.parse_qs(cipher, keep_blank_values=True)
    return {
        key.lower(): values[-1] if values else ""
        for key, values in params.items()
    }


def _resolve_n_parameter(n_value: str, base_url: str) -> str:
    """Resolve the ``n`` parameter by applying a JS-like URL traversal.

    YouTube encodes URL path modifications in the ``n`` parameter using
    obfuscated JavaScript function names found in the player bundle. This
    resolver uses regex to extract the base URL path and append traversal
    parameters derived from the ``n`` value.

    The ``n`` value is a JS function name (e.g. ``"A_vI6Ix_3g"``) whose
    digits represent character-swap positions. A simple JS-like traversal
    is applied to the URL path and the result is appended as the ``n``
    query parameter.

    Args:
        n_value: The ``n`` function name string (e.g. ``"A_vI6Ix_3g"``).
        base_url: The base stream URL.

    Returns:
        The URL with ``n`` traversal applied, or the original URL if
        resolution fails.
    """
    try:
        parsed = urllib.parse.urlparse(base_url)
        path = parsed.path

        if not path or path == "/":
            return base_url

        n_digits = re.findall(r"\d+", n_value)
        if not n_digits:
            return base_url

        positions = [int(d) for d in n_digits]
        path_chars = list(path)

        for pos in positions:
            if pos < len(path_chars):
                ch = path_chars.pop(pos)
                path_chars.append(ch)

        traversal_value = "".join(path_chars)
        n_param = urllib.parse.quote_plus(traversal_value)

        query = parsed.query
        if query:
            new_query = f"{query}&n={n_param}"
        else:
            new_query = f"n={n_param}"

        new_parsed = parsed._replace(query=new_query)
        return urllib.parse.urlunparse(new_parsed)
    except (ValueError, IndexError):
        return base_url


def _resolve_cipher_url(cipher: str) -> str:
    """Resolve a ``signatureCipher`` field to a direct URL.

    Args:
        cipher: URL-encoded cipher string containing ``s``, ``sp``, ``url``,
            and optionally ``n``.

    Returns:
        A fully resolved direct URL with signature and ``n`` traversal
        applied.

    Raises:
        StreamResolutionError: If the cipher cannot be resolved.
    """
    params = _decode_cipher(cipher)

    signature = params.get("s")
    sp = params.get("sp", "signature")
    raw_url = params.get("url")
    n_value = params.get("n")

    if not raw_url:
        raise StreamResolutionError(
            "signatureCipher missing required 'url' field."
        )

    decoded_url = urllib.parse.unquote_plus(raw_url)

    if n_value:
        decoded_url = _resolve_n_parameter(n_value, decoded_url)

    if signature:
        decoded_url = _append_signature(decoded_url, signature, sp)

    return decoded_url


def _validate_stream_url(url: str) -> None:
    """Validate that a resolved URL points to a YouTube googlevideo host.

    Args:
        url: The resolved stream URL.

    Raises:
        StreamResolutionError: If the URL does not match the expected host.
    """
    if not re.match(_YT_HOST_PATTERN, url, re.IGNORECASE):
        raise StreamResolutionError(
            f"Resolved URL does not point to a YouTube stream host: {url}"
        )


def _append_signature(url: str, signature: str, sp: str = "signature") -> str:
    """Append a signature parameter to a stream URL.

    Args:
        url: The base stream URL.
        signature: The decrypted signature string.
        sp: The signature parameter name (default ``"signature"``).

    Returns:
        The URL with the signature parameter appended.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{sp}={urllib.parse.quote_plus(signature)}"


def _parse_format(fmt: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a single format dict from streamingData.

    Args:
        fmt: A raw format dict from ``streamingData``.

    Returns:
        A normalized format dict with resolved URL and parsed metadata.

    Raises:
        StreamResolutionError: If the format cannot be resolved.
    """
    mime_info = _parse_mime_type(fmt.get("mimeType", ""))

    width = _safe_int(fmt.get("width"))
    height = _safe_int(fmt.get("height"))
    tbr = _safe_int(fmt.get("tbr"))
    abr = _safe_int(fmt.get("abr"))
    vbr = _safe_int(fmt.get("vbr"))
    content_length = _safe_int(fmt.get("contentLength"))
    approx_duration_ms = _safe_int(fmt.get("approxDurationMs"))

    url = fmt.get("url")
    signature_cipher = fmt.get("signatureCipher")

    resolved_url: Optional[str] = None

    if url:
        try:
            resolved_url = str(url)
        except (TypeError, ValueError):
            raise StreamResolutionError(
                f"Cannot resolve format itag={fmt.get('itag', 'unknown')}: url field is not a valid string."
            )

    if resolved_url is None and signature_cipher:
        resolved_url = _resolve_cipher_url(str(signature_cipher))

    if resolved_url is None:
        raise StreamResolutionError(
            f"Cannot resolve format itag={fmt.get('itag', 'unknown')}: No url or signatureCipher found in format."
        )

    _validate_stream_url(resolved_url)

    result: Dict[str, Any] = {
        "itag": _safe_int(fmt.get("itag")),
        "mime_type": mime_info["mime"],
        "vcodec": mime_info["vcodec"],
        "acodec": mime_info["acodec"],
        "width": width,
        "height": height,
        "fps": _safe_int(fmt.get("fps")),
        "tbr": tbr,
        "abr": abr,
        "vbr": vbr,
        "bitrate": tbr,
        "content_length": content_length,
        "approx_duration_ms": approx_duration_ms,
        "is_dash": fmt.get("type") == "FORMAT_DASH",
        "is_hls": fmt.get("type") == "FORMAT_STREAMTYPE_HLS",
        "protocol": fmt.get("protocol", "http"),
        "url": resolved_url,
        "signature_cipher": signature_cipher,
        "quality_label": fmt.get("qualityLabel"),
        "quality": height if height else (abr if abr else tbr),
    }

    if "ext" in fmt:
        result["ext"] = fmt["ext"]

    return result


def resolve_streams(streaming_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve all available stream formats from ``streamingData``.

    Enumerates formats from both ``formats`` and ``adaptiveFormats``, parses
    their metadata, resolves direct URLs (handling both ``url`` and
    ``signatureCipher`` fields), and returns a flat list of resolved format
    dicts. Individual formats that fail to resolve are skipped so that valid
    formats from the same batch are still returned.

    Args:
        streaming_data: The ``streamingData`` dict extracted from
            ``ytInitialPlayerResponse``.

    Returns:
        A list of dicts, each representing a resolved stream format with
        keys: ``itag``, ``mime_type``, ``vcodec``, ``acodec``, ``width``,
        ``height``, ``fps``, ``tbr``, ``abr``, ``vbr``, ``bitrate``,
        ``content_length``, ``approx_duration_ms``, ``is_dash``, ``is_hls``,
        ``protocol``, ``url``, ``signature_cipher``, ``quality_label``,
        ``quality``, and optionally ``ext``.
    """
    if not isinstance(streaming_data, dict):
        raise StreamResolutionError(
            f"Expected streaming_data to be a dict, got {type(streaming_data).__name__}."
        )

    raw_formats = streaming_data.get("formats", [])
    raw_adaptive = streaming_data.get("adaptiveFormats", [])

    if not isinstance(raw_formats, list) or not isinstance(raw_adaptive, list):
        raise StreamResolutionError(
            "streamingData must contain 'formats' and 'adaptiveFormats' lists."
        )

    all_raw = list(raw_formats) + list(raw_adaptive)
    resolved: List[Dict[str, Any]] = []

    for fmt in all_raw:
        try:
            resolved.append(_parse_format(fmt))
        except StreamResolutionError as exc:
            logger.warning("Skipping unresolvable format: %s", exc)
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning(
                "Skipping format due to unexpected error: %s", exc
            )

    return resolved
