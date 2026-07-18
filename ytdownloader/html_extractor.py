"""
HTML extraction utilities for parsing YouTube watch pages.

Provides regex-based extractors for ``ytInitialPlayerResponse``, ``ytcfg``,
``ytInitialData``, video IDs, and restriction flags directly from raw HTML
returned by YouTube's watch endpoint.

Typical usage::

    from ytdownloader.html_extractor import extract_player_response

    with open("watch_page.html", "r", encoding="utf-8") as fh:
        html = fh.read()

    player_response = extract_player_response(html)
    video_id = find_video_id_from_html(html)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .exceptions import HtmlExtractionError, NetworkError

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Matches: ytInitialPlayerResponse = {...};  (with optional trailing ; or ,)
# Also handles: var ytInitialPlayerResponse = {...}
# and: window["ytInitialPlayerResponse"] = {...}
_RE_PLAYER_RESPONSE = re.compile(
    r"(?:var\s+)?(?:window\[[\'\"]ytInitialPlayerResponse[\'\"]\]\s*=\s*"
    r"|ytInitialPlayerResponse\s*=\s*)"
    r"(\{.*?\})"
    r"\s*[;,\n]",
    re.DOTALL | re.IGNORECASE,
)

# Matches: var ytcfg = {...};  anywhere in the HTML
_RE_YTCFG = re.compile(
    r"(?:var\s+)?ytcfg\s*=\s*(\{.*?\})\s*[;,\n]",
    re.DOTALL | re.IGNORECASE,
)

# Matches: ytInitialData = {...};
_RE_INITIAL_DATA = re.compile(
    r"ytInitialData\s*=\s*(\{.*?\})\s*[;,\n]",
    re.DOTALL | re.IGNORECASE,
)

# Matches: "sts":<digits> inside ytcfg or anywhere in the script
_RE_STS = re.compile(
    r'"sts"\s*:\s*(\d+)',
    re.IGNORECASE,
)

# Matches video ID in <meta> tags or og:url
_RE_VIDEO_ID_META = re.compile(
    r'<meta\s+(?:itemprop|property)'
    r'=["\'](?:videoId|og:url)["\']\s+content=["\']'
    r'([A-Za-z0-9_-]{11})'
    r'["\']',
    re.IGNORECASE,
)

# Fallback: video ID in canonical link
_RE_VIDEO_ID_CANONICAL = re.compile(
    r'<link\s+rel=["\']canonical["\']\s+href=["\']'
    r'(?:https?://(?:www\.)?youtube\.com/watch\?v=)'
    r'([A-Za-z0-9_-]{11})'
    r'["\']',
    re.IGNORECASE,
)

# Fallback: video ID in URL path
_RE_VIDEO_ID_PATH = re.compile(
    r'(?:/watch\?v=|/embed/|/v/|/shorts/|/live/)'
    r'([A-Za-z0-9_-]{11})',
    re.IGNORECASE,
)

# Age restriction indicators
_RE_AGE_GATE = re.compile(
    r'"isAgeRestricted"\s*:\s*true'
    r'|"age_verification"\s*:\s*true'
    r'|"isPlayable"\s*:\s*false'
    r'|loginfo="AGE_GATE"'
    r'|age-gate'
    r'|show_age_verification',
    re.IGNORECASE,
)

# Geo restriction indicators
_RE_GEO_RESTRICTED = re.compile(
    r'"status"\s*:\s*"AGE_CHECK_REQUIRED"'
    r'|"status"\s*:\s*"AGE_VERIFICATION_REQUIRED"'
    r'|"status"\s*:\s*"AGE_RESTRICTION"'
    r'|geo-restricted'
    r'|"gl"\s*:',
    re.IGNORECASE,
)

# Embedded JSON escaped-unicode / control-char sequences
_RE_ESCAPED_UNICODE = re.compile(r"\\u[0-9a-fA-F]{4}")
_RE_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _unescape_js_string(value: str) -> str:
    """Reverse the JavaScript string escaping applied by YouTube's page.

    Handles ``\\uXXXX`` unicode escapes and common backslash sequences
    (``\\n``, ``\\t``, ``\\"``, ``\\\\``) that appear in the raw HTML
    before the JSON is parsed.

    Args:
        value: Raw string as found inside the HTML ``<script>`` tag.

    Returns:
        A Python string with escape sequences resolved.
    """
    result = _RE_ESCAPED_UNICODE.sub(
        lambda m: chr(int(m.group(0)[2:], 16)),
        value,
    )
    result = result.replace("\\n", "\n")
    result = result.replace("\\t", "\t")
    result = result.replace('\\"', '"')
    result = result.replace("\\\\", "\\")
    result = result.replace("\\r", "\r")
    result = result.replace("\\/", "/")
    return result


def _clean_json_candidate(raw: str) -> str:
    """Strip JS trailing characters and clean a raw JSON candidate string.

    YouTube may embed ``{...}`` inside JavaScript where the closing brace is
    followed by a semicolon, comma, or newline that was already consumed by
    the regex.  This function performs a conservative cleanup:

    1. Strip a single trailing semicolon if present.
    2. Strip one level of outer parentheses ``(``...``)`` if the JSON was
       wrapped by a parenthesised expression.
    3. Remove ASCII control characters (except ``\\n`` already converted).

    Args:
        raw: Raw substring captured from the HTML.

    Returns:
        Cleaned string suitable for :func:`json.loads`.
    """
    cleaned = raw.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    cleaned = _RE_CONTROL_CHARS.sub("", cleaned)
    return cleaned.strip()


def _safe_json_loads(raw: str, context: str = "") -> dict[str, Any]:
    """Parse a JSON string with robust error reporting.

    First attempts :func:`json.loads` directly, then falls back to
    :func:`_unescape_js_string` before re-parsing.

    Args:
        raw: Raw JSON string (possibly with JS-style escapes).
        context: Human-readable context included in error messages so the
            caller can identify which extractor failed.

    Returns:
        Parsed :class:`dict`.

    Raises:
        HtmlExtractionError: If the string cannot be parsed as JSON.
    """
    errors: list[tuple[int, Exception]] = []

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append((exc.lineno, exc))

    unescaped = _unescape_js_string(raw)
    try:
        return json.loads(unescaped)
    except json.JSONDecodeError as exc:
        errors.append((exc.lineno, exc))

    preview = raw[:200]
    line_no, first_err = errors[0]
    raise HtmlExtractionError(
        f"Failed to parse JSON {context!r} at line ~{line_no}: {first_err}. "
        f"Preview: {preview!r}.",
        cause=first_err,
    ) from first_err


def _extract_with_regex(
    html: str,
    pattern: re.Pattern[str],
    label: str,
) -> dict[str, Any]:
    """Run a compiled regex against *html* and parse the captured group.

    Args:
        html: Raw HTML source string.
        pattern: Compiled :class:`re.Pattern` with exactly one capture group
            that captures the raw JSON string.
        label: Human-readable label used in log and error messages.

    Returns:
        Parsed :class:`dict` from the captured JSON.

    Raises:
        HtmlExtractionError: If the pattern does not match or the captured
            text is not valid JSON.
    """
    match = pattern.search(html)
    if not match:
        _logger.debug("%s pattern did not match in HTML.", label)
        raise HtmlExtractionError(
            f"Could not locate {label} in the provided HTML. "
            "The page may be malformed, the video unavailable, or YouTube "
            "has changed its page structure."
        )

    raw_json = match.group(1)
    _logger.debug("%s matched %d characters.", label, len(raw_json))

    cleaned = _clean_json_candidate(raw_json)
    return _safe_json_loads(cleaned, context=label)


def _extract_regex_text(
    html: str,
    pattern: re.Pattern[str],
    label: str,
    default: str = "",
) -> str:
    """Run a compiled regex and return the first captured group as a string.

    Args:
        html: Raw HTML source string.
        pattern: Compiled :class:`re.Pattern` with one capture group.
        label: Human-readable label for log/error messages.
        default: Value returned when the pattern does not match.

    Returns:
        Captured string, or *default* if not found.
    """
    match = pattern.search(html)
    if match:
        return match.group(1)
    _logger.debug("%s pattern did not match in HTML.", label)
    return default


# ---------------------------------------------------------------------------
# Public extractors
# ---------------------------------------------------------------------------


def extract_player_response(html: str) -> dict[str, Any]:
    """Extract the ``ytInitialPlayerResponse`` JavaScript object from HTML.

    Searches the raw HTML for a ``ytInitialPlayerResponse = {...}``
    assignment using several regex variants to handle the different forms
    YouTube may emit:

    * ``ytInitialPlayerResponse = {...};``
    * ``var ytInitialPlayerResponse = {...};``
    * ``window["ytInitialPlayerResponse"] = {...};``

    The captured JSON is cleaned of JavaScript-specific escape sequences and
    parsed into a Python :class:`dict`.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        A dictionary representing the full ``ytInitialPlayerResponse`` object.

    Raises:
        HtmlExtractionError: If the player response cannot be found or the
            embedded JSON is malformed.
    """
    _logger.info("Extracting ytInitialPlayerResponse from HTML (%d chars).", len(html))
    return _extract_with_regex(html, _RE_PLAYER_RESPONSE, "ytInitialPlayerResponse")


def extract_ytcfg(html: str) -> dict[str, Any]:
    """Extract the ``ytcfg`` JavaScript configuration object from HTML.

    The ``ytcfg`` object contains configuration values used by the YouTube
    player, including the API key and player JS URL needed by downstream
    extractors.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        A dictionary representing the ``ytcfg`` object, or an empty dict if
        not found.
    """
    _logger.info("Extracting ytcfg from HTML (%d chars).", len(html))
    try:
        return _extract_with_regex(html, _RE_YTCFG, "ytcfg")
    except HtmlExtractionError:
        _logger.warning("ytcfg not found in HTML; returning empty dict.")
        return {}


def extract_sts(html: str) -> str:
    """Extract the ``sts`` (session token) value from the HTML.

    The ``sts`` token is a numeric string embedded in the page's JavaScript
    and is required for certain YouTube API calls.  It is searched both
    inside ``ytcfg`` and as a bare ``"sts":<digits>`` pattern.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        The ``sts`` token as a string, or an empty string if not found.
    """
    _logger.info("Extracting sts token from HTML (%d chars).", len(html))
    match = _RE_STS.search(html)
    if match:
        sts_value = match.group(1)
        _logger.debug("Found sts token: %s", sts_value)
        return sts_value
    _logger.warning("sts token not found in HTML.")
    return ""


def extract_initial_data(html: str) -> dict[str, Any]:
    """Extract the ``ytInitialData`` JavaScript object from HTML.

    ``ytInitialData`` contains the initial React/Navigation data used to
    render the page chrome (sidebar, header, etc.).  It is separate from
    ``ytInitialPlayerResponse`` but follows the same ``var X = {...}``
    embedding pattern.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        A dictionary representing ``ytInitialData``, or an empty dict if not
        found.
    """
    _logger.info("Extracting ytInitialData from HTML (%d chars).", len(html))
    try:
        return _extract_with_regex(html, _RE_INITIAL_DATA, "ytInitialData")
    except HtmlExtractionError:
        _logger.warning("ytInitialData not found in HTML; returning empty dict.")
        return {}


def find_video_id_from_html(html: str) -> str:
    """Extract the YouTube video ID from HTML meta tags and URL patterns.

    Searches in the following order of preference:

    1. ``<meta itemprop="videoId" content="...">``
    2. ``<meta property="og:url" content="...">`` (URL containing ``v=``)
    3. ``<link rel="canonical" href="...">``
    4. Any YouTube watch/embed/shorts/live URL path in the HTML source

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        The 11-character video ID string.

    Raises:
        HtmlExtractionError: If no video ID could be found in the HTML.
    """
    _logger.info("Extracting video ID from HTML (%d chars).", len(html))

    # Strategy 1: meta itemprop or og:url
    match = _RE_VIDEO_ID_META.search(html)
    if match:
        video_id = match.group(1)
        _logger.debug("Video ID found via meta tag: %s", video_id)
        return video_id

    # Strategy 2: canonical link
    match = _RE_VIDEO_ID_CANONICAL.search(html)
    if match:
        video_id = match.group(1)
        _logger.debug("Video ID found via canonical link: %s", video_id)
        return video_id

    # Strategy 3: any YouTube URL path in the HTML
    match = _RE_VIDEO_ID_PATH.search(html)
    if match:
        video_id = match.group(1)
        _logger.debug("Video ID found via URL path: %s", video_id)
        return video_id

    raise HtmlExtractionError(
        "Could not locate a YouTube video ID in the provided HTML. "
        "Expected an 11-character ID in a meta tag, canonical link, or URL path."
    )


def is_age_gated(html: str) -> bool:
    """Detect whether the YouTube page contains an age-gate restriction.

    Searches the HTML for JavaScript boolean flags and text indicators that
    YouTube embeds when a video requires age verification.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        ``True`` if age-gate indicators were found, ``False`` otherwise.
    """
    _logger.debug("Checking for age-gate indicators in HTML.")
    is_gated = bool(_RE_AGE_GATE.search(html))

    if is_gated:
        _logger.info("Age gate detected in page HTML.")

    # Cross-check with player response if present
    try:
        player_response = extract_player_response(html)
        playability = player_response.get("playabilityStatus", {})
        status = playability.get("status", "")
        if status in ("AGE_CHECK_REQUIRED", "AGE_VERIFICATION_REQUIRED"):
            _logger.info(
                "Age gate confirmed via playabilityStatus=%s.", status
            )
            return True
    except HtmlExtractionError:
        pass

    return is_gated


def is_geo_restricted(html: str) -> bool:
    """Detect whether the YouTube page indicates geo-restriction.

    Searches the HTML for JavaScript flags that signal the video is not
    available in the current geographic region.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        ``True`` if geo-restriction indicators were found, ``False`` otherwise.
    """
    _logger.debug("Checking for geo-restriction indicators in HTML.")
    is_restricted = bool(_RE_GEO_RESTRICTED.search(html))

    if is_restricted:
        _logger.info("Geo-restriction detected in page HTML.")

    # Cross-check with player response if present
    try:
        player_response = extract_player_response(html)
        playability = player_response.get("playabilityStatus", {})
        status = playability.get("status", "")
        if status in ("AGE_CHECK_REQUIRED", "AGE_VERIFICATION_REQUIRED"):
            return True
        reason = playability.get("reason", "")
        if "country" in reason.lower() or "available" in reason.lower():
            _logger.info(
                "Geo-restriction confirmed via playabilityStatus reason: %s.",
                reason,
            )
            return True
    except HtmlExtractionError:
        pass

    return is_restricted


def extract_player_response_from_url(
    url: str,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch a YouTube watch page and extract ``ytInitialPlayerResponse``.

    Convenience function that combines an HTTP GET request with
    :func:`extract_player_response`.  A standard set of browser-like request
    headers is sent unless overridden via *headers*.

    Args:
        url: YouTube watch URL (e.g. ``https://www.youtube.com/watch?v=...``).
        headers: Optional additional HTTP headers merged with the defaults.
        timeout: Request timeout in seconds (default 30).

    Returns:
        Parsed ``ytInitialPlayerResponse`` dictionary.

    Raises:
        HtmlExtractionError: If the page cannot be fetched or the player
            response cannot be extracted.
        NetworkError: If the HTTP request fails.
    """
    _logger.info("Fetching YouTube watch page: %s", url)

    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    if headers:
        default_headers.update(headers)

    try:
        response = requests.get(url, headers=default_headers, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise NetworkError(
            f"HTTP error fetching {url}: {exc}",
            cause=exc,
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise NetworkError(
            f"Connection error fetching {url}: {exc}",
            cause=exc,
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise NetworkError(
            f"Timeout fetching {url} after {timeout}s: {exc}",
            cause=exc,
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise NetworkError(
            f"Failed to fetch {url}: {exc}",
            cause=exc,
        ) from exc

    html = response.text
    _logger.debug(
        "Fetched %d bytes from %s (status %d).",
        len(html),
        url,
        response.status_code,
    )

    return extract_player_response(html)


def extract_player_response_with_retry(
    url: str,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
    max_attempts: int = 3,
    backoff_base: float = 1.0,
) -> dict[str, Any]:
    """Fetch and extract with automatic retry on transient HTTP errors.

    Retries on HTTP 429 and 5xx status codes using exponential backoff with
    jitter.  Does not retry on 4xx client errors (other than 429).

    Args:
        url: YouTube watch URL.
        headers: Optional additional HTTP headers.
        timeout: Per-request timeout in seconds.
        max_attempts: Maximum number of fetch attempts (default 3).
        backoff_base: Base delay in seconds for exponential backoff.

    Returns:
        Parsed ``ytInitialPlayerResponse`` dictionary.

    Raises:
        HtmlExtractionError: If extraction fails after all attempts.
        NetworkError: If all HTTP attempts fail.
    """
    import time
    import random

    _logger.info(
        "Fetching player response with up to %d attempts: %s",
        max_attempts,
        url,
    )

    last_exception: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return extract_player_response_from_url(url, headers=headers, timeout=timeout)
        except NetworkError as exc:
            last_exception = exc
            status_code = 0

            if exc.cause is not None:
                response = getattr(exc.cause, "response", None)
                if response is not None:
                    status_code = getattr(response, "status_code", 0)

            retryable = status_code in (429, 500, 502, 503, 504)

            if retryable and attempt < max_attempts:
                delay = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                _logger.warning(
                    "Attempt %d/%d failed with HTTP %d for %s. "
                    "Retrying in %.2fs…",
                    attempt,
                    max_attempts,
                    status_code,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue

            if not retryable:
                raise

        except HtmlExtractionError:
            raise

    raise HtmlExtractionError(
        f"Failed to extract player response from {url} after "
        f"{max_attempts} attempt(s).",
        cause=last_exception,
    ) from last_exception


# ---------------------------------------------------------------------------
# Batch extraction helper
# ---------------------------------------------------------------------------


def extract_all(html: str) -> dict[str, Any]:
    """Extract all available data from a YouTube watch page in one call.

    Runs all primary extractors and returns their results in a single
    dictionary.  Missing data is represented as an empty dict or empty
    string rather than raising an exception.

    Args:
        html: Raw HTML source of a YouTube watch page.

    Returns:
        Dictionary with the following keys:

        * ``player_response`` – ``ytInitialPlayerResponse`` dict or ``{}``.
        * ``ytcfg`` – ``ytcfg`` dict or ``{}``.
        * ``initial_data`` – ``ytInitialData`` dict or ``{}``.
        * ``sts`` – ``sts`` token string (may be empty).
        * ``video_id`` – 11-character video ID string.
        * ``is_age_gated`` – ``bool``.
        * ``is_geo_restricted`` – ``bool``.
    """
    _logger.info("Running full extraction on HTML (%d chars).", len(html))

    player_response: dict[str, Any] = {}
    ytcfg: dict[str, Any] = {}
    initial_data: dict[str, Any] = {}
    sts: str = ""
    video_id: str = ""
    age_gated: bool = False
    geo_restricted: bool = False

    try:
        player_response = extract_player_response(html)
    except HtmlExtractionError as exc:
        _logger.warning("extract_player_response failed: %s", exc)

    try:
        ytcfg = extract_ytcfg(html)
    except HtmlExtractionError as exc:
        _logger.warning("extract_ytcfg failed: %s", exc)

    try:
        initial_data = extract_initial_data(html)
    except HtmlExtractionError as exc:
        _logger.warning("extract_initial_data failed: %s", exc)

    sts = extract_sts(html)

    try:
        video_id = find_video_id_from_html(html)
    except HtmlExtractionError as exc:
        _logger.warning("find_video_id_from_html failed: %s", exc)
        if player_response:
            video_id = player_response.get("videoDetails", {}).get(
                "videoId", ""
            )

    age_gated = is_age_gated(html)
    geo_restricted = is_geo_restricted(html)

    return {
        "player_response": player_response,
        "ytcfg": ytcfg,
        "initial_data": initial_data,
        "sts": sts,
        "video_id": video_id,
        "is_age_gated": age_gated,
        "is_geo_restricted": geo_restricted,
    }


# ---------------------------------------------------------------------------
# Playability status helpers
# ---------------------------------------------------------------------------


def get_playability_status(
    player_response: dict[str, Any],
) -> dict[str, Any]:
    """Return the ``playabilityStatus`` section from a player response.

    This is a convenience accessor for the ``playabilityStatus`` key which
    contains the ``status`` (e.g. ``"OK"``, ``"AGE_CHECK_REQUIRED"``,
    ``"AGE_VERIFICATION_REQUIRED"``, ``"UNPLAYABLE"``, ``"LIVE_STREAM_OFFLINE"``)
    and an optional ``reason`` string.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        The ``playabilityStatus`` sub-dict, or an empty dict if absent.
    """
    return player_response.get("playabilityStatus", {})


def is_video_playable(player_response: dict[str, Any]) -> bool:
    """Check whether the video is actually playable.

    A video is considered playable when ``playabilityStatus.status`` equals
    ``"OK"``.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        ``True`` when the video can be streamed, ``False`` otherwise.
    """
    status = get_playability_status(player_response).get("status", "")
    return status == "OK"


def get_playability_reason(player_response: dict[str, Any]) -> str:
    """Return the human-readable reason for a video being unplayable.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        The ``reason`` string from ``playabilityStatus``, or an empty string
        if the video is playable or the reason is not provided.
    """
    if is_video_playable(player_response):
        return ""
    return get_playability_status(player_response).get("reason", "")


def get_video_details(player_response: dict[str, Any]) -> dict[str, Any]:
    """Return the ``videoDetails`` section from a player response.

    ``videoDetails`` contains metadata such as ``videoId``, ``title``,
    ``author``, ``lengthSeconds``, ``viewCount``, ``isLive``, and
    ``isPrivate``.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        The ``videoDetails`` sub-dict, or an empty dict if absent.
    """
    return player_response.get("videoDetails", {})


def get_streaming_data(player_response: dict[str, Any]) -> dict[str, Any]:
    """Return the ``streamingData`` section from a player response.

    ``streamingData`` contains the ``formats`` and ``adaptiveFormats`` lists
    with per-stream URL and codec information.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        The ``streamingData`` sub-dict, or an empty dict if absent.
    """
    return player_response.get("streamingData", {})


# ---------------------------------------------------------------------------
# Microformat / metadata helpers
# ---------------------------------------------------------------------------


def get_microformat(player_response: dict[str, Any]) -> dict[str, Any]:
    """Return the ``microformat`` section from a player response.

    ``microformat`` contains enriched metadata such as ``playerMicroformatRenderer``
    with publish date, category, and channel information.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        The ``microformat`` sub-dict, or an empty dict if absent.
    """
    return player_response.get("microformat", {})


def get_captions(player_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the caption tracks from a player response.

    Args:
        player_response: Parsed ``ytInitialPlayerResponse`` dictionary.

    Returns:
        List of caption track dictionaries from
        ``captions.playerCaptionsTracklistRenderer.captionTracks``.
        Returns an empty list if no captions are present.
    """
    captions_node = (
        player_response.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    return list(captions_nodes if isinstance(captions_node, list) else [])


# ---------------------------------------------------------------------------
# Text / preview utilities
# ---------------------------------------------------------------------------


def get_page_title(html: str) -> str:
    """Extract the ``<title>`` text from raw HTML.

    Args:
        html: Raw HTML source string.

    Returns:
        The page title text, or an empty string if no ``<title>`` tag found.
    """
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1)
        raw = re.sub(r"<[^>]+>", "", raw)
        return raw.strip()
    return ""


def get_og_title(html: str) -> str:
    """Extract the Open Graph title from HTML meta tags.

    Args:
        html: Raw HTML source string.

    Returns:
        The ``og:title`` content, or an empty string if not present.
    """
    match = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""


def get_og_description(html: str) -> str:
    """Extract the Open Graph description from HTML meta tags.

    Args:
        html: Raw HTML source string.

    Returns:
        The ``og:description`` content, or an empty string if not present.
    """
    match = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""


def get_og_thumbnail(html: str) -> str:
    """Extract the Open Graph thumbnail URL from HTML meta tags.

    Args:
        html: Raw HTML source string.

    Returns:
        The ``og:image`` content URL, or an empty string if not present.
    """
    match = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""


def get_channel_id_from_html(html: str) -> str:
    """Extract the YouTube channel ID from the HTML.

    Searches for the channel ID in meta tags and URL patterns embedded in
    the page.

    Args:
        html: Raw HTML source string.

    Returns:
        The channel ID string, or an empty string if not found.
    """
    match = re.search(
        r'<meta[^>]+itemprop=["\']channelId["\'][^>]+content=["\']'
        r'(UC[A-Za-z0-9_-]{22})'
        r'["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(
        r'"channelId"\s*:\s*"([^"]+)"',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)

    return ""


def get_upload_date_from_html(html: str) -> str:
    """Extract the upload date from HTML meta tags.

    Searches for ``<meta itemprop="datePublished">`` and falls back to
    ``uploadDate`` in JSON-LD structured data.

    Args:
        html: Raw HTML source string.

    Returns:
        The date string (typically ``YYYY-MM-DD`` or ``YYYYMMDD``),
        or an empty string if not found.
    """
    match = re.search(
        r'<meta[^>]+itemprop=["\'](?:datePublished|uploadDate)["\'][^>]+content=["\']'
        r'([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    match = re.search(
        r'"uploadDate"\s*:\s*"([^"]+)"',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    return ""


# ---------------------------------------------------------------------------
# HTML snippet utilities
# ---------------------------------------------------------------------------


def get_script_content(html: str, identifier: str) -> str:
    """Extract the content of a ``<script>`` tag containing *identifier*.

    Searches for a ``<script>`` tag whose ``id`` or inner text contains
    *identifier* and returns its inner text content.

    Args:
        html: Raw HTML source string.
        identifier: Substring that must appear in the script tag's ``id``
            attribute or text content.

    Returns:
        Inner text of the matching ``<script>`` tag, or an empty string.
    """
    pattern = re.compile(
        r"<script[^>]*>.*?" + re.escape(identifier) + r".*?</script>",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        content = match.group(0)
        content = re.sub(r"<script[^>]*>", "", content, flags=re.IGNORECASE)
        content = re.sub(r"</script>", "", content, flags=re.IGNORECASE)
        return content.strip()
    return ""


def strip_html_tags(html: str) -> str:
    """Remove all HTML tags from a string.

    Args:
        html: Raw HTML string.

    Returns:
        Plain text with all ``<...>`` tags removed.
    """
    return re.sub(r"<[^>]+>", "", html)


def truncate(text: str, max_length: int = 200) -> str:
    """Truncate *text* to *max_length* characters, appending ``"..."`` if cut.

    Args:
        text: Input string.
        max_length: Maximum length of the returned string (including the
            ``"..."`` suffix when truncation occurs).

    Returns:
        Possibly truncated string.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


# ---------------------------------------------------------------------------
# JSON path helpers (for player response traversal)
# ---------------------------------------------------------------------------


def json_get(d: Any, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dictionaries by key path.

    Args:
        d: Root dictionary (or any nested structure).
        *keys: Sequence of keys to traverse in order.
        default: Value returned when any key in the path is missing.

    Returns:
        The value at ``d[key1][key2]...[keyN]``, or *default* if the path
        does not exist.
    """
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
            if current is None:
                return default
        else:
            return default
    return current


def json_get_list(d: Any, *keys: str, default: list[Any] | None = None) -> list[Any]:
    """Safely traverse nested dicts and return a list value.

    Like :func:`json_get` but verifies the final value is a list.

    Args:
        d: Root dictionary.
        *keys: Key path.
        default: Fallback value when the path is missing or not a list.

    Returns:
        List at the key path, or *default*.
    """
    if default is None:
        default = []
    result = json_get(d, *keys, default=default)
    return result if isinstance(result, list) else default


def json_get_str(d: Any, *keys: str, default: str = "") -> str:
    """Safely traverse nested dicts and return a string value.

    Like :func:`json_get` but verifies the final value is a string and
    converts non-string values using :class:`str`.

    Args:
        d: Root dictionary.
        *keys: Key path.
        default: Fallback value when the path is missing.

    Returns:
        String at the key path, or *default*.
    """
    result = json_get(d, *keys, default=None)
    if result is None:
        return default
    return str(result)
