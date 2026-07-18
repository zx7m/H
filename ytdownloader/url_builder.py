"""
YouTube stream URL construction and validation utilities.

This module provides comprehensive URL building, sanitization, validation,
and inspection utilities for constructing valid YouTube stream URLs. It
handles signature parameters, n-parameter values, DASH/HLS manifest URLs,
query parameter manipulation, tracking-parameter removal, and host
extraction for both YouTube watch pages and googlevideo.com stream URLs.

All public functions log at appropriate levels and raise :class:`StreamURLError`
when a URL cannot be constructed or validated.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from ytdownloader.exceptions import YTDLException
from ytdownloader.logger import get_logger

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

GOOGLEVIDEO_HOSTS: frozenset[str] = frozenset(
    {
        "googlevideo.com",
        "www.googlevideo.com",
        "redirector.googlevideo.com",
        "manifest.googlevideo.com",
    }
)

TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "dclid",
        "ocid",
        "feature",
        "si",
        "app",
        "desktop_uri",
        "fbclid",
        "_ga",
        "_gl",
        "msclkid",
        "vero_id",
        "ved",
        "usg",
        "sns",
        "sns_ids",
    }
)

SIGNATURE_PARAM_NAMES: List[str] = ["s", "sig", "signature"]
SIGNATURE_SP_NAMES: List[str] = ["sp"]
N_PARAM_NAME: str = "n"

YOUTUBE_PAGE_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    }
)

_VALID_SCHEMES: frozenset[str] = frozenset({"http", "https"})


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class StreamURLError(YTDLException):
    """Raised when a stream URL cannot be constructed, validated, or parsed.

    This exception is raised by :func:`build_stream_url` and the validation
    helpers when the supplied arguments are inconsistent or the URL fails
    structural validation.

    Attributes:
        url: The URL that triggered the error, if applicable.
    """

    def __init__(
        self, message: str = "", url: str = "", cause: Exception | None = None
    ) -> None:
        self.url = url
        super().__init__(message, cause=cause)

    def __str__(self) -> str:
        msg = super().__str__()
        if self.url:
            msg = f"{msg} [url={self.url!r}]"
        return msg


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

_logger: logging.Logger = get_logger("url_builder")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_debug(message: str, *args: Any) -> None:
    _logger.debug(message, *args)


def _log_info(message: str, *args: Any) -> None:
    _logger.info(message, *args)


def _log_warning(message: str, *args: Any) -> None:
    _logger.warning(message, *args)


def _log_error(message: str, *args: Any) -> None:
    _logger.error(message, *args)


def _raise_url_error(
    message: str, url: str = "", cause: Exception | None = None
) -> None:
    _log_error(message)
    raise StreamURLError(message, url=url, cause=cause)


def _normalize_scheme(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


def _encode_query_value(value: str) -> str:
    return urllib.parse.quote(value, safe="-_~.*")


def _decode_query_value(value: str) -> str:
    return urllib.parse.unquote(value)


def _parse_url_parts(url: str):
    url = _normalize_scheme(url)
    return urllib.parse.urlsplit(url)


def _rebuild_url(
    scheme: str, netloc: str, path: str, query: str, fragment: str
) -> str:
    return urllib.parse.urlunsplit((scheme, netloc, path, query, fragment))


def _has_signature(url: str) -> bool:
    parsed = _parse_url_parts(url)
    if not parsed.query:
        return False
    params = urllib.parse.parse_qs(parsed.query)
    return any(name in params for name in SIGNATURE_PARAM_NAMES)


def _has_n_param(url: str) -> bool:
    parsed = _parse_url_parts(url)
    if not parsed.query:
        return False
    params = urllib.parse.parse_qs(parsed.query)
    return N_PARAM_NAME in params


def _set_query_param(url: str, name: str, value: str) -> str:
    parsed = _parse_url_parts(url)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    params[name] = [value]
    new_query = urllib.parse.urlencode(params, doseq=True)
    return _rebuild_url(
        parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment
    )


def _remove_query_params(url: str, names: List[str]) -> str:
    parsed = _parse_url_parts(url)
    if not parsed.query:
        return url
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for name in names:
        params.pop(name, None)
    new_query = urllib.parse.urlencode(params, doseq=True)
    return _rebuild_url(
        parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_host(url: str) -> str:
    """Return the hostname from *url*.

    The scheme is normalised to ``https`` if missing and the port (if any)
    is preserved in the hostname portion returned by
    :func:`urllib.parse.urlsplit`.

    Args:
        url: Any URL string.

    Returns:
        The hostname (lower-cased) extracted from *url*, or an empty string
        when *url* does not contain a valid network location.

    Examples:
        >>> extract_host("https://www.youtube.com/watch?v=abc")
        'www.youtube.com'
        >>> extract_host("googlevideo.com/videoplayback?itag=18")
        'googlevideo.com'
    """
    _log_debug("extract_host: url=%r", url)
    try:
        parsed = _parse_url_parts(url)
        host = parsed.netloc.split(":")[0].lower()
        return host
    except Exception as exc:
        _log_warning("extract_host failed for %r: %s", url, exc)
        return ""


def is_youtube_stream_url(url: str) -> bool:
    """Return ``True`` if *url* points to a googlevideo.com stream host.

    googlevideo.com is the domain YouTube uses for actual media stream
    delivery (as opposed to the youtube.com watch pages used for metadata
    extraction).

    Args:
        url: URL string to test.

    Returns:
        ``True`` when the hostname is in :data:`GOOGLEVIDEO_HOSTS`.

    Examples:
        >>> is_youtube_stream_url(
        ...     "https://rr4---sn-8xgp1vo-xfgl.googlevideo.com/videoplayback?itag=18"
        ... )
        True
        >>> is_youtube_stream_url("https://www.youtube.com/watch?v=abc")
        False
    """
    _log_debug("is_youtube_stream_url: url=%r", url)
    host = extract_host(url)
    result = host in GOOGLEVIDEO_HOSTS
    _log_debug("is_youtube_stream_url: host=%r, result=%s", host, result)
    return result


def validate_stream_url(url: str) -> bool:
    """Perform structural validation of a stream URL.

    A URL is considered valid when:

    1. It is non-empty after stripping whitespace.
    2. It uses ``http`` or ``https`` (or has no scheme, in which case
       ``https`` is assumed).
    3. It has a non-empty hostname component.
    4. Its path is non-empty.

    Args:
        url: URL string to validate.

    Returns:
        ``True`` when *url* passes all structural checks.

    Examples:
        >>> validate_stream_url("https://googlevideo.com/videoplayback?itag=18")
        True
        >>> validate_stream_url("")
        False
        >>> validate_stream_url("   ")
        False
    """
    _log_debug("validate_stream_url: url=%r", url)
    stripped = url.strip()
    if not stripped:
        _log_warning("validate_stream_url: empty URL")
        return False

    try:
        parsed = _parse_url_parts(stripped)
    except Exception as exc:
        _log_warning("validate_stream_url: parse error for %r: %s", stripped, exc)
        return False

    if parsed.scheme and parsed.scheme not in _VALID_SCHEMES:
        _log_warning(
            "validate_stream_url: unsupported scheme %r for %r",
            parsed.scheme,
            stripped,
        )
        return False

    host = parsed.netloc.split(":")[0].lower()
    if not host:
        _log_warning("validate_stream_url: missing host in %r", stripped)
        return False

    if not parsed.path or parsed.path == "/":
        _log_warning("validate_stream_url: missing path in %r", stripped)
        return False

    _log_debug("validate_stream_url: %r is valid", stripped)
    return True


def sanitize_url(url: str) -> str:
    """Remove tracking parameters and normalise a URL.

    The function performs the following transformations:

    1. Strips leading and trailing whitespace.
    2. Ensures the URL has a scheme (defaults to ``https``).
    3. Removes all parameters listed in :data:`TRACKING_PARAMS`.
    4. Collapses consecutive slashes in the path.
    5. Removes a trailing ``?`` or ``&`` if the query string becomes empty.

    Args:
        url: The URL to sanitise.

    Returns:
        A cleaned URL string.

    Raises:
        StreamURLError: If *url* is empty or structurally invalid after
            stripping.

    Examples:
        >>> sanitize_url(
        ...     "https://www.youtube.com/watch?v=abc&utm_source=email"
        ... )
        'https://www.youtube.com/watch?v=abc'
    """
    _log_info("sanitize_url: input=%r", url)
    stripped = url.strip()
    if not stripped:
        _raise_url_error("Cannot sanitize an empty URL", url=stripped)

    try:
        parsed = _parse_url_parts(stripped)
    except Exception as exc:
        _raise_url_error(
            f"Failed to parse URL for sanitization: {exc}", url=stripped, cause=exc
        )

    new_params: Dict[str, List[str]] = {}
    if parsed.query:
        raw_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        for key, values in raw_params.items():
            if key.lower() not in TRACKING_PARAMS:
                new_params[key] = values

    new_query = urllib.parse.urlencode(new_params, doseq=True)
    normalised_path = re.sub(r"/{2,}", "/", parsed.path)
    result = _rebuild_url(
        parsed.scheme, parsed.netloc, normalised_path, new_query, parsed.fragment
    )
    _log_info("sanitize_url: output=%r", result)
    return result


def append_url_params(url: str, params: Dict[str, Any]) -> str:
    """Safely append or update query parameters on *url*.

    Parameters in *params* override any existing values for the same key.
    Values that are ``None`` are treated as empty strings.  All other
    values are coerced to strings before encoding.

    If *url* contains existing query parameters, the new parameters are
    merged rather than appended with a second ``?`` delimiter.

    Args:
        url: Base URL.  May or may not already contain a query string.
        params: Mapping of parameter names to values.  Values may be
            ``str``, ``int``, ``float``, or any type with a ``__str__``
            representation.

    Returns:
        The URL with the supplied parameters appended.

    Raises:
        StreamURLError: If *url* is empty or structurally invalid.

    Examples:
        >>> append_url_params(
        ...     "https://googlevideo.com/videoplayback?itag=18",
        ...     {"itag": "22", "range": "0-1024"},
        ... )
        'https://googlevideo.com/videoplayback?itag=22&range=0-1024'
    """
    _log_info("append_url_params: url=%r, params=%r", url, params)
    stripped = url.strip()
    if not stripped:
        _raise_url_error("Cannot append params to an empty URL", url=stripped)

    try:
        parsed = _parse_url_parts(stripped)
    except Exception as exc:
        _raise_url_error(
            f"Failed to parse URL for param appending: {exc}", url=stripped, cause=exc
        )

    existing: Dict[str, List[str]] = {}
    if parsed.query:
        existing = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    for key, value in params.items():
        str_value = "" if value is None else str(value)
        existing[key] = [_encode_query_value(str_value)]
        _log_debug("append_url_params: set %r=%r", key, str_value)

    new_query = urllib.parse.urlencode(existing, doseq=True)
    result = _rebuild_url(
        parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment
    )
    _log_info("append_url_params: result=%r", result)
    return result


def build_hls_stream_url(base_url: str) -> str:
    """Build an HLS playlist URL from *base_url*.

    The function appends the ``.m3u8`` extension or ``/master.m3u8`` path
    to the URL path when the URL does not already point to an HLS manifest.
    Query parameters are preserved.

    Args:
        base_url: The base stream URL.

    Returns:
        A URL whose path ends with ``.m3u8`` or ``/master.m3u8``, suitable
        for use as an HLS playlist manifest URL.

    Raises:
        StreamURLError: If *base_url* is empty or structurally invalid.

    Examples:
        >>> build_hls_stream_url(
        ...     "https://manifest.googlevideo.com/api/manifest/hls_variant/..."
        ... )
        'https://manifest.googlevideo.com/api/manifest/hls_variant/...'
    """
    _log_info("build_hls_stream_url: base_url=%r", base_url)
    stripped = base_url.strip()
    if not stripped:
        _raise_url_error("Cannot build HLS URL from an empty base_url", url=stripped)

    if not validate_stream_url(stripped):
        _raise_url_error("Invalid base_url for HLS stream", url=stripped)

    parsed = _parse_url_parts(stripped)
    path = parsed.path.rstrip("/")

    if path.endswith(".m3u8") or path.endswith("master.m3u8"):
        _log_info("build_hls_stream_url: URL already points to HLS manifest")
        return stripped

    if "/hls" in path.lower() or "/manifest" in path.lower():
        new_path = (
            f"{path}/master.m3u8" if not path.endswith("/") else f"{path}master.m3u8"
        )
    else:
        new_path = f"{path}.m3u8"

    result = _rebuild_url(
        parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment
    )
    _log_info("build_hls_stream_url: result=%r", result)
    return result


def build_dash_stream_url(
    base_url: str, quality_params: Optional[Dict[str, str]] = None
) -> str:
    """Build a DASH manifest URL from *base_url*.

    The function ensures the path points to a DASH ``mpd`` manifest.
    When *quality_params* is provided, those parameters are appended to
    the query string.  Existing query parameters are preserved unless
    overridden by *quality_params*.

    Args:
        base_url: The base stream URL.
        quality_params: Optional mapping of quality-related query parameters
            (e.g. ``{"itag": "137", "quality": "hd1080"}``).

    Returns:
        A URL whose path ends with ``.mpd``, suitable for use as a DASH
        manifest URL.

    Raises:
        StreamURLError: If *base_url* is empty or structurally invalid.

    Examples:
        >>> build_dash_stream_url(
        ...     "https://manifest.googlevideo.com/api/manifest/dash/...",
        ...     {"itag": "137"},
        ... )
        'https://manifest.googlevideo.com/api/manifest/dash/...'
    """
    _log_info(
        "build_dash_stream_url: base_url=%r, quality_params=%r",
        base_url,
        quality_params,
    )
    stripped = base_url.strip()
    if not stripped:
        _raise_url_error("Cannot build DASH URL from an empty base_url", url=stripped)

    if not validate_stream_url(stripped):
        _raise_url_error("Invalid base_url for DASH stream", url=stripped)

    parsed = _parse_url_parts(stripped)
    path = parsed.path.rstrip("/")

    if path.endswith(".mpd"):
        _log_info("build_dash_stream_url: URL already points to DASH manifest")
    elif "/dash" in path.lower():
        new_path = (
            f"{path}/manifest.mpd" if not path.endswith("/") else f"{path}manifest.mpd"
        )
        path = new_path
    else:
        path = f"{path}.mpd"

    new_params: Dict[str, List[str]] = {}
    if parsed.query:
        new_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    if quality_params:
        for key, value in quality_params.items():
            new_params[key] = [str(value) if value is not None else ""]
            _log_debug("build_dash_stream_url: quality param %r=%r", key, value)

    new_query = urllib.parse.urlencode(new_params, doseq=True)
    result = _rebuild_url(
        parsed.scheme, parsed.netloc, path, new_query, parsed.fragment
    )
    _log_info("build_dash_stream_url: result=%r", result)
    return result


def build_stream_url(
    base_url: str,
    signature: Optional[str] = None,
    sp: Optional[str] = None,
    n_value: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a complete YouTube stream download URL.

    This is the primary entry point for constructing a valid, signed stream
    URL from a raw googlevideo.com URL extracted from the YouTube player
    response.

    The function handles several edge cases automatically:

    - URLs that already contain a signature parameter (``s``/``sig``/
      ``signature``) -- the existing signature is left untouched and a
      :class:`StreamURLError` is raised if *signature* is also supplied.
    - URLs that already contain the ``n`` parameter -- the supplied
      *n_value* replaces the existing one (with a warning).
    - URLs that already contain query parameters -- new parameters are
      merged rather than appended blindly.
    - Special characters in parameter values -- all values are
      percent-encoded.

    Args:
        base_url: The raw stream URL extracted from
            ``ytInitialPlayerResponse``.
        signature: Optional decrypted signature string.  When supplied the
            ``sp`` parameter defaults to ``"signature"``.
        sp: The signature parameter name (e.g. ``"s"``, ``"sig"``, or
            ``"signature"``).  Defaults to ``"signature"`` when *signature*
            is provided and *sp* is ``None``.
        n_value: Optional n-parameter cipher value.  When supplied the
            ``n`` query parameter is set.
        extra_params: Optional additional query parameters to append to
            the URL.

    Returns:
        The fully constructed, signed stream URL.

    Raises:
        StreamURLError: If *base_url* is empty, structurally invalid, or
            both a signature and an existing signature parameter are
            supplied.

    Examples:
        >>> url = build_stream_url(
        ...     "https://rr4---sn-8xgp1vo-xfgl.googlevideo.com/videoplayback?itag=18&ratebypass=yes",
        ...     signature="A3f3x8Y2...",
        ...     sp="sig",
        ...     n_value="KJE2...",
        ...     extra_params={"range": "0-1048575"},
        ... )
    """
    _log_info(
        "build_stream_url: base_url=%r, signature=%r, sp=%r, n_value=%r, extra_params=%r",
        base_url,
        "[REDACTED]" if signature else None,
        sp,
        "[REDACTED]" if n_value else None,
        extra_params,
    )
    stripped = base_url.strip()
    if not stripped:
        _raise_url_error("Cannot build stream URL from an empty base_url", url=stripped)

    if not validate_stream_url(stripped):
        _raise_url_error("Invalid base_url for stream URL construction", url=stripped)

    url = stripped

    if signature is not None and _has_signature(url):
        _raise_url_error(
            "base_url already contains a signature parameter; "
            "cannot apply a second signature.  Remove the existing signature "
            "parameter from base_url before calling build_stream_url.",
            url=url,
        )

    if n_value is not None and _has_n_param(url):
        _log_warning(
            "build_stream_url: base_url already contains n parameter; "
            "replacing with supplied n_value."
        )
        url = _remove_query_params(url, [N_PARAM_NAME])

    if signature is not None:
        effective_sp = sp if sp is not None else "signature"
        if effective_sp not in SIGNATURE_PARAM_NAMES:
            _log_warning(
                "build_stream_url: sp=%r is not in the known signature "
                "parameter list %r; proceeding anyway.",
                effective_sp,
                SIGNATURE_PARAM_NAMES,
            )
        url = _set_query_param(url, effective_sp, signature)
        _log_info("build_stream_url: applied signature param %r", effective_sp)

    if n_value is not None:
        url = _set_query_param(url, N_PARAM_NAME, n_value)
        _log_info("build_stream_url: applied n parameter")

    if extra_params:
        url = append_url_params(url, extra_params)
        _log_info("build_stream_url: applied extra_params")

    _log_info("build_stream_url: result=%r", url)
    return url


# ---------------------------------------------------------------------------
# Edge-case helpers
# ---------------------------------------------------------------------------


def is_signed_url(url: str) -> bool:
    """Return ``True`` if *url* contains a signature parameter.

    Known signature parameter names are ``s``, ``sig``, and ``signature``.

    Args:
        url: The URL to inspect.

    Returns:
        ``True`` if any known signature parameter is present.
    """
    return _has_signature(url)


def has_n_parameter(url: str) -> bool:
    """Return ``True`` if *url* contains the ``n`` cipher parameter.

    Args:
        url: The URL to inspect.

    Returns:
        ``True`` when ``n`` is in the query string.
    """
    return _has_n_param(url)


def is_already_signed(url: str) -> bool:
    """Return ``True`` if *url* contains any known signature parameter.

    This is an alias for :func:`is_signed_url` provided for readability.

    Args:
        url: The URL to inspect.

    Returns:
        ``True`` if a signature parameter is present.
    """
    return is_signed_url(url)


def strip_signature(url: str) -> str:
    """Remove all known signature parameters from *url*.

    Args:
        url: The URL to process.

    Returns:
        The URL with all signature parameters removed.

    Raises:
        StreamURLError: If *url* is empty.
    """
    _log_info("strip_signature: url=%r", url)
    stripped = url.strip()
    if not stripped:
        _raise_url_error("Cannot strip signature from an empty URL", url=stripped)
    result = _remove_query_params(stripped, SIGNATURE_PARAM_NAMES)
    _log_info("strip_signature: result=%r", result)
    return result


def strip_n_parameter(url: str) -> str:
    """Remove the ``n`` cipher parameter from *url*.

    Args:
        url: The URL to process.

    Returns:
        The URL with the ``n`` parameter removed.

    Raises:
        StreamURLError: If *url* is empty.
    """
    _log_info("strip_n_parameter: url=%r", url)
    stripped = url.strip()
    if not stripped:
        _raise_url_error("Cannot strip n-parameter from an empty URL", url=stripped)
    result = _remove_query_params(stripped, [N_PARAM_NAME])
    _log_info("strip_n_parameter: result=%r", result)
    return result


def has_existing_query_params(url: str) -> bool:
    """Return ``True`` if *url* has a non-empty query string.

    Args:
        url: The URL to inspect.

    Returns:
        ``True`` when the URL contains a ``?`` followed by at least one
        character.
    """
    parsed = _parse_url_parts(url.strip())
    return bool(parsed.query)


def get_query_params(url: str) -> Dict[str, List[str]]:
    """Return all query parameters from *url* as a dictionary of lists.

    Args:
        url: The URL to parse.

    Returns:
        A dictionary mapping parameter names to lists of values.

    Raises:
        StreamURLError: If *url* is empty.
    """
    _log_debug("get_query_params: url=%r", url)
    stripped = url.strip()
    if not stripped:
        _raise_url_error("Cannot parse query params from an empty URL", url=stripped)
    parsed = _parse_url_parts(stripped)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    _log_debug("get_query_params: params=%r", params)
    return params


def get_single_query_param(
    url: str, name: str, default: Optional[str] = None
) -> Optional[str]:
    """Return the first value of *name* from *url*'s query string.

    Args:
        url: The URL to parse.
        name: Parameter name.
        default: Value to return when *name* is not present.

    Returns:
        The first value associated with *name*, or *default*.
    """
    params = get_query_params(url)
    values = params.get(name, [])
    return values[0] if values else default



