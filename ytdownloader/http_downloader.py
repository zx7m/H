"""
Native HTTP downloader with retry logic, 403 handling, and progress reporting.

This module provides the core download pipeline for the native YouTube downloader.
It downloads stream files via HTTP range requests with progress reporting,
forwards cookies from the initial page fetch, uses realistic browser headers,
retries on 403/429/5xx with exponential backoff (max 5 retries), and supports
audio-to-mp3 conversion when ffmpeg is available.

Public API
----------
    - :func:`download_stream` — download a single stream URL to a file.
    - :func:`download_audio_from_info` — download the best audio stream.
    - :func:`download_video_from_info` — download the best video stream.
    - :func:`compute_output_path` — derive a clean output path from video info.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .constants import (
    DEFAULT_ACCEPT_HEADER,
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAY_BASE,
    DEFAULT_USER_AGENT,
)
from .exceptions import DownloadError, NResolverError, StreamResolutionError
from .n_resolver import resolve_n_param

logger = logging.getLogger(__name__)

_ALTERNATE_USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.2 Safari/605.1.15"
    ),
]

_HAS_FFMPEG = shutil.which("ffmpeg") is not None

_RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}


def _sanitize_filename(text: str) -> str:
    """Replace characters that are unsafe in filenames.

    Args:
        text: Raw text (e.g. video title).

    Returns:
        A sanitized string safe for use as a filename component.
    """
    import re
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', text)
    text = text.strip(". _")
    if not text:
        text = "download"
    return text


def compute_output_path(
    info: Dict[str, Any],
    output_dir: str = ".",
    ext: str = "mp4",
) -> str:
    """Derive a clean output file path from video metadata.

    The filename is ``{sanitized_title} [{video_id}].{ext}``.

    Args:
        info: Video info dict as returned by :func:`get_video_info`.  Must
            contain ``title`` and ``id`` (or ``video_id``) keys.
        output_dir: Directory in which to place the file.
        ext: File extension (without leading dot).

    Returns:
        Full output path string.
    """
    title = info.get("title") or info.get("videoDetails", {}).get("title") or "untitled"
    video_id = info.get("id") or info.get("videoDetails", {}).get("videoId") or "unknown"
    safe_title = _sanitize_filename(str(title))
    safe_ext = _sanitize_filename(str(ext)).lstrip(".")
    filename = f"{safe_title} [{video_id}].{safe_ext}"
    return os.path.join(output_dir, filename)


def _build_base_headers(
    url: str,
    extra_headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, str]:
    """Build a complete set of request headers for a stream download.

    Args:
        url: The target URL (used to derive Origin/Referer).
        extra_headers: Additional headers to merge in.
        cookies: Optional cookie dict — included as a ``Cookie`` header when
            provided.
        user_agent: User-Agent string override.

    Returns:
        A header dict suitable for use with ``requests``.
    """
    parsed = requests.utils.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    referer = f"{origin}/"

    if "youtube.com" in parsed.netloc:
        referer = "https://www.youtube.com/"

    headers: Dict[str, str] = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
        "Referer": referer,
        "Origin": origin,
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": '"Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Accept-Encoding": "identity",
    }

    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers["Cookie"] = cookie_str

    if extra_headers:
        headers.update(extra_headers)

    return headers


def _make_session(headers: Dict[str, str]) -> requests.Session:
    """Create a ``requests.Session`` pre-configured with *headers*.

    Args:
        headers: Header dict to set on the session.

    Returns:
        A new :class:`requests.Session` instance.
    """
    session = requests.Session()
    session.headers.update(headers)
    return session


def _retry_delay(attempt: int, base: float = DEFAULT_RETRY_DELAY_BASE) -> float:
    """Calculate exponential backoff delay for a given retry attempt.

    Args:
        attempt: Zero-based retry attempt index.
        base: Base delay in seconds.

    Returns:
        Delay in seconds (``base * 2**attempt``).
    """
    return base * (2 ** attempt)


def _try_resolve_n(url: str, js_url: Optional[str]) -> Optional[str]:
    """Attempt to resolve the n-parameter for a stream URL.

    If *url* contains an ``n=`` parameter the value is resolved using
    :func:`resolve_n_param`.  If resolution fails the original URL is
    returned unchanged.

    Args:
        url: Stream URL that may contain an ``n`` parameter.
        js_url: Player JS URL for n-parameter resolution.

    Returns:
        The URL with (possibly) resolved ``n`` parameter, or the original
        *url* if no n-parameter is present or resolution fails.
    """
    if "n=" not in url or not js_url:
        return url
    try:
        from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
        parsed = urlparse(url)
        qp = parse_qs(parsed.query)
        raw_n = qp.get("n", [None])[0]
        if raw_n:
            resolved_n = resolve_n_param(js_url, raw_n)
            qp["n"] = [resolved_n]
            new_query = urlencode(qp, doseq=True)
            url = urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            ))
    except Exception as exc:
        logger.debug("n-parameter resolution failed: %s", exc)
    return url


def _get_content_length(headers: Dict[str, str]) -> Optional[int]:
    """Extract ``Content-Length`` from response headers.

    Args:
        headers: Response headers dict.

    Returns:
        Integer byte count, or ``None`` if the header is absent or unparseable.
    """
    cl = headers.get("Content-Length") or headers.get("content-length")
    if cl is None:
        return None
    try:
        return int(cl.strip())
    except (ValueError, TypeError):
        return None


def _http_get_with_retry(
    url: str,
    session: requests.Session,
    max_retries: int = DEFAULT_MAX_RETRIES,
    stream: bool = True,
    timeout: int = 30,
    headers_override: Optional[Dict[str, str]] = None,
    cookies_override: Optional[Dict[str, str]] = None,
    js_url: Optional[str] = None,
    n_value: Optional[str] = None,
) -> requests.Response:
    """Perform an HTTP GET with automatic retry on transient errors.

    Retry logic
    -----------
    - 403: tries up to 5 alternative User-Agent strings (each attempt rotates
      to the next UA).  On the 403 retries it also tries with/without Cookie
      headers, and if *n_value* is given, appends a resolved n-parameter to
      the URL.
    - 429/5xx: retries with exponential backoff up to *max_retries* times.

    Args:
        url: Target URL.
        session: Pre-configured :class:`requests.Session`.
        max_retries: Maximum number of retry attempts (total attempts =
            ``1 + max_retries``).
        stream: Passed to ``session.get``.
        timeout: Request timeout in seconds.
        headers_override: Additional headers applied on every retry.
        cookies_override: Cookie dict — applied on every retry.
        js_url: Player JS URL used for n-parameter resolution on 403 retries.
        n_value: Raw n-value string from the stream URL — used to resolve and
            append a corrected n-parameter on 403 retries.

    Returns:
        A successful :class:`requests.Response`.

    Raises:
        DownloadError: If all retry attempts fail.
    """
    last_exc: Optional[Exception] = None
    attempt_count = 0
    max_total = max_retries + 1

    ua_rotation_index = 0
    use_cookies = bool(cookies_override)

    while attempt_count < max_total:
        attempt = attempt_count
        attempt_count += 1

        current_headers = dict(session.headers)
        if headers_override:
            current_headers.update(headers_override)

        if attempt > 0 and attempt % len(_ALTERNATE_USER_AGENTS) == 0:
            ua_rotation_index = min(ua_rotation_index + 1, len(_ALTERNATE_USER_AGENTS) - 1)

        if attempt > 0:
            alt_ua = _ALTERNATE_USER_AGENTS[ua_rotation_index % len(_ALTERNATE_USER_AGENTS)]
            current_headers["User-Agent"] = alt_ua

        current_cookies = cookies_override if use_cookies else None

        try:
            response = session.get(
                url,
                stream=stream,
                timeout=timeout,
                headers=current_headers,
                cookies=current_cookies,
            )
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Request attempt %d failed with exception: %s", attempt, exc)
            if attempt_count < max_total:
                delay = _retry_delay(attempt)
                logger.info("Retrying in %.1fs (attempt %d/%d).", delay, attempt + 1, max_total)
                time.sleep(delay)
            continue

        status = response.status_code

        if status not in _RETRYABLE_STATUS_CODES or status < 400:
            if status >= 400:
                response.raise_for_status()
            return response

        last_exc = requests.HTTPError(
            f"{response.status_code} {response.reason} for {url}",
            response=response,
        )

        is_403 = status == 403
        is_retryable = status in {403, 429, 500, 502, 503, 504}

        if not is_retryable or attempt_count >= max_total:
            logger.error(
                "HTTP %d for %s after %d attempt(s): %s",
                status,
                url,
                attempt_count,
                last_exc,
            )
            raise DownloadError(
                f"HTTP {status} for {url} after {attempt_count} attempt(s): {last_exc}"
            ) from last_exc

        backoff = _retry_delay(attempt)
        logger.warning(
            "HTTP %d for %s (attempt %d/%d) — retrying in %.1fs.",
            status,
            url,
            attempt_count,
            max_total,
            backoff,
        )

        if is_403:
            url = _try_resolve_n(url, js_url)

            if attempt_count % 2 == 0:
                use_cookies = not use_cookies
                logger.debug("403 retry: toggled cookies %s.", "on" if use_cookies else "off")

        time.sleep(backoff)

    raise DownloadError(
        f"All {attempt_count} download attempts failed for {url}: {last_exc}"
    ) from last_exc


def download_stream(
    url: str,
    output_path: str,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    progress_callback: Optional[Any] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    expected_size: Optional[int] = None,
    js_url: Optional[str] = None,
) -> str:
    """Download a stream URL to *output_path* with progress reporting.

    Uses HTTP range requests and reports progress via *progress_callback*.
    Retries on 403/429/5xx with exponential backoff (max 5 retries by default).

    Args:
        url: Direct stream URL.
        output_path: Full path to the output file.
        headers: Base request headers (merged with realistic defaults).
        cookies: Cookie dict forwarded from the initial page fetch.
        progress_callback: Callable ``f(downloaded, total, speed)`` called
            after each chunk.  *speed* is in bytes/second.
        max_retries: Maximum number of retry attempts (total = max_retries + 1).
        expected_size: Expected file size in bytes (used for progress
            calculation if Content-Length is not in the response).
        js_url: Player JS URL used for n-parameter resolution on 403 retries.

    Returns:
        The *output_path* on success.

    Raises:
        DownloadError: If the download fails after all retry attempts.
    """
    base_headers = _build_base_headers(url, extra_headers=headers, cookies=cookies)
    session = _make_session(base_headers)

    n_value = None
    if "n=" in url:
        try:
            from urllib.parse import parse_qs, urlparse
            parsed_url = urlparse(url)
            qp = parse_qs(parsed_url.query)
            n_value = qp.get("n", [None])[0]
        except Exception:
            pass

    logger.info("Downloading stream to: %s", output_path)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    response = _http_get_with_retry(
        url,
        session,
        max_retries=max_retries,
        stream=True,
        timeout=60,
        headers_override=headers,
        cookies_override=cookies,
        js_url=js_url,
        n_value=n_value,
    )

    content_length = _get_content_length(response.headers) or expected_size

    downloaded = 0
    start_time = time.monotonic()
    last_chunk_time = start_time
    last_chunk_bytes = 0

    try:
        with open(output_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)

                now = time.monotonic()
                elapsed_chunk = now - last_chunk_time
                if elapsed_chunk >= 0.5:
                    chunk_delta = downloaded - last_chunk_bytes
                    speed = chunk_delta / elapsed_chunk if elapsed_chunk > 0 else 0.0
                    last_chunk_time = now
                    last_chunk_bytes = downloaded

                    if progress_callback is not None:
                        try:
                            progress_callback(downloaded, content_length, speed)
                        except Exception as exc:
                            logger.debug("Progress callback raised: %s", exc)

        # Always fire a final progress report so callers see the completed state.
        if progress_callback is not None and downloaded != last_chunk_bytes:
            try:
                total_elapsed = time.monotonic() - start_time
                final_speed = downloaded / total_elapsed if total_elapsed > 0 else 0.0
                progress_callback(downloaded, content_length, final_speed)
            except Exception as exc:
                logger.debug("Final progress callback raised: %s", exc)

    except requests.RequestException as exc:
        raise DownloadError(f"Download interrupted: {exc}") from exc
    finally:
        response.close()

    if content_length and downloaded < content_length:
        logger.warning(
            "Download incomplete: got %d of %d expected bytes.",
            downloaded,
            content_length,
        )
    elif content_length is None:
        logger.debug("Downloaded %d bytes (content-length unknown).", downloaded)
    else:
        logger.info("Download complete: %d bytes.", downloaded)

    return output_path


def _convert_to_mp3(input_path: str, output_path: str) -> str:
    """Convert an audio file to MP3 using ffmpeg.

    Args:
        input_path: Path to the source audio file.
        output_path: Desired MP3 output path (``.mp3`` extension).

    Returns:
        The *output_path* on success.

    Raises:
        DownloadError: If ffmpeg is not available or conversion fails.
    """
    if not _HAS_FFMPEG:
        raise DownloadError(
            "ffmpeg is not installed; cannot convert audio to MP3. "
            "Install ffmpeg or save in native format."
        )

    mp3_path = os.path.splitext(output_path)[0] + ".mp3"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        mp3_path,
    ]
    logger.info("Converting audio to MP3: %s -> %s", input_path, mp3_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise DownloadError(
                f"ffmpeg conversion failed (rc={result.returncode}): "
                f"{result.stderr[-500:] if result.stderr else ''}"
            )
    except subprocess.TimeoutExpired as exc:
        raise DownloadError("ffmpeg conversion timed out.") from exc
    except FileNotFoundError as exc:
        raise DownloadError("ffmpeg binary not found.") from exc

    try:
        os.remove(input_path)
    except OSError:
        pass

    return mp3_path


def _select_audio_format(formats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Select the best audio-only format from a list of raw format dicts.

    Prefers formats with higher audio bitrate (``abr``).

    Args:
        formats: List of raw YouTube format dicts.

    Returns:
        The best audio format dict.

    Raises:
        DownloadError: If no audio format is found.
    """
    audio_formats = [
        fmt for fmt in formats
        if fmt.get("acodec", "none") != "none"
        and fmt.get("vcodec", "none") == "none"
    ]
    if not audio_formats:
        audio_formats = [
            fmt for fmt in formats
            if fmt.get("acodec", "none") != "none"
        ]
    if not audio_formats:
        raise DownloadError("No audio format available for download.")

    audio_formats.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    return audio_formats[0]


def _select_video_format(
    formats: List[Dict[str, Any]],
    prefer_mp4: bool = True,
) -> Dict[str, Any]:
    """Select the best video (or combined) format from a list of raw format dicts.

    Prefers combined (progressive) formats when available, then falls back to
    the highest-quality video-only format.

    Args:
        formats: List of raw YouTube format dicts.
        prefer_mp4: If ``True``, prefer MP4 container formats.

    Returns:
        The best video format dict.

    Raises:
        DownloadError: If no video format is found.
    """
    candidates = list(formats)

    if prefer_mp4:
        mp4 = [f for f in candidates if (f.get("ext") or "").lower() == "mp4"]
        if mp4:
            candidates = mp4

    combined = [
        f for f in candidates
        if f.get("vcodec", "none") != "none" and f.get("acodec", "none") != "none"
    ]
    if combined:
        combined.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
        return combined[0]

    video_only = [
        f for f in candidates
        if f.get("vcodec", "none") != "none"
    ]
    if video_only:
        video_only.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
        return video_only[0]

    raise DownloadError("No video format available for download.")


def _resolve_stream_url(fmt: Dict[str, Any], js_url: Optional[str]) -> str:
    """Resolve the direct stream URL from a format dict.

    Handles both plain ``url`` and ``signatureCipher`` fields.

    Args:
        fmt: Raw YouTube format dict.
        js_url: Player JS URL for cipher/n-parameter resolution.

    Returns:
        Direct, usable stream URL.

    Raises:
        StreamResolutionError: If the format has no usable URL.
    """
    cipher_value = fmt.get("signatureCipher")
    if cipher_value:
        from .cipher import decipher_url, parse_signature_cipher
        deciphered = decipher_url(cipher_value, js_url)
        if js_url:
            try:
                cipher_data = parse_signature_cipher(cipher_value)
                raw_n = cipher_data.get("n")
                if raw_n:
                    deciphered = _try_resolve_n(deciphered, js_url)
            except Exception as exc:
                logger.debug("n-parameter resolution failed after deciphering: %s", exc)
        return deciphered

    raw_url = fmt.get("url")
    if raw_url:
        if js_url and "n=" in raw_url:
            return _try_resolve_n(raw_url, js_url)
        return raw_url

    raise StreamResolutionError(
        f"Format itag={fmt.get('itag')} has neither 'url' nor 'signatureCipher'."
    )


def download_audio_from_info(
    info: Dict[str, Any],
    output_path: str = ".",
    progress_callback: Optional[Any] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    cookies: Optional[Dict[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Download the best audio stream from a video info dict.

    Selects the highest-bitrate audio-only format, downloads it, and converts
    to MP3 if ``ffmpeg`` is available.  Falls back to the native container
    (webm/opus or m4a/aac) when ffmpeg is absent.

    Args:
        info: Video info dict as returned by :func:`get_video_info`.
        output_path: Output directory (file name is derived automatically).
        progress_callback: ``f(downloaded, total, speed)`` progress function.
        max_retries: Maximum retry attempts per download.
        cookies: Cookie dict forwarded from the initial page fetch.
        extra_headers: Additional HTTP headers.

    Returns:
        Path to the downloaded (and possibly converted) audio file.
    """
    streaming_data = info.get("streaming_data", {})
    all_formats = (
        streaming_data.get("formats", []) + streaming_data.get("adaptiveFormats", [])
    )
    if not all_formats:
        raise DownloadError("No streaming data available in video info.")

    best_audio_fmt = _select_audio_format(all_formats)

    js_url = None
    try:
        assets = info.get("assets", {})
        js_url = assets.get("js")
    except AttributeError:
        pass
    if js_url is None:
        player_response = info.get("player_response") or info
        if isinstance(player_response, dict):
            js_url = player_response.get("assets", {}).get("js")

    stream_url = _resolve_stream_url(best_audio_fmt, js_url)
    ext = best_audio_fmt.get("ext", "webm")
    out_path = compute_output_path(info, output_dir=output_path, ext=ext)

    logger.info(
        "Downloading audio (itag=%d, ext=%s) to %s.",
        best_audio_fmt.get("itag"),
        ext,
        out_path,
    )

    download_stream(
        url=stream_url,
        output_path=out_path,
        headers=extra_headers,
        cookies=cookies,
        progress_callback=progress_callback,
        max_retries=max_retries,
        expected_size=best_audio_fmt.get("contentLength"),
        js_url=js_url,
    )

    if _HAS_FFMPEG and ext not in ("mp3",):
        try:
            return _convert_to_mp3(out_path, out_path)
        except DownloadError:
            logger.warning("MP3 conversion failed; keeping native format: %s", out_path)

    return out_path


def download_video_from_info(
    info: Dict[str, Any],
    output_path: str = ".",
    progress_callback: Optional[Any] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    cookies: Optional[Dict[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Download the best video stream from a video info dict.

    Selects the highest-quality progressive (combined audio+video) format or,
    if none is available, the best video-only format.  The downloaded file is
    saved with a clean filename derived from the video title and ID.

    Note:
        When only a video-only DASH stream is available the file will contain
        no audio.  Audio/video merging via ffmpeg is not implemented here;
        callers needing merged output should prefer combined formats.

    Args:
        info: Video info dict as returned by :func:`get_video_info`.
        output_path: Output directory (file name is derived automatically).
        progress_callback: ``f(downloaded, total, speed)`` progress function.
        max_retries: Maximum retry attempts per download.
        cookies: Cookie dict forwarded from the initial page fetch.
        extra_headers: Additional HTTP headers.

    Returns:
        Path to the downloaded video file.
    """
    streaming_data = info.get("streaming_data", {})
    all_formats = (
        streaming_data.get("formats", []) + streaming_data.get("adaptiveFormats", [])
    )
    if not all_formats:
        raise DownloadError("No streaming data available in video info.")

    best_video_fmt = _select_video_format(all_formats)

    js_url = None
    try:
        assets = info.get("assets", {})
        js_url = assets.get("js")
    except AttributeError:
        pass
    if js_url is None:
        player_response = info.get("player_response") or info
        if isinstance(player_response, dict):
            js_url = player_response.get("assets", {}).get("js")

    stream_url = _resolve_stream_url(best_video_fmt, js_url)
    ext = best_video_fmt.get("ext", "mp4")
    out_path = compute_output_path(info, output_dir=output_path, ext=ext)

    logger.info(
        "Downloading video (itag=%d, ext=%s) to %s.",
        best_video_fmt.get("itag"),
        ext,
        out_path,
    )

    download_stream(
        url=stream_url,
        output_path=out_path,
        headers=extra_headers,
        cookies=cookies,
        progress_callback=progress_callback,
        max_retries=max_retries,
        expected_size=best_video_fmt.get("contentLength"),
        js_url=js_url,
    )

    return out_path
