"""
Full custom HTTP client for the ytdownloader package.

Wraps :mod:`requests` with automatic retry, cookie management, proxy support,
debug logging, and chunked stream download.  All network interaction flows
through this module so callers never touch :mod:`requests` directly.

Typical usage::

    from ytdownloader.config import YTConfig, load_config
    from ytdownloader.http_client import HttpClient

    config = load_config()
    client = HttpClient(config)

    response = client.get("https://www.youtube.com/watch?v=abc123")
    client.download_stream(stream_url, "output.mp4", expected_size=10_000_000)
"""

from __future__ import annotations

import http.cookiejar
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, Union
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import YTConfig
from .exceptions import (
    ConfigError,
    DownloadError,
    NetworkError,
    YTDLException,
)
from .logger import debug_log_request, debug_log_response, get_logger

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT: int = 30
_DEFAULT_CHUNK_SIZE: int = 1024 * 1024  # 1 MB
_DEFAULT_MAX_RETRIES: int = 3
_DEFAULT_RETRY_DELAY_BASE: float = 1.0

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset(
    {
        requests.codes.request_timeout,   # 408
        requests.codes.too_many_requests, # 429
        requests.codes.internal_server_error,  # 500
        requests.codes.bad_gateway,       # 502
        requests.codes.service_unavailable,   # 503
        requests.codes.gateway_timeout,   # 504
    }
)

_HTTP_METHOD_GET: str = "GET"
_HTTP_METHOD_POST: str = "POST"
_HTTP_METHOD_HEAD: str = "HEAD"

_COOKIE_POLICY_NAMES: dict[str, str] = {
    "netscape": "Netscape HTTP Cookie File",
    "lwp": "LWP cookies",
    "moz": "Mozilla cookies.txt",
}

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, Optional[int], float], None]
"""Signature for the download progress callback.

Args:
    downloaded: Bytes downloaded so far.
    total: Expected total size, or ``None`` when unknown.
    speed: Instantaneous speed in bytes per second.
"""


# ---------------------------------------------------------------------------
# HttpClient
# ---------------------------------------------------------------------------


class HttpClient:
    """High-level HTTP client with retry, cookie, proxy, and debug support.

    All network I/O goes through a single :class:`requests.Session` so that
    cookies, connection pools, and retry settings are shared across requests.

    Attributes:
        config: The :class:`~ytdownloader.config.YTConfig` driving this client.
        session: The underlying :class:`requests.Session` instance.
    """

    def __init__(self, config: YTConfig) -> None:
        """Initialise the HTTP client from a configuration object.

        Args:
            config: A fully-populated :class:`~ytdownloader.config.YTConfig`
                instance.  At minimum the ``user_agent``, ``timeout``,
                ``max_retries``, and ``retry_delay_base`` fields are read.

        Raises:
            ConfigError: If *config* is missing required attributes or has
                obviously invalid values.
        """
        self._config = config
        self._session: requests.Session = self._build_session()
        self._cookies_loaded: bool = False
        self._proxy_url: Optional[str] = config.proxy
        self._retry_stats: dict[str, int] = {
            "total_attempts": 0,
            "total_retries": 0,
            "failed_requests": 0,
        }
        self._last_request_meta: dict[str, Any] = {}

        if config.proxy:
            self.set_proxy(config.proxy)

        if config.cookies_file:
            self.load_cookies_from_file(config.cookies_file)

    # ------------------------------------------------------------------
    # Session construction
    # ------------------------------------------------------------------

    def _build_session(self) -> requests.Session:
        """Build and configure a fresh :class:`requests.Session`.

        Creates a session pre-loaded with the default YouTube user-agent and
        any additional headers supplied via the configuration.  The session is
        fitted with a :class:`~requests.adapters.HTTPAdapter` that mounts a
        :class:`urllib3.util.retry.Retry` policy on both HTTP and HTTPS
        prefixes.

        Returns:
            A fully configured :class:`requests.Session` ready for use.
        """
        session: requests.Session = requests.Session()

        headers: dict[str, str] = {
            "User-Agent": self._config.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        if self._config.headers:
            headers.update(self._config.headers)

        session.headers.update(headers)

        retry_policy = Retry(
            total=self._config.max_retries,
            backoff_factor=self._config.retry_delay_base,
            status_forcelist=list(_RETRYABLE_STATUS_CODES),
            allowed_methods={
                _HTTP_METHOD_HEAD,
                _HTTP_METHOD_GET,
                _HTTP_METHOD_POST,
                _HTTP_METHOD_PUT,
                _HTTP_METHOD_DELETE,
                _HTTP_METHOD_PATCH,
            },
            raise_on_status=False,
            respect_retry_after_header=True,
        )

        adapter = HTTPAdapter(max_retries=retry_policy, pool_connections=20, pool_maxsize=50)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        self._logger.debug(
            "Built requests.Session with max_retries=%d, retry_delay_base=%.2f, user_agent=%s",
            self._config.max_retries,
            self._config.retry_delay_base,
            self._config.user_agent[:40],
        )

        return session

    # ------------------------------------------------------------------
    # Internal logger shortcut
    # ------------------------------------------------------------------

    @property
    def _logger(self) -> logging.Logger:
        """Return the module-level logger bound to this client."""
        return _logger

    # ------------------------------------------------------------------
    # Retry helpers
    # ------------------------------------------------------------------

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        """Determine whether a failed request should be retried.

        A request is retried when:

        * *attempt* is strictly less than ``config.max_retries``.
        * *status_code* is one of the recognised retryable HTTP codes.

        Network-level exceptions (connection errors, timeouts, etc.) are
        always retried up to ``max_retries`` regardless of status code.

        Args:
            status_code: The HTTP status code returned by the server.
            attempt: The zero-based index of the current attempt (0 = first).

        Returns:
            ``True`` if another attempt should be made, ``False`` otherwise.
        """
        if attempt >= self._config.max_retries:
            return False
        return status_code in _RETRYABLE_STATUS_CODES

    def _get_backoff_delay(self, attempt: int) -> float:
        """Compute the sleep duration for a given retry attempt.

        Uses exponential backoff: ``retry_delay_base * (2 ** attempt)`` plus
        a small random jitter (0-0.5 s) to avoid thundering-herd effects.

        Args:
            attempt: The zero-based index of the current retry attempt.

        Returns:
            Number of seconds to sleep before the next attempt.
        """
        base: float = self._config.retry_delay_base * (2**attempt)
        jitter: float = 0.01 * attempt
        return base + jitter

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    def _handle_response(
        self,
        response: requests.Response,
        url: str,
        attempt: int,
    ) -> requests.Response:
        """Validate the response and raise on HTTP error status codes.

        Does **not** retry here – retry orchestration lives in :meth:`get`
        and :meth:`post`.  Raises are only emitted when the final attempt
        is exhausted or when the status code is non-retryable.

        Args:
            response: The raw :class:`requests.Response` to inspect.
            url: The request URL (used in error messages).
            attempt: The zero-based attempt index that produced this response.

        Returns:
            The original *response* when ``response.ok`` is ``True``.

        Raises:
            NetworkError: If the server returns an HTTP error (4xx or 5xx).
        """
        if response.ok:
            return response

        status = response.status_code

        if self._should_retry(status, attempt):
            return response  # caller will loop

        body_preview = ""
        try:
            content_type = response.headers.get("Content-Type", "")
            if "text" in content_type or "json" in content_type or not content_type:
                body_preview = response.text[:500]
        except Exception:  # pragma: no cover - defensive
            body_preview = "(unable to read body)"

        self._logger.error(
            "HTTP %d for %s (attempt %d/%d). Body preview: %s",
            status,
            url,
            attempt + 1,
            self._config.max_retries + 1,
            body_preview,
        )

        if 400 <= status < 500:
            if status == requests.codes.not_found:
                raise NetworkError(
                    f"HTTP {status}: resource not found at {url}.",
                    cause=requests.HTTPError(f"{status} Not Found", response=response),
                )
            if status == requests.codes.forbidden:
                raise NetworkError(
                    f"HTTP {status}: access forbidden for {url}.",
                    cause=requests.HTTPError(f"{status} Forbidden", response=response),
                )
            raise NetworkError(
                f"HTTP {status}: client error for {url}.",
                cause=requests.HTTPError(f"{status} Client Error", response=response),
            )

        raise NetworkError(
            f"HTTP {status}: server error for {url} after {attempt + 1} attempt(s).",
            cause=requests.HTTPError(f"{status} Server Error", response=response),
        )

    # ------------------------------------------------------------------
    # Public HTTP verbs
    # ------------------------------------------------------------------

    def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        stream: bool = False,
        timeout: Optional[Union[int, float, tuple]] = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        """Send an HTTP GET request with automatic retry and backoff.

        The request is retried on connection errors and on retryable HTTP
        status codes (408, 429, 500, 502, 503, 504) up to
        ``config.max_retries`` times.  The delay between retries follows an
        exponential backoff schedule derived from ``config.retry_delay_base``.

        Args:
            url: The target URL.  Must be a well-formed absolute URL.
            params: Optional query-string parameters appended to *url*.
            headers: Additional per-request headers merged with the session
                defaults.
            stream: If ``True``, defer response body download so the caller
                can iterate over :attr:`requests.Response.raw`.
            timeout: Request timeout in seconds.  Pass ``None`` to use
                ``config.timeout``; pass a tuple ``(connect, read)`` for
                per-phase control.
            allow_redirects: Follow HTTP redirects automatically.

        Returns:
            A :class:`requests.Response` object for a successful request.

        Raises:
            NetworkError: If all retry attempts are exhausted or the server
                returns a non-retryable error status.
            YTDLException: If a connection-level error occurs after all
                retries.
        """
        effective_timeout = timeout if timeout is not None else self._config.timeout

        last_exception: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            self._retry_stats["total_attempts"] += 1
            try:
                request_headers = dict(self._session.headers)
                if headers:
                    request_headers.update(headers)

                debug_log_request(
                    url=url,
                    method=_HTTP_METHOD_GET,
                    headers=request_headers,
                )

                response = self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    stream=stream,
                    timeout=effective_timeout,
                    allow_redirects=allow_redirects,
                )

                self._last_request_meta = {
                    "method": _HTTP_METHOD_GET,
                    "url": url,
                    "status_code": response.status_code,
                    "attempt": attempt + 1,
                }

                debug_log_response(
                    status=response.status_code,
                    headers=dict(response.headers),
                )

                if response.ok:
                    self._logger.debug(
                        "GET %s succeeded with status %d on attempt %d.",
                        url,
                        response.status_code,
                        attempt + 1,
                    )
                    return response

                validated = self._handle_response(response, url, attempt)
                if self._should_retry(response.status_code, attempt):
                    self._retry_stats["total_retries"] += 1
                    self._logger.warning(
                        "GET %s returned HTTP %d (attempt %d/%d). "
                        "Retrying in %.2fs…",
                        url,
                        response.status_code,
                        attempt + 1,
                        self._config.max_retries + 1,
                        self._get_backoff_delay(attempt),
                    )
                    time.sleep(self._get_backoff_delay(attempt))
                    continue

                return validated

            except requests.exceptions.ConnectionError as exc:
                last_exception = exc
                self._logger.warning(
                    "GET %s connection error (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

            except requests.exceptions.Timeout as exc:
                last_exception = exc
                self._logger.warning(
                    "GET %s timed out (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

            except requests.exceptions.RequestException as exc:
                last_exception = exc
                self._logger.warning(
                    "GET %s request error (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

        self._retry_stats["failed_requests"] += 1
        raise NetworkError(
            f"GET {url} failed after {self._config.max_retries + 1} attempt(s).",
            cause=last_exception,
        )

    def post(
        self,
        url: str,
        data: Optional[Union[dict[str, Any], str, bytes]] = None,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[Union[int, float, tuple]] = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        """Send an HTTP POST request with automatic retry and backoff.

        Retry semantics are identical to :meth:`get`.  Only the HTTP method
        and the payload arguments differ.

        Args:
            url: The target URL.
            data: Form-encoded body data passed directly to
                :meth:`requests.Session.post` via the ``data`` parameter.
                Accepts a ``dict``, ``str``, or ``bytes`` value.
            json: JSON-encodable body data passed via the ``json`` parameter.
                Mutually exclusive with *data*.
            headers: Additional per-request headers.
            timeout: Request timeout.  Pass ``None`` to use
                ``config.timeout``.
            allow_redirects: Follow HTTP redirects automatically.

        Returns:
            A :class:`requests.Response` for a successful request.

        Raises:
            NetworkError: If all retry attempts are exhausted or the server
                returns a non-retryable error status.
            YTDLException: If a connection-level error occurs after all
                retries.
        """
        effective_timeout = timeout if timeout is not None else self._config.timeout

        last_exception: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            self._retry_stats["total_attempts"] += 1
            try:
                request_headers = dict(self._session.headers)
                if headers:
                    request_headers.update(headers)

                debug_log_request(
                    url=url,
                    method=_HTTP_METHOD_POST,
                    headers=request_headers,
                )

                response = self._session.post(
                    url,
                    data=data,
                    json=json,
                    headers=headers,
                    timeout=effective_timeout,
                    allow_redirects=allow_redirects,
                )

                self._last_request_meta = {
                    "method": _HTTP_METHOD_POST,
                    "url": url,
                    "status_code": response.status_code,
                    "attempt": attempt + 1,
                }

                debug_log_response(
                    status=response.status_code,
                    headers=dict(response.headers),
                )

                if response.ok:
                    self._logger.debug(
                        "POST %s succeeded with status %d on attempt %d.",
                        url,
                        response.status_code,
                        attempt + 1,
                    )
                    return response

                validated = self._handle_response(response, url, attempt)
                if self._should_retry(response.status_code, attempt):
                    self._retry_stats["total_retries"] += 1
                    self._logger.warning(
                        "POST %s returned HTTP %d (attempt %d/%d). "
                        "Retrying in %.2fs…",
                        url,
                        response.status_code,
                        attempt + 1,
                        self._config.max_retries + 1,
                        self._get_backoff_delay(attempt),
                    )
                    time.sleep(self._get_backoff_delay(attempt))
                    continue

                return validated

            except requests.exceptions.ConnectionError as exc:
                last_exception = exc
                self._logger.warning(
                    "POST %s connection error (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

            except requests.exceptions.Timeout as exc:
                last_exception = exc
                self._logger.warning(
                    "POST %s timed out (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

            except requests.exceptions.RequestException as exc:
                last_exception = exc
                self._logger.warning(
                    "POST %s request error (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

        self._retry_stats["failed_requests"] += 1
        raise NetworkError(
            f"POST {url} failed after {self._config.max_retries + 1} attempt(s).",
            cause=last_exception,
        )

    def head(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[Union[int, float, tuple]] = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        """Send an HTTP HEAD request to retrieve headers without the body.

        HEAD requests are useful for checking file size, content type, and
        other response headers before committing to a full download.

        Args:
            url: The target URL.
            headers: Additional per-request headers.
            timeout: Request timeout.  Pass ``None`` to use
                ``config.timeout``.
            allow_redirects: Follow HTTP redirects automatically.

        Returns:
            A :class:`requests.Response` whose ``headers`` attribute contains
            the server's response headers.

        Raises:
            NetworkError: If the request fails after all retries.
            YTDLException: If a connection-level error occurs.
        """
        effective_timeout = timeout if timeout is not None else self._config.timeout

        for attempt in range(self._config.max_retries + 1):
            self._retry_stats["total_attempts"] += 1
            try:
                request_headers = dict(self._session.headers)
                if headers:
                    request_headers.update(headers)

                debug_log_request(
                    url=url,
                    method=_HTTP_METHOD_HEAD,
                    headers=request_headers,
                )

                response = self._session.head(
                    url,
                    headers=headers,
                    timeout=effective_timeout,
                    allow_redirects=allow_redirects,
                )

                self._last_request_meta = {
                    "method": _HTTP_METHOD_HEAD,
                    "url": url,
                    "status_code": response.status_code,
                    "attempt": attempt + 1,
                }

                debug_log_response(
                    status=response.status_code,
                    headers=dict(response.headers),
                )

                if response.ok:
                    self._logger.debug(
                        "HEAD %s succeeded with status %d on attempt %d.",
                        url,
                        response.status_code,
                        attempt + 1,
                    )
                    return response

                validated = self._handle_response(response, url, attempt)
                if self._should_retry(response.status_code, attempt):
                    self._retry_stats["total_retries"] += 1
                    self._logger.warning(
                        "HEAD %s returned HTTP %d (attempt %d/%d). Retrying…",
                        url,
                        response.status_code,
                        attempt + 1,
                        self._config.max_retries + 1,
                    )
                    time.sleep(self._get_backoff_delay(attempt))
                    continue

                return validated

            except requests.exceptions.ConnectionError as exc:
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

            except requests.exceptions.Timeout as exc:
                if attempt < self._config.max_retries:
                    self._retry_stats["total_retries"] += 1
                    time.sleep(self._get_backoff_delay(attempt))

        self._retry_stats["failed_requests"] += 1
        raise NetworkError(
            f"HEAD {url} failed after {self._config.max_retries + 1} attempt(s)."
        )

    # ------------------------------------------------------------------
    # Proxy
    # ------------------------------------------------------------------

    def set_proxy(self, proxy_url: str) -> None:
        """Configure an HTTP/HTTPS proxy for all subsequent requests.

        Both ``http://`` and ``https://`` schemes are configured from the
        single *proxy_url*.  Passing ``None`` or an empty string removes
        any previously set proxy.

        Args:
            proxy_url: Proxy URL (e.g. ``"http://proxy.local:8080"``).
                Pass ``None`` to clear the proxy.

        Raises:
            ConfigError: If *proxy_url* is not a valid URL string.
        """
        if proxy_url is None or str(proxy_url).strip() == "":
            self._session.proxies = {}
            self._proxy_url = None
            self._logger.debug("Proxy cleared.")
            return

        parsed = urlparse(str(proxy_url))
        if not parsed.scheme or not parsed.netloc:
            raise ConfigError(
                f"Invalid proxy URL '{proxy_url}'. Expected format: "
                "'http://host:port' or 'socks5://host:port'."
            )

        self._session.proxies = {
            "http": str(proxy_url),
            "https": str(proxy_url),
        }
        self._proxy_url = str(proxy_url)
        self._logger.info("Proxy configured: %s", proxy_url)

    @property
    def proxy_url(self) -> Optional[str]:
        """The currently configured proxy URL, or ``None``."""
        return self._proxy_url

    # ------------------------------------------------------------------
    # Cookie management
    # ------------------------------------------------------------------

    def _load_netscape_cookies(self, cookies_file: str) -> None:
        """Parse a Netscape-format cookies file and load into the session.

        The Netscape format is the de-facto standard used by ``curl`` and
        many browsers.  It has the form::

            # Netscape HTTP Cookie File
            .youtube.com	TRUE	/	FALSE	1700000000	NAME	VALUE

        Blank lines and lines starting with ``#`` are skipped.

        Args:
            cookies_file: Filesystem path to the Netscape cookies file.

        Raises:
            ConfigError: If the file cannot be read or is malformed.
        """
        path = os.path.expanduser(cookies_file)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        if not os.path.isfile(path):
            raise ConfigError(
                f"Cookies file not found: '{cookies_file}'."
            )

        self._logger.debug("Loading Netscape cookies from: %s", path)

        cookie_jar = http.cookiejar.MozillaCookieJar(path)
        try:
            cookie_jar.load(path, ignore_discard=True, ignore_expires=False)
        except http.cookiejar.LoadError as exc:
            raise ConfigError(
                f"Failed to load Netscape cookies from '{cookies_file}': {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Cannot read cookies file '{cookies_file}': {exc}"
            ) from exc

        self._session.cookies = cookie_jar
        self._cookies_loaded = True
        self._logger.info(
            "Loaded %d cookies from Netscape file: %s",
            len(cookie_jar),
            cookies_file,
        )

    def _load_lwp_cookies(self, cookies_file: str) -> None:
        """Parse an LWP-format cookies file and load into the session.

        Args:
            cookies_file: Filesystem path to the LWP cookies file.

        Raises:
            ConfigError: If the file cannot be read or is malformed.
        """
        path = os.path.expanduser(cookies_file)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        if not os.path.isfile(path):
            raise ConfigError(
                f"Cookies file not found: '{cookies_file}'."
            )

        self._logger.debug("Loading LWP cookies from: %s", path)

        cookie_jar = http.cookiejar.LWPCookieJar(path)
        try:
            cookie_jar.load(path, ignore_discard=True, ignore_expires=False)
        except http.cookiejar.LoadError as exc:
            raise ConfigError(
                f"Failed to load LWP cookies from '{cookies_file}': {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"Cannot read cookies file '{cookies_file}': {exc}"
            ) from exc

        self._session.cookies = cookie_jar
        self._cookies_loaded = True
        self._logger.info(
            "Loaded %d cookies from LWP file: %s",
            len(cookie_jar),
            cookies_file,
        )

    def _detect_cookie_format(self, cookies_file: str) -> str:
        """Detect the cookie file format by inspecting the file header.

        Checks the first non-blank, non-comment line for a format signature.

        Args:
            cookies_file: Path to the cookies file.

        Returns:
            One of ``"netscape"``, ``"lwp"``, or ``"unknown"``.
        """
        path = os.path.expanduser(cookies_file)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    stripped = raw_line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if stripped.startswith("# HTTP Cookie File") or stripped.startswith(
                        "# Netscape"
                    ):
                        return "netscape"
                    return "unknown"
        except OSError:
            pass
        return "unknown"

    def set_cookies(self, cookies_dict: dict[str, str]) -> None:
        """Set session cookies from a flat key-value dictionary.

        Overwrites any existing cookies in the session.  Cookies are stored
        as domain-less session cookies that are not persisted to disk.

        Args:
            cookies_dict: Mapping of cookie names to their values.

        Raises:
            ConfigError: If *cookies_dict* is not a mapping.
        """
        if not isinstance(cookies_dict, dict):
            raise ConfigError(
                "cookies_dict must be a dict[str, str]; "
                f"got {type(cookies_dict).__name__}."
            )

        cookie_jar = http.cookiejar.CookieJar()
        for name, value in cookies_dict.items():
            cookie = requests.cookies.create_cookie(
                name=name,
                value=str(value),
                domain=".youtube.com",
                path="/",
            )
            cookie_jar.set_cookie(cookie)

        self._session.cookies = cookie_jar
        self._cookies_loaded = True
        self._logger.debug(
            "Loaded %d cookies from dict for domain '.youtube.com'.",
            len(cookies_dict),
        )

    def load_cookies_from_file(self, cookies_file: str) -> None:
        """Load cookies from a file and inject them into the session.

        Automatically detects the file format (Netscape or LWP) and delegates
        to the appropriate parser.  If the format cannot be determined the
        Netscape parser is tried first, then LWP as a fallback.

        Args:
            cookies_file: Path to the cookies file.  Relative paths are
                resolved against the current working directory.  The
                ``~`` prefix is expanded automatically.

        Raises:
            ConfigError: If the file does not exist or cannot be parsed in
                any supported format.
        """
        file_format = self._detect_cookie_format(cookies_file)

        if file_format == "netscape":
            self._load_netscape_cookies(cookies_file)
            return
        if file_format == "lwp":
            self._load_lwp_cookies(cookies_file)
            return

        try:
            self._load_netscape_cookies(cookies_file)
        except ConfigError:
            self._load_lwp_cookies(cookies_file)

    def save_cookies_to_file(self, cookies_file: str) -> None:
        """Persist the current session cookies to a file.

        The output format is determined by the file extension: ``.txt``
        produces a Netscape-format file, all other extensions produce an
        LWP-format file.

        Args:
            cookies_file: Destination file path.  Parent directories are
                created automatically.

        Raises:
            ConfigError: If cookies have not been loaded into the session or
                the file cannot be written.
        """
        if not self._session.cookies or not self._cookies_loaded:
            raise ConfigError(
                "No cookies are currently loaded in the session. "
                "Call set_cookies() or load_cookies_from_file() first."
            )

        path = os.path.expanduser(cookies_file)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        cookie_jar = self._session.cookies
        if isinstance(cookie_jar, http.cookiejar.MozillaCookieJar):
            try:
                cookie_jar.save(path, ignore_discard=True, ignore_expires=True)
            except OSError as exc:
                raise ConfigError(
                    f"Cannot write cookies to '{cookies_file}': {exc}"
                ) from exc
        else:
            try:
                cookie_jar.save(path, ignore_discard=True, ignore_expires=True)
            except (OSError, AttributeError) as exc:
                raise ConfigError(
                    f"Cannot write cookies to '{cookies_file}': {exc}"
                ) from exc

        self._logger.info("Cookies saved to: %s", cookies_file)

    def get_cookies_dict(self) -> dict[str, str]:
        """Return all session cookies as a flat name-value mapping.

        Returns:
            A ``dict`` where keys are cookie names and values are the
            corresponding cookie values as strings.
        """
        return {
            cookie.name: cookie.value
            for cookie in self._session.cookies
        }

    def clear_cookies(self) -> None:
        """Remove all cookies from the session."""
        self._session.cookies.clear()
        self._cookies_loaded = False
        self._logger.debug("All session cookies cleared.")

    @property
    def cookies_loaded(self) -> bool:
        """``True`` if cookies have been loaded into the session."""
        return self._cookies_loaded

    # ------------------------------------------------------------------
    # Stream download
    # ------------------------------------------------------------------

    def head_file_size(self, url: str) -> Optional[int]:
        """Check the remote file size via an HTTP HEAD request.

        Args:
            url: The URL whose file size to check.

        Returns:
            The ``Content-Length`` value as an integer, or ``None`` if the
            header is absent or the request fails.

        Raises:
            NetworkError: If the HEAD request fails with a non-retryable
                error.
        """
        try:
            response = self.head(url)
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    return int(content_length)
                except ValueError:
                    self._logger.warning(
                        "Malformed Content-Length header: %r", content_length
                    )
            return None
        except NetworkError as exc:
            self._logger.warning("HEAD request for file size failed: %s", exc)
            return None
        except YTDLException as exc:
            self._logger.warning("HEAD request error for %s: %s", url, exc)
            return None

    def download_stream(
        self,
        url: str,
        output_path: str,
        expected_size: Optional[int] = None,
        progress_callback: Optional[ProgressCallback] = None,
        chunk_size: Optional[int] = None,
        resume: bool = False,
        headers: Optional[dict[str, str]] = None,
    ) -> int:
        """Download a remote file to *output_path* with progress reporting.

        Reads the remote resource in chunks, calls *progress_callback* after
        each chunk, and verifies the final file size against *expected_size*
        when it is provided.  Supports resuming partial downloads by sending
        a ``Range`` header when a partial file already exists on disk and
        *resume* is ``True``.

        Args:
            url: Source URL of the file to download.
            output_path: Destination filesystem path.  Parent directories
                are created automatically.
            expected_size: Expected total size in bytes, or ``None`` when
                unknown.  When provided the final file size is verified and
                a :class:`~ytdownloader.exceptions.DownloadError` is raised
                if it does not match.
            progress_callback: Called as
                ``progress_callback(downloaded, total, speed)`` after each
                chunk.  Pass ``None`` to disable progress reporting.
            chunk_size: Number of bytes per chunk read from the network.
                Pass ``None`` to use ``config.chunk_size``.
            resume: If ``True`` and a partial file exists at *output_path*,
                resume the download by sending a ``Range`` header.
            headers: Additional headers to include in the GET request.

        Returns:
            Total bytes written to *output_path*.

        Raises:
            DownloadError: If the download fails, the response body is
                empty, or the file size does not match *expected_size*.
            NetworkError: If the underlying HTTP request fails.
            OSError: If the output file cannot be written.
        """
        effective_chunk_size = chunk_size if chunk_size is not None else self._config.chunk_size

        output_abs = os.path.abspath(output_path)
        output_dir = os.path.dirname(output_abs)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        byte_offset = 0
        mode = "ab" if resume else "wb"
        partial_exists = resume and os.path.isfile(output_abs) and os.path.getsize(output_abs) > 0

        if partial_exists:
            byte_offset = os.path.getsize(output_abs)
            if expected_size is not None and byte_offset >= expected_size:
                self._logger.info(
                    "Partial file already complete (%d bytes). Skipping download.",
                    byte_offset,
                )
                if progress_callback is not None:
                    progress_callback(byte_offset, expected_size, 0.0)
                return byte_offset

        req_headers: dict[str, str] = {}
        if partial_exists and byte_offset > 0:
            req_headers["Range"] = f"bytes={byte_offset}-"

        if headers:
            req_headers.update(headers)

        debug_log_request(url=url, method=_HTTP_METHOD_GET, headers=req_headers)

        self._logger.info(
            "Downloading: %s -> %s%s",
            url,
            output_abs,
            f" (resuming from byte {byte_offset})" if byte_offset > 0 else "",
        )

        total_downloaded = byte_offset
        start_time = time.monotonic()
        last_chunk_time = start_time
        last_chunk_bytes = 0

        last_exception: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            try:
                request_kwargs: dict[str, Any] = {
                    "url": url,
                    "headers": req_headers,
                    "stream": True,
                    "timeout": self._config.timeout,
                }

                response = self._session.get(**request_kwargs)
                response.raise_for_status()

                debug_log_response(
                    status=response.status_code,
                    headers=dict(response.headers),
                )

                content_length_header = response.headers.get("Content-Length")
                chunk_expected_size: Optional[int] = None
                if content_length_header is not None:
                    try:
                        chunk_expected_size = int(content_length_header)
                        if partial_exists and response.status_code == 206:
                            chunk_expected_size += byte_offset
                    except ValueError:
                        chunk_expected_size = None

                effective_expected = expected_size if expected_size is not None else chunk_expected_size

                with open(output_abs, mode) as fh:
                    for chunk in response.iter_content(chunk_size=effective_chunk_size):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        total_downloaded += len(chunk)
                        last_chunk_bytes += len(chunk)

                        now = time.monotonic()
                        elapsed = now - last_chunk_time
                        if elapsed >= 0.5:
                            speed = last_chunk_bytes / elapsed if elapsed > 0 else 0.0
                            if progress_callback is not None:
                                progress_callback(
                                    total_downloaded,
                                    effective_expected,
                                    speed,
                                )
                            self._logger.debug(
                                "Download progress: %s / %s (%s/s)",
                                self._format_size(total_downloaded),
                                self._format_size(effective_expected)
                                if effective_expected is not None
                                else "?",
                                self._format_speed(speed),
                            )
                            last_chunk_time = now
                            last_chunk_bytes = 0

                break

            except requests.exceptions.RequestException as exc:
                last_exception = exc
                self._logger.warning(
                    "Download error (attempt %d/%d): %s",
                    attempt + 1,
                    self._config.max_retries + 1,
                    exc,
                )
                if attempt < self._config.max_retries:
                    time.sleep(self._get_backoff_delay(attempt))
                else:
                    raise DownloadError(
                        f"Download of '{url}' failed after "
                        f"{self._config.max_retries + 1} attempt(s).",
                        cause=exc,
                    ) from exc

            except OSError as exc:
                raise DownloadError(
                    f"Cannot write to '{output_abs}': {exc}"
                ) from exc

            finally:
                try:
                    response.close()
                except Exception:  # pragma: no cover
                    pass

        if expected_size is not None and total_downloaded != expected_size:
            raise DownloadError(
                f"Downloaded file size mismatch: expected {expected_size} bytes, "
                f"got {total_downloaded} bytes."
            )

        final_elapsed = time.monotonic() - start_time
        if final_elapsed > 0:
            avg_speed = total_downloaded / final_elapsed
            self._logger.info(
                "Download complete: %s (%s, %.2f s, %s/s avg).",
                output_abs,
                self._format_size(total_downloaded),
                final_elapsed,
                self._format_speed(avg_speed),
            )
        else:
            self._logger.info(
                "Download complete: %s (%s).",
                output_abs,
                self._format_size(total_downloaded),
            )

        if progress_callback is not None:
            progress_callback(total_downloaded, expected_size, 0.0)

        return total_downloaded

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------

    def debug_log_request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        """Log an outgoing HTTP request at DEBUG level.

        A thin wrapper around the package-level :func:`debug_log_request`
        that also logs via the instance logger for consistent output.

        Args:
            url: The request URL.
            method: HTTP method string.
            headers: Optional request headers to include in the log.
        """
        debug_log_request(url=url, method=method, headers=headers)
        self._logger.debug("OUTGOING %s %s", method, url)

    def debug_log_response(
        self,
        status: int,
        headers: Optional[dict[str, str]] = None,
        body_preview: str = "",
    ) -> None:
        """Log an HTTP response at DEBUG level.

        A thin wrapper around the package-level :func:`debug_log_response`.

        Args:
            status: Numeric HTTP status code.
            headers: Optional response headers to include in the log.
            body_preview: Short excerpt of the response body.
        """
        debug_log_response(status=status, headers=headers, body_preview=body_preview)
        self._logger.debug("INCOMING HTTP %d", status)

    # ------------------------------------------------------------------
    # Retry statistics
    # ------------------------------------------------------------------

    @property
    def retry_stats(self) -> dict[str, int]:
        """Return a snapshot of retry statistics.

        The dictionary contains the following keys:

        * ``"total_attempts"`` – total requests made across all calls.
        * ``"total_retries"`` – how many of those were retries.
        * ``"failed_requests"`` – requests that exhausted all retries.
        """
        return dict(self._retry_stats)

    def reset_stats(self) -> None:
        """Reset the internal retry statistics to zero."""
        self._retry_stats = {
            "total_attempts": 0,
            "total_retries": 0,
            "failed_requests": 0,
        }

    @property
    def last_request_meta(self) -> dict[str, Any]:
        """Metadata about the most recent request (method, URL, status, attempt)."""
        return dict(self._last_request_meta)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying session and release all resources.

        Should be called when the client is no longer needed to free
        connection pool resources.
        """
        try:
            self._session.close()
            self._logger.debug("HttpClient session closed.")
        except Exception as exc:
            self._logger.warning("Error closing HttpClient session: %s", exc)

    def __enter__(self) -> HttpClient:
        """Support usage as a context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close the session when leaving the context manager."""
        self.close()

    def __repr__(self) -> str:
        """Return a developer-friendly representation of the client."""
        return (
            f"HttpClient("
            f"proxy={self._proxy_url!r}, "
            f"max_retries={self._config.max_retries}, "
            f"cookies_loaded={self._cookies_loaded})"
        )

    # ------------------------------------------------------------------
    # Static formatting helpers (used internally and by download_stream)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_size(num_bytes: Optional[int]) -> str:
        """Format a byte count as a human-readable string.

        Args:
            num_bytes: Raw byte count.  If ``None``, returns ``"?"``.

        Returns:
            A string such as ``"1.5 MB"`` or ``"320 KB"``.
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

    @staticmethod
    def _format_speed(bytes_per_sec: float) -> str:
        """Format a transfer speed in bytes/sec as a human-readable string.

        Args:
            bytes_per_sec: Transfer speed in bytes per second.

        Returns:
            A string such as ``"1.2 MB/s"`` or ``"450 KB/s"``.
        """
        if bytes_per_sec < 0:
            return "0 B/s"
        if bytes_per_sec >= 1 << 30:
            return f"{bytes_per_sec / (1 << 30):.2f} GB/s"
        if bytes_per_sec >= 1 << 20:
            return f"{bytes_per_sec / (1 << 20):.2f} MB/s"
        if bytes_per_sec >= 1 << 10:
            return f"{bytes_per_sec / (1 << 10):.2f} KB/s"
        return f"{bytes_per_sec:.0f} B/s"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def build_client(
    config: Optional[YTConfig] = None,
    cookies_file: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> HttpClient:
    """Build an :class:`HttpClient` with optional cookie and proxy overrides.

    Convenience factory that loads configuration, optionally overlays a
    proxy and cookies file, and returns the ready-to-use client.

    Args:
        config: A :class:`~ytdownloader.config.YTConfig` instance.  Pass
            ``None`` to load the default configuration from disk.
        cookies_file: Optional path to a cookies file.  Pass ``None`` to
            skip cookie loading.
        proxy_url: Optional proxy URL.  Pass ``None`` to skip proxy setup.

    Returns:
        A fully configured :class:`HttpClient`.
    """
    if config is None:
        from .config import load_config

        config = load_config()

    client = HttpClient(config)

    if cookies_file:
        client.load_cookies_from_file(cookies_file)

    if proxy_url:
        client.set_proxy(proxy_url)

    return client


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "HttpClient",
    "ProgressCallback",
    "build_client",
    "_RETRYABLE_STATUS_CODES",
]
