"""
Core download manager for the ytdownloader package.

Builds the streaming download pipeline from scratch using only
:mod:`requests`.  Provides chunked reading, resume support, progress
reporting, thread-safe file writing, automatic retry, and post-download
verification.

Typical usage::

    from ytdownloader.config import YTConfig, load_config
    from ytdownloader.http_client import HttpClient
    from ytdownloader.download_manager import DownloadManager

    config = load_config()
    client = HttpClient(config)
    manager = DownloadManager(config, client)
    manager.download_stream(stream_url, "output.mp4", expected_size=10_000_000)
    manager.download_audio(audio_url, "output.mp3")
    manager.download_video(video_url, "output.mp4")
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Optional, Union

import requests

from .config import YTConfig
from .exceptions import DownloadError, YTDLException
from .http_client import HttpClient, ProgressCallback
from .logger import get_logger

__all__ = [
    "DownloadManager",
    "DownloadProgress",
    "DownloadError",
]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_CHUNK_SIZE: int = 1024 * 1024  # 1 MB
_MIN_PROGRESS_INTERVAL: float = 0.25  # seconds between progress callbacks
_SPEED_WINDOW_SECONDS: float = 5.0  # rolling window for speed smoothing
_MAX_SPEED_SAMPLES: int = 20
_MIN_VALID_CHUNK_SIZE: int = 1
_MAX_VALID_CHUNK_SIZE: int = 64 * 1024 * 1024  # 64 MB
_MIN_READ_TIMEOUT_FACTOR: float = 0.5
_CONNECT_TIMEOUT: float = 15.0
_READ_TIMEOUT: float = 30.0
_RETRYABLE_HTTP_CODES: frozenset[int] = frozenset(
    {
        requests.codes.request_timeout,   # 408
        requests.codes.too_many_requests, # 429
        requests.codes.internal_server_error,  # 500
        requests.codes.bad_gateway,       # 502
        requests.codes.service_unavailable,   # 503
        requests.codes.gateway_timeout,   # 504
    }
)
_CONTENT_TYPE_VIDEO: frozenset[str] = frozenset(
    {"video/mp4", "video/webm", "video/x-flv", "video/3gpp", "video/quicktime"}
)
_CONTENT_TYPE_AUDIO: frozenset[str] = frozenset(
    {"audio/mpeg", "audio/mp3", "audio/mp4", "audio/wav",
     "audio/flac", "audio/ogg", "audio/webm", "audio/aac"}
)

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# DownloadProgress dataclass
# ---------------------------------------------------------------------------

@dataclass
class DownloadProgress:
    """Track the state of an in-progress or completed download.

    Attributes:
        url: Source URL of the download.
        output_path: Destination file path.
        expected_size: Expected total size in bytes, or ``None`` if unknown.
        downloaded: Cumulative bytes written so far.
        speed: Most recent speed estimate in bytes per second.
        start_time: Monotonic timestamp when the download began.
        end_time: Monotonic timestamp when the download finished, or ``None``.
        is_complete: Whether the download has finished.
        is_resumed: Whether the download was resumed from a partial file.
        resume_offset: Byte offset at which a resumed download started.
        total_bytes_written: Total bytes written including any pre-existing
            partial data.
        error: Exception that caused the download to fail, or ``None``.
        content_type: Content-Type header value from the response, or ``None``.
        http_status: HTTP status code of the final response, or ``None``.
        attempts: Number of request attempts made.
    """

    url: str = ""
    output_path: str = ""
    expected_size: Optional[int] = None
    downloaded: int = 0
    speed: float = 0.0
    start_time: float = field(default_factory=time.monotonic)
    end_time: Optional[float] = None
    is_complete: bool = False
    is_resumed: bool = False
    resume_offset: int = 0
    total_bytes_written: int = 0
    error: Optional[Exception] = None
    content_type: Optional[str] = None
    http_status: Optional[int] = None
    attempts: int = 0

    @property
    def elapsed(self) -> float:
        """Return elapsed seconds since :attr:`start_time`."""
        end = self.end_time if self.end_time is not None else time.monotonic()
        return max(0.0, end - self.start_time)

    @property
    def percentage(self) -> Optional[float]:
        """Return completion percentage, or ``None`` if size is unknown."""
        if self.expected_size is None or self.expected_size <= 0:
            return None
        return min(100.0, max(0.0, self.downloaded / self.expected_size * 100.0))

    @property
    def eta_seconds(self) -> Optional[float]:
        """Return estimated seconds remaining, or ``None`` if unavailable."""
        if self.speed <= 0 or self.expected_size is None:
            return None
        remaining = self.expected_size - self.downloaded
        if remaining <= 0:
            return 0.0
        return remaining / self.speed

    @property
    def average_speed(self) -> float:
        """Return average bytes per second since :attr:`start_time`."""
        elapsed = self.elapsed
        if elapsed <= 0:
            return 0.0
        return self.downloaded / elapsed

    def mark_complete(self) -> None:
        """Mark the download as finished and record the end time."""
        self.is_complete = True
        self.end_time = time.monotonic()

    def mark_failed(self, error: Exception) -> None:
        """Record the error that caused the failure.

        Args:
            error: The exception that was raised.
        """
        self.error = error
        self.end_time = time.monotonic()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the progress state to a plain dictionary.

        Returns:
            A dictionary representation of the progress state.
        """
        return {
            "url": self.url,
            "output_path": self.output_path,
            "expected_size": self.expected_size,
            "downloaded": self.downloaded,
            "speed": self.speed,
            "elapsed": self.elapsed,
            "is_complete": self.is_complete,
            "is_resumed": self.is_resumed,
            "resume_offset": self.resume_offset,
            "total_bytes_written": self.total_bytes_written,
            "percentage": self.percentage,
            "eta_seconds": self.eta_seconds,
            "average_speed": self.average_speed,
            "content_type": self.content_type,
            "http_status": self.http_status,
            "attempts": self.attempts,
            "has_error": self.error is not None,
        }

    def __repr__(self) -> str:
        status = "complete" if self.is_complete else ("failed" if self.error else "in_progress")
        pct = f"{self.percentage:.1f}%" if self.percentage is not None else "?"
        return (
            f"DownloadProgress("
            f"status={status!r}, "
            f"downloaded={self.downloaded}, "
            f"expected={self.expected_size}, "
            f"progress={pct}, "
            f"speed={self.speed:.1f} B/s)"
        )


# ---------------------------------------------------------------------------
# _SpeedSmoother
# ---------------------------------------------------------------------------

class _SpeedSmoother:
    """Rolling-window speed estimator with thread safety.

    Maintains a time-ordered list of ``(timestamp, byte_delta)`` samples
    and computes a smoothed speed estimate over the most recent window.

    Attributes:
        _samples: Ordered list of ``(timestamp, bytes)`` tuples.
        _window_seconds: Maximum age of samples to retain.
        _lock: Threading lock protecting mutable state.
    """

    def __init__(self, window_seconds: float = _SPEED_WINDOW_SECONDS) -> None:
        """Initialise the speed smoother.

        Args:
            window_seconds: Maximum age of samples in seconds.
        """
        self._samples: list[tuple[float, float]] = []
        self._window_seconds = window_seconds
        self._lock = threading.Lock()

    def record(self, bytes_delta: float, timestamp: Optional[float] = None) -> None:
        """Record a new speed sample.

        Args:
            bytes_delta: Number of bytes transferred since the last sample.
            timestamp: Monotonic timestamp of the sample.  Defaults to
                :func:`time.monotonic` if not provided.
        """
        if bytes_delta <= 0:
            return
        now = timestamp if timestamp is not None else time.monotonic()
        with self._lock:
            self._samples.append((now, float(bytes_delta)))
            self._prune(now)

    def _prune(self, now: float) -> None:
        """Remove samples older than :attr:`_window_seconds`.

        Args:
            now: Current monotonic timestamp.
        """
        cutoff = now - self._window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def get_speed(self) -> float:
        """Compute the smoothed speed in bytes per second.

        Returns:
            Estimated bytes per second, or ``0.0`` if there are too few
            samples.
        """
        with self._lock:
            if len(self._samples) < 2:
                return 0.0
            oldest_time = self._samples[0][0]
            newest_time = self._samples[-1][0]
            elapsed = newest_time - oldest_time
            if elapsed <= 0:
                return 0.0
            total_bytes = sum(s[1] for s in self._samples)
            return total_bytes / elapsed

    def reset(self) -> None:
        """Clear all recorded samples."""
        with self._lock:
            self._samples.clear()


# ---------------------------------------------------------------------------
# DownloadManager
# ---------------------------------------------------------------------------

class DownloadManager:
    """Orchestrate chunked streaming downloads with resume and progress.

    Wraps an :class:`~ytdownloader.http_client.HttpClient` and provides
    high-level download methods that handle chunked streaming, partial-file
    resume, progress reporting, retry, and post-download verification.

    Attributes:
        config: The :class:`~ytdownloader.config.YTConfig` driving this manager.
        http_client: The :class:`~ytdownloader.http_client.HttpClient` used
            for all network requests.
        progress_callback: Optional callable receiving
            ``(downloaded, total, speed)`` updates.
        _progress: The current :class:`DownloadProgress` state object.
        _speed_smoother: Rolling-window speed estimator.
        _file_lock: Threading lock protecting file writes.
    """

    def __init__(
        self,
        config: YTConfig,
        http_client: HttpClient,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        """Initialise the download manager.

        Args:
            config: A fully-populated :class:`~ytdownloader.config.YTConfig`
                instance.
            http_client: An initialised :class:`~ytdownloader.http_client.HttpClient`
                for all network requests.
            progress_callback: Called as
                ``progress_callback(downloaded, total, speed)`` after each
                chunk.  Pass ``None`` to disable progress reporting.
        """
        self._config = config
        self._http_client = http_client
        self._progress_callback = progress_callback
        self._progress = DownloadProgress()
        self._speed_smoother = _SpeedSmoother()
        self._file_lock = threading.Lock()
        self._download_lock = threading.Lock()
        self._active_downloads: dict[str, DownloadProgress] = {}

        self._logger = _logger

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> YTConfig:
        """The configuration driving this manager."""
        return self._config

    @property
    def http_client(self) -> HttpClient:
        """The underlying HTTP client."""
        return self._http_client

    @property
    def progress(self) -> DownloadProgress:
        """The current download progress state."""
        return self._progress

    @property
    def active_downloads(self) -> dict[str, DownloadProgress]:
        """Mapping of output paths to their progress states."""
        return dict(self._active_downloads)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_logger(self) -> logging.Logger:
        """Return the module logger."""
        return self._logger

    def _build_progress(
        self,
        url: str,
        output_path: str,
        expected_size: Optional[int],
        is_resumed: bool = False,
        resume_offset: int = 0,
    ) -> DownloadProgress:
        """Create and register a :class:`DownloadProgress` state object.

        Args:
            url: Source URL.
            output_path: Destination file path.
            expected_size: Expected total size in bytes.
            is_resumed: Whether the download is being resumed.
            resume_offset: Byte offset for resumed downloads.

        Returns:
            A new :class:`DownloadProgress` instance registered in the
            active downloads map.
        """
        progress = DownloadProgress(
            url=url,
            output_path=output_path,
            expected_size=expected_size,
            is_resumed=is_resumed,
            resume_offset=resume_offset,
            total_bytes_written=resume_offset,
        )
        with self._download_lock:
            self._active_downloads[output_path] = progress
        return progress

    def _remove_progress(self, output_path: str) -> None:
        """Remove a download from the active downloads map.

        Args:
            output_path: Destination file path of the completed download.
        """
        with self._download_lock:
            self._active_downloads.pop(output_path, None)

    def _report_progress(
        self,
        progress: DownloadProgress,
        downloaded: int,
        total: Optional[int],
        speed: float,
    ) -> None:
        """Update progress state and invoke the callback.

        Args:
            progress: The :class:`DownloadProgress` state to update.
            downloaded: Total bytes downloaded so far.
            total: Expected total size, or ``None``.
            speed: Current speed in bytes per second.
        """
        progress.downloaded = downloaded
        progress.speed = speed
        self._speed_smoother.record(speed * _MIN_PROGRESS_INTERVAL)
        if self._progress_callback is not None:
            try:
                self._progress_callback(downloaded, total, speed)
            except Exception:
                pass

    def _get_file_size(self, path: str) -> int:
        """Return the current size of a file on disk.

        Args:
            path: Filesystem path to the file.

        Returns:
            Size in bytes, or ``0`` if the file does not exist or cannot
            be stat'd.
        """
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _check_resume_eligibility(
        self,
        output_path: str,
        expected_size: Optional[int],
        resume: bool,
    ) -> tuple[bool, int]:
        """Determine whether a download can be resumed and at what offset.

        Args:
            output_path: Destination file path.
            expected_size: Expected total size in bytes, or ``None``.
            resume: Whether the caller requested resume support.

        Returns:
            A ``(can_resume, byte_offset)`` tuple.  ``can_resume`` is
            ``True`` when the file exists, is non-empty, and is smaller
            than the expected total size.
        """
        if not resume:
            return False, 0
        existing_size = self._get_file_size(output_path)
        if existing_size <= 0:
            return False, 0
        if expected_size is not None and existing_size >= expected_size:
            return False, existing_size
        return True, existing_size

    def _open_file(self, path: str, mode: str) -> IO[bytes]:
        """Open a file for writing with appropriate safety checks.

        Parent directories are created automatically.  The file is opened
        in unbuffered binary mode when the mode implies append, otherwise
        buffered binary write mode is used.

        Args:
            path: Filesystem path to the output file.
            mode: File open mode string (e.g. ``"wb"``, ``"ab"``).

        Returns:
            An open file handle ready for writing.

        Raises:
            DownloadError: If the file cannot be opened.
        """
        abs_path = os.path.abspath(path)
        parent_dir = os.path.dirname(abs_path)
        if parent_dir:
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except OSError as exc:
                raise DownloadError(
                    f"Cannot create directory '{parent_dir}': {exc}"
                ) from exc
        try:
            if mode == "ab":
                fd = os.open(abs_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                file_handle = os.fdopen(fd, "wb", buffering=0)
            else:
                file_handle = open(abs_path, mode, buffering=-1)
            return file_handle
        except OSError as exc:
            raise DownloadError(
                f"Cannot open output file '{abs_path}' for writing: {exc}"
            ) from exc

    def _close_file(self, file_handle: IO[bytes]) -> None:
        """Flush and close a file handle, swallowing non-critical errors.

        Args:
            file_handle: Open file handle to close.
        """
        try:
            file_handle.flush()
        except (OSError, ValueError):
            pass
        try:
            file_handle.close()
        except (OSError, ValueError):
            pass

    def _acquire_file_lock(self, output_path: str) -> Optional[threading.Lock]:
        """Return the per-file write lock for *output_path*.

        All downloads of the same file share a single lock so that
        concurrent writes are serialised.

        Args:
            output_path: Destination file path.

        Returns:
            The :class:`threading.Lock` associated with this output path.
        """
        if not hasattr(self, "_per_file_locks"):
            object.__setattr__(self, "_per_file_locks", {})
        locks = self._per_file_locks
        if output_path not in locks:
            with self._file_lock:
                if output_path not in locks:
                    locks[output_path] = threading.Lock()
        return locks[output_path]

    def _write_chunk(self, file_handle: IO[bytes], chunk_data: bytes) -> int:
        """Write a single chunk of data to the file handle.

        Validates the chunk before writing and performs the write under
        the global file lock.

        Args:
            file_handle: Open binary file handle to write to.
            chunk_data: Raw bytes to write.

        Returns:
            Number of bytes successfully written.

        Raises:
            DownloadError: If *chunk_data* is empty, is not a
                :class:`bytes` instance, or the write fails.
        """
        if not isinstance(chunk_data, (bytes, bytearray)):
            raise DownloadError(
                f"Chunk data must be bytes; got {type(chunk_data).__name__}."
            )
        chunk_len = len(chunk_data)
        if chunk_len == 0:
            return 0
        if chunk_len < _MIN_VALID_CHUNK_SIZE:
            self._logger.debug("Writing small chunk of %d bytes.", chunk_len)
        file_lock = self._acquire_file_lock(
            getattr(file_handle, "name", "__unknown__")
        )
        with file_lock:
            try:
                if hasattr(file_handle, "mode") and "a" in getattr(file_handle, "mode", ""):
                    raw_fd = file_handle.fileno()
                    bytes_written = os.write(raw_fd, chunk_data)
                    if bytes_written != chunk_len:
                        remaining = chunk_data[bytes_written:]
                        total = bytes_written
                        while remaining:
                            n = os.write(raw_fd, remaining)
                            if n <= 0:
                                raise DownloadError(
                                    "Incomplete write to file; disk may be full."
                                )
                            total += n
                            remaining = remaining[n:]
                        bytes_written = total
                else:
                    file_handle.write(chunk_data)
                    bytes_written = chunk_len
            except OSError as exc:
                raise DownloadError(
                    f"Failed to write chunk of {chunk_len} bytes: {exc}"
                ) from exc
        return bytes_written

    def _verify_download(self, path: str, expected_size: Optional[int]) -> int:
        """Verify the downloaded file size matches expectations.

        Args:
            path: Filesystem path to the completed download.
            expected_size: Expected size in bytes, or ``None`` to skip
                verification.

        Returns:
            Actual file size in bytes.

        Raises:
            DownloadError: If *expected_size* is provided and the file
                size does not match.
            OSError: If the file cannot be stat'd.
        """
        actual_size = self._get_file_size(path)
        if expected_size is not None and actual_size != expected_size:
            raise DownloadError(
                f"File size mismatch: expected {expected_size} bytes, "
                f"got {actual_size} bytes at '{path}'."
            )
        return actual_size

    def _calculate_speed(
        self,
        bytes_downloaded: int,
        elapsed_seconds: float,
    ) -> float:
        """Compute transfer speed from bytes and elapsed time.

        Args:
            bytes_downloaded: Number of bytes transferred.
            elapsed_seconds: Elapsed time in seconds.

        Returns:
            Bytes per second, or ``0.0`` if *elapsed_seconds* is zero
            or *bytes_downloaded* is negative.
        """
        if elapsed_seconds <= 0 or bytes_downloaded < 0:
            return 0.0
        if bytes_downloaded == 0:
            return 0.0
        return bytes_downloaded / elapsed_seconds

    def _build_request_headers(
        self,
        resume_offset: int,
        extra_headers: Optional[dict[str, str]],
    ) -> dict[str, str]:
        """Build request headers including optional Range header for resume.

        Args:
            resume_offset: Byte offset to resume from.  When ``> 0`` a
                ``Range`` header is included.
            extra_headers: Additional headers merged into the result.

        Returns:
            Dictionary of HTTP headers.
        """
        headers: dict[str, str] = {}
        if resume_offset > 0:
            headers["Range"] = f"bytes={resume_offset}-"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _validate_url(self, url: str) -> None:
        """Validate that *url* is a non-empty string.

        Args:
            url: URL to validate.

        Raises:
            DownloadError: If *url* is empty or not a string.
        """
        if not url or not isinstance(url, str) or not url.strip():
            raise DownloadError(
                "A valid non-empty URL string is required for download."
            )
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise DownloadError(
                f"URL must start with 'http://' or 'https://': {url!r}"
            )

    def _get_content_length(
        self,
        response: requests.Response,
    ) -> Optional[int]:
        """Parse the Content-Length header from an HTTP response.

        Args:
            response: The HTTP response to inspect.

        Returns:
            Content length as an integer, or ``None`` if absent or invalid.
        """
        raw = response.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw.strip())
            return value if value >= 0 else None
        except (ValueError, TypeError):
            self._logger.warning("Malformed Content-Length header: %r", raw)
            return None

    def _is_206_response(self, response: requests.Response) -> bool:
        """Return ``True`` if the response is a 206 Partial Content.

        Args:
            response: The HTTP response to inspect.

        Returns:
            ``True`` when status code is 206.
        """
        return response.status_code == 206

    def _handle_connection_error(
        self,
        exc: Exception,
        attempt: int,
        max_attempts: int,
        url: str,
    ) -> float:
        """Log a connection error and compute the backoff delay.

        Args:
            exc: The exception that was raised.
            attempt: Current zero-based attempt index.
            max_attempts: Maximum number of attempts.
            url: URL that failed (for logging).

        Returns:
            Seconds to sleep before the next attempt.
        """
        self._logger.warning(
            "Connection error for %s (attempt %d/%d): %s",
            url,
            attempt + 1,
            max_attempts,
            exc,
        )
        return self._get_backoff_delay(attempt)

    def _get_backoff_delay(self, attempt: int) -> float:
        """Compute the backoff delay for a given retry attempt.

        Uses exponential backoff: ``retry_delay_base * (2 ** attempt)``.

        Args:
            attempt: Zero-based retry attempt index.

        Returns:
            Number of seconds to wait.
        """
        base = self._config.retry_delay_base
        return base * (2 ** attempt)

    def _sleep_backoff(self, attempt: int) -> None:
        """Sleep for the backoff duration for the given attempt.

        Args:
            attempt: Zero-based retry attempt index.
        """
        delay = self._get_backoff_delay(attempt)
        if delay > 0:
            time.sleep(delay)

    def _process_stream_chunks(
        self,
        response: requests.Response,
        file_handle: IO[bytes],
        progress: DownloadProgress,
        chunk_size: int,
        expected_total: Optional[int],
        initial_bytes: int,
        mode: str,
    ) -> int:
        """Iterate over the response stream and write chunks to the file.

        Tracks progress, computes speed, invokes the progress callback,
        and returns the total bytes written during this attempt.

        Args:
            response: Open :class:`requests.Response` with streaming body.
            file_handle: Open file handle to write chunks to.
            progress: :class:`DownloadProgress` state to update.
            chunk_size: Number of bytes to read per iteration.
            expected_total: Expected total file size, or ``None``.
            initial_bytes: Bytes already present before this attempt
                (for resumed downloads).
            mode: File open mode (``"wb"`` or ``"ab"``).

        Returns:
            Total bytes written during this streaming attempt.
        """
        total_downloaded = initial_bytes
        bytes_since_last_callback = 0
        last_callback_time = time.monotonic()
        chunk_count = 0

        for raw_chunk in response.iter_content(chunk_size=chunk_size):
            if raw_chunk is None:
                continue
            if not isinstance(raw_chunk, (bytes, bytearray)):
                continue
            chunk_len = len(raw_chunk)
            if chunk_len == 0:
                continue

            written = self._write_chunk(file_handle, raw_chunk)
            if written != chunk_len:
                raise DownloadError(
                    f"Short write: expected {chunk_len} bytes, wrote {written}."
                )

            total_downloaded += written
            bytes_since_last_callback += written
            chunk_count += 1

            now = time.monotonic()
            elapsed_since_callback = now - last_callback_time

            if elapsed_since_callback >= _MIN_PROGRESS_INTERVAL:
                instant_speed = self._calculate_speed(
                    bytes_since_last_callback, elapsed_since_callback
                )
                self._speed_smoother.record(
                    bytes_since_last_callback, timestamp=now
                )
                smoothed_speed = self._speed_smoother.get_speed()
                if smoothed_speed <= 0:
                    smoothed_speed = instant_speed

                progress.downloaded = total_downloaded
                progress.speed = smoothed_speed
                progress.total_bytes_written = total_downloaded

                effective_total = expected_total
                if mode == "ab" and expected_total is not None:
                    pass
                self._report_progress(
                    progress, total_downloaded, effective_total, smoothed_speed
                )

                if self._logger.isEnabledFor(logging.DEBUG):
                    self._logger.debug(
                        "Chunk %d: wrote %d bytes, total=%d, speed=%.1f B/s.",
                        chunk_count,
                        written,
                        total_downloaded,
                        smoothed_speed,
                    )

                bytes_since_last_callback = 0
                last_callback_time = now

        final_speed = self._speed_smoother.get_speed()
        if final_speed <= 0 and total_downloaded > initial_bytes:
            total_elapsed = time.monotonic() - progress.start_time
            final_speed = self._calculate_speed(
                total_downloaded - initial_bytes, total_elapsed
            )

        progress.downloaded = total_downloaded
        progress.speed = final_speed
        progress.total_bytes_written = total_downloaded
        self._report_progress(
            progress, total_downloaded, expected_total, final_speed
        )

        return total_downloaded

    def _handle_response_status(
        self,
        response: requests.Response,
        url: str,
        attempt: int,
        max_attempts: int,
        progress: DownloadProgress,
    ) -> bool:
        """Validate response status and decide whether to retry.

        Args:
            response: The HTTP response received.
            url: Source URL (for logging).
            attempt: Zero-based attempt index.
            max_attempts: Maximum attempts allowed.
            progress: Progress state to update with HTTP status.

        Returns:
            ``True`` if the response is acceptable (2xx or 206),
            ``False`` if a retry should be attempted.

        Raises:
            DownloadError: If the response is non-retryable.
        """
        progress.http_status = response.status_code
        progress.content_type = response.headers.get("Content-Type")

        if response.ok:
            return True

        status = response.status_code

        if self._is_retryable_status(status) and attempt < max_attempts - 1:
            self._logger.warning(
                "HTTP %d for %s (attempt %d/%d). Will retry.",
                status,
                url,
                attempt + 1,
                max_attempts,
            )
            return False

        body_preview = ""
        try:
            ct = response.headers.get("Content-Type", "")
            if "text" in ct or "json" in ct or not ct:
                body_preview = response.text[:300]
        except Exception:
            body_preview = "(unable to read body)"

        self._logger.error(
            "HTTP %d for %s (attempt %d/%d). Body: %s",
            status,
            url,
            attempt + 1,
            max_attempts,
            body_preview,
        )

        if status == requests.codes.request_timeout:
            raise DownloadError(
                f"HTTP {status}: request timed out for {url}.",
            )
        if status == requests.codes.too_many_requests:
            raise DownloadError(
                f"HTTP {status}: rate limited for {url}. "
                "Increase retry_delay_base or use a proxy."
            )
        if status == requests.codes.not_found:
            raise DownloadError(
                f"HTTP {status}: resource not found at {url}."
            )
        if status == requests.codes.forbidden:
            raise DownloadError(
                f"HTTP {status}: access forbidden for {url}."
            )
        if 400 <= status < 500:
            raise DownloadError(
                f"HTTP {status}: client error for {url}. "
                f"Body preview: {body_preview}"
            )
        raise DownloadError(
            f"HTTP {status}: server error for {url} after {attempt + 1} attempt(s)."
        )

    def _is_retryable_status(self, status_code: int) -> bool:
        """Return ``True`` if *status_code* is a retryable HTTP error.

        Args:
            status_code: HTTP status code to check.

        Returns:
            ``True`` if the status code should trigger a retry.
        """
        return status_code in _RETRYABLE_HTTP_CODES

    def _detect_stream_type(
        self,
        content_type: Optional[str],
        url: str,
    ) -> str:
        """Classify the stream type from the Content-Type header or URL.

        Args:
            content_type: HTTP Content-Type header, or ``None``.
            url: Source URL (used as fallback for classification).

        Returns:
            ``"video"`` or ``"audio"``.
        """
        if content_type:
            ct_lower = content_type.lower()
            for video_type in _CONTENT_TYPE_VIDEO:
                if video_type in ct_lower:
                    return "video"
            for audio_type in _CONTENT_TYPE_AUDIO:
                if audio_type in ct_lower:
                    return "audio"
        url_lower = url.lower()
        audio_extensions = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".weba"}
        for ext in audio_extensions:
            if url_lower.endswith(ext):
                return "audio"
        return "video"

    def _check_disk_space(self, path: str, required_bytes: int) -> bool:
        """Verify sufficient disk space is available for the download.

        Args:
            path: Destination file path.
            required_bytes: Minimum free space required in bytes.

        Returns:
            ``True`` if sufficient space is available.
        """
        try:
            stat = os.statvfs(os.path.dirname(os.path.abspath(path)) or ".")
            free = stat.f_bavail * stat.f_frsize
            return free >= required_bytes
        except (AttributeError, OSError):
            return True

    def _cleanup_on_failure(
        self,
        output_path: str,
        progress: DownloadProgress,
        original_size: int,
    ) -> None:
        """Remove a partial download if it was created during this attempt.

        Args:
            output_path: Destination file path.
            progress: Progress state object.
            original_size: Size of the file before this download attempt.
        """
        if progress.is_resumed and original_size > 0:
            return
        try:
            if os.path.isfile(output_path):
                os.remove(output_path)
                self._logger.debug("Removed partial file: %s", output_path)
        except OSError:
            pass

    def _log_download_stats(self, progress: DownloadProgress) -> None:
        """Log final download statistics.

        Args:
            progress: Completed progress state.
        """
        elapsed = progress.elapsed
        avg_speed = progress.average_speed
        self._logger.info(
            "Download complete: %s -> %s (%s, %.2f s, %s/s avg, %d attempt(s)).",
            progress.url,
            progress.output_path,
            self._format_size(progress.downloaded),
            elapsed,
            self._format_size(int(avg_speed)),
            progress.attempts,
        )

    def _format_size(self, num_bytes: int) -> str:
        """Format a byte count as a human-readable string.

        Args:
            num_bytes: Raw byte count.

        Returns:
            Formatted string such as ``"1.5 MB"``.
        """
        abs_bytes = abs(num_bytes)
        if abs_bytes >= 1 << 30:
            return f"{num_bytes / (1 << 30):.2f} GB"
        if abs_bytes >= 1 << 20:
            return f"{num_bytes / (1 << 20):.2f} MB"
        if abs_bytes >= 1 << 10:
            return f"{num_bytes / (1 << 10):.2f} KB"
        return f"{num_bytes} B"

    # ------------------------------------------------------------------
    # Core download_stream method
    # ------------------------------------------------------------------

    def download_stream(
        self,
        url: str,
        output_path: str,
        expected_size: Optional[int] = None,
        resume: bool = False,
        chunk_size: Optional[int] = None,
        progress_callback: Optional[ProgressCallback] = None,
        extra_headers: Optional[dict[str, str]] = None,
        verify_size: bool = True,
        cleanup_on_failure: bool = False,
        stream_type: Optional[str] = None,
        min_content_length: Optional[int] = None,
    ) -> int:
        """Download a single stream to *output_path* with full resume support.

        This is the primary low-level download entry point.  It performs
        the following steps for each retry attempt:

        1. Validates the URL.
        2. Checks whether a partial file can be resumed.
        3. Builds request headers including ``Range`` for resume.
        4. Issues a streaming GET request via the HTTP client.
        5. Validates the response status.
        6. Reads the response body in chunks and writes them to disk.
        7. Fires progress callbacks as each chunk is processed.
        8. Verifies the final file size when *expected_size* is given.
        9. Logs comprehensive download statistics.

        Args:
            url: Source URL of the stream to download.
            output_path: Destination filesystem path.  Parent directories
                are created automatically.
            expected_size: Expected total size in bytes, or ``None`` when
                the size is unknown.  When provided the final file size is
                verified against this value and a :class:`DownloadError` is
                raised on mismatch.
            resume: If ``True`` and a partial file exists at *output_path*,
                attempt to resume the download by sending a ``Range``
                header.  The partial file is appended to.
            chunk_size: Number of bytes to read per network read.  Pass
                ``None`` to use ``config.chunk_size``.
            progress_callback: Override the instance-level
                ``progress_callback`` for this call.  Called as
                ``progress_callback(downloaded, total, speed)``.
            extra_headers: Additional HTTP headers merged into the request.
            verify_size: If ``True`` (default), verify the file size against
                *expected_size* after the download completes.
            cleanup_on_failure: If ``True``, remove partial files on failure.
                Has no effect when resuming a partial download.
            stream_type: Override stream type classification (``"video"``
                or ``"audio"``).  ``None`` auto-detects from Content-Type.
            min_content_length: Minimum Content-Length to accept without
                raising an error.  ``None`` disables this check.

        Returns:
            Total bytes written to *output_path*.

        Raises:
            DownloadError: If the download fails, the response body is
                empty, or the file size does not match *expected_size*.
            NetworkError: If the underlying HTTP request fails.
            OSError: If the output file cannot be written.
        """
        effective_chunk_size = chunk_size if chunk_size is not None else self._config.chunk_size
        if not (_MIN_VALID_CHUNK_SIZE <= effective_chunk_size <= _MAX_VALID_CHUNK_SIZE):
            raise DownloadError(
                f"chunk_size must be between {_MIN_VALID_CHUNK_SIZE} and "
                f"{_MAX_VALID_CHUNK_SIZE}; got {effective_chunk_size}."
            )

        self._validate_url(url)

        abs_path = os.path.abspath(output_path)

        progress = self._build_progress(
            url=url,
            output_path=abs_path,
            expected_size=expected_size,
        )

        can_resume, resume_offset = self._check_resume_eligibility(
            abs_path, expected_size, resume
        )

        if can_resume:
            progress.is_resumed = True
            progress.resume_offset = resume_offset
            self._logger.info(
                "Resuming download of %s from byte %d.",
                abs_path,
                resume_offset,
            )
        else:
            if resume and self._get_file_size(abs_path) > 0:
                self._logger.info(
                    "Starting fresh download (overwriting partial file): %s.",
                    abs_path,
                )

        effective_resume = resume and can_resume
        file_mode = "ab" if effective_resume else "wb"
        initial_bytes = resume_offset if effective_resume else 0

        if expected_size is not None and min_content_length is not None:
            if expected_size < min_content_length:
                self._logger.warning(
                    "expected_size (%d) is less than min_content_length (%d).",
                    expected_size,
                    min_content_length,
                )

        max_attempts = self._config.max_retries + 1
        effective_progress_callback = (
            progress_callback if progress_callback is not None
            else self._progress_callback
        )

        last_exception: Optional[Exception] = None
        response: Optional[requests.Response] = None
        file_handle: Optional[IO[bytes]] = None
        original_existing_size = self._get_file_size(abs_path)
        download_succeeded = False

        for attempt in range(max_attempts):
            progress.attempts = attempt + 1
            req_headers = self._build_request_headers(
                resume_offset if effective_resume else 0,
                extra_headers,
            )

            self._logger.info(
                "Download attempt %d/%d for %s -> %s%s",
                attempt + 1,
                max_attempts,
                url,
                abs_path,
                f" (offset={resume_offset})" if effective_resume else "",
            )

            try:
                request_kwargs: dict[str, Any] = {
                    "url": url,
                    "headers": req_headers,
                    "stream": True,
                    "timeout": (_CONNECT_TIMEOUT, _READ_TIMEOUT),
                }

                response = self._http_client._session.get(**request_kwargs)

                if not self._is_206_response(response) and effective_resume and attempt > 0:
                    effective_resume = False
                    file_mode = "ab"
                    resume_offset = initial_bytes
                    req_headers = self._build_request_headers(0, extra_headers)
                    response.close()
                    response = self._http_client._session.get(
                        url, headers=req_headers, stream=True,
                        timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                    )

                status_ok = self._handle_response_status(
                    response, url, attempt, max_attempts, progress
                )
                if not status_ok:
                    if response is not None:
                        response.close()
                    self._sleep_backoff(attempt)
                    continue

                content_length = self._get_content_length(response)
                if content_length is not None and min_content_length is not None:
                    if content_length < min_content_length:
                        raise DownloadError(
                            f"Server returned Content-Length {content_length} "
                            f"which is less than minimum {min_content_length}."
                        )

                detected_type = stream_type or self._detect_stream_type(
                    response.headers.get("Content-Type"), url
                )
                self._logger.debug("Detected stream type: %s", detected_type)

                effective_expected_size = expected_size
                if self._is_206_response(response):
                    if content_length is not None:
                        effective_expected_size = (
                            content_length + resume_offset
                            if expected_size is None
                            else expected_size
                        )
                    if expected_size is None and content_length is not None:
                        effective_expected_size = content_length + resume_offset
                elif content_length is not None and expected_size is None:
                    effective_expected_size = content_length

                if expected_size is not None and effective_expected_size != expected_size:
                    effective_expected_size = expected_size

                if effective_expected_size is not None:
                    progress.expected_size = effective_expected_size
                    if not self._check_disk_space(
                        abs_path,
                        max(0, effective_expected_size - initial_bytes),
                    ):
                        raise DownloadError(
                            "Insufficient disk space for download of "
                            f"{self._format_size(effective_expected_size)}."
                        )

                file_handle = self._open_file(abs_path, file_mode)

                try:
                    total_written = self._process_stream_chunks(
                        response=response,
                        file_handle=file_handle,
                        progress=progress,
                        chunk_size=effective_chunk_size,
                        expected_total=effective_expected_size,
                        initial_bytes=initial_bytes,
                        mode=file_mode,
                    )
                finally:
                    self._close_file(file_handle)
                    file_handle = None
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass

                if total_written == initial_bytes and effective_expected_size is not None and effective_expected_size > 0:
                    if self._is_206_response(response) or effective_resume:
                        self._logger.warning(
                            "Server returned no data. Re-attempting without resume."
                        )
                        effective_resume = False
                        resume_offset = 0
                        initial_bytes = 0
                        file_mode = "wb"
                        try:
                            os.remove(abs_path)
                        except OSError:
                            pass
                        self._sleep_backoff(attempt)
                        continue

                if verify_size and expected_size is not None:
                    try:
                        self._verify_download(abs_path, expected_size)
                    except DownloadError as exc:
                        self._logger.error("Post-download verification failed: %s", exc)
                        if cleanup_on_failure and not progress.is_resumed:
                            self._cleanup_on_failure(abs_path, progress, original_existing_size)
                        raise

                progress.mark_complete()
                self._remove_progress(abs_path)
                download_succeeded = True
                self._log_download_stats(progress)

                if effective_progress_callback is not None:
                    try:
                        effective_progress_callback(
                            total_written,
                            expected_size,
                            progress.speed,
                        )
                    except Exception:
                        pass

                return total_written

            except DownloadError:
                raise
            except requests.exceptions.ConnectionError as exc:
                last_exception = exc
                delay = self._handle_connection_error(exc, attempt, max_attempts, url)
                if file_handle is not None:
                    self._close_file(file_handle)
                    file_handle = None
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                if attempt < max_attempts - 1:
                    self._sleep_backoff(attempt)
                    continue
                progress.mark_failed(exc)
                self._remove_progress(abs_path)
                raise DownloadError(
                    f"Download of '{url}' failed after {max_attempts} "
                    f"attempt(s) due to connection error.",
                    cause=exc,
                ) from exc

            except requests.exceptions.Timeout as exc:
                last_exception = exc
                self._logger.warning(
                    "Download timeout for %s (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if file_handle is not None:
                    self._close_file(file_handle)
                    file_handle = None
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                if attempt < max_attempts - 1:
                    self._sleep_backoff(attempt)
                    continue
                progress.mark_failed(exc)
                self._remove_progress(abs_path)
                raise DownloadError(
                    f"Download of '{url}' timed out after {max_attempts} attempt(s).",
                    cause=exc,
                ) from exc

            except requests.exceptions.RequestException as exc:
                last_exception = exc
                self._logger.warning(
                    "Request error during download of %s (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if file_handle is not None:
                    self._close_file(file_handle)
                    file_handle = None
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                if attempt < max_attempts - 1:
                    self._sleep_backoff(attempt)
                    continue
                progress.mark_failed(exc)
                self._remove_progress(abs_path)
                raise DownloadError(
                    f"Download of '{url}' failed after {max_attempts} attempt(s).",
                    cause=exc,
                ) from exc

            except OSError as exc:
                self._logger.error("OS error writing to '%s': %s", abs_path, exc)
                if file_handle is not None:
                    self._close_file(file_handle)
                    file_handle = None
                progress.mark_failed(exc)
                self._remove_progress(abs_path)
                raise DownloadError(
                    f"Cannot write to output file '{abs_path}': {exc}"
                ) from exc

            finally:
                if file_handle is not None:
                    self._close_file(file_handle)
                    file_handle = None
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

        progress.mark_failed(last_exception or DownloadError("Unknown download failure."))
        self._remove_progress(abs_path)

        if cleanup_on_failure and not download_succeeded and not progress.is_resumed:
            self._cleanup_on_failure(abs_path, progress, original_existing_size)

        raise DownloadError(
            f"Download of '{url}' failed after {max_attempts} attempt(s)."
        )

    # ------------------------------------------------------------------
    # High-level download methods
    # ------------------------------------------------------------------

    def download_audio(
        self,
        url: str,
        output_path: str,
        expected_size: Optional[int] = None,
        resume: bool = False,
        chunk_size: Optional[int] = None,
        progress_callback: Optional[ProgressCallback] = None,
        output_format: Optional[str] = None,
    ) -> int:
        """Download an audio stream to *output_path*.

        Wraps :meth:`download_stream` with audio-specific defaults and
        post-download format handling.

        Args:
            url: Source URL of the audio stream.
            output_path: Destination file path.  The extension may be
                changed to match *output_format* when provided.
            expected_size: Expected total size in bytes, or ``None``.
            resume: If ``True``, resume a partial download.
            chunk_size: Bytes per chunk read.  ``None`` uses config default.
            progress_callback: Override the instance progress callback.
            output_format: Audio container format (e.g. ``"mp3"``, ``"m4a"``).
                When provided, the file extension is adjusted accordingly.

        Returns:
            Total bytes written to the output file.
        """
        effective_format = output_format or self._config.audio_format
        base, _ = os.path.splitext(output_path)
        if effective_format and not output_path.lower().endswith(f".{effective_format}"):
            output_path = f"{base}.{effective_format}"

        self._logger.info(
            "Downloading audio stream: %s -> %s (format=%s)",
            url,
            output_path,
            effective_format,
        )

        return self.download_stream(
            url=url,
            output_path=output_path,
            expected_size=expected_size,
            resume=resume,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
            stream_type="audio",
        )

    def download_video(
        self,
        url: str,
        output_path: str,
        expected_size: Optional[int] = None,
        resume: bool = False,
        chunk_size: Optional[int] = None,
        progress_callback: Optional[ProgressCallback] = None,
        output_format: Optional[str] = None,
    ) -> int:
        """Download a video stream to *output_path*.

        Wraps :meth:`download_stream` with video-specific defaults and
        post-download format handling.

        Args:
            url: Source URL of the video stream.
            output_path: Destination file path.  The extension may be
                changed to match *output_format* when provided.
            expected_size: Expected total size in bytes, or ``None``.
            resume: If ``True``, resume a partial download.
            chunk_size: Bytes per chunk read.  ``None`` uses config default.
            progress_callback: Override the instance progress callback.
            output_format: Video container format (e.g. ``"mp4"``, ``"webm"``).
                When provided, the file extension is adjusted accordingly.

        Returns:
            Total bytes written to the output file.
        """
        effective_format = output_format or self._config.video_format
        base, _ = os.path.splitext(output_path)
        if effective_format and not output_path.lower().endswith(f".{effective_format}"):
            output_path = f"{base}.{effective_format}"

        self._logger.info(
            "Downloading video stream: %s -> %s (format=%s)",
            url,
            output_path,
            effective_format,
        )

        return self.download_stream(
            url=url,
            output_path=output_path,
            expected_size=expected_size,
            resume=resume,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
            stream_type="video",
        )

    # ------------------------------------------------------------------
    # Batch download helpers
    # ------------------------------------------------------------------

    def download_streams_concurrent(
        self,
        streams: list[tuple[str, str, Optional[int], bool]],
        max_workers: Optional[int] = None,
    ) -> dict[str, Union[int, Exception]]:
        """Download multiple streams concurrently with bounded parallelism.

        Args:
            streams: List of ``(url, output_path, expected_size, resume)``
                tuples describing each stream to download.
            max_workers: Maximum concurrent download threads.  ``None``
                uses ``config.max_concurrent_downloads``.

        Returns:
            A dictionary mapping each *output_path* to the number of bytes
            written on success, or the :class:`Exception` on failure.
        """
        effective_workers = max_workers or self._config.max_concurrent_downloads
        effective_workers = max(1, min(effective_workers, len(streams)))

        results: dict[str, Union[int, Exception]] = {}
        threads: list[threading.Thread] = []
        results_lock = threading.Lock()

        def _worker(
            url: str,
            out_path: str,
            exp_size: Optional[int],
            resume_flag: bool,
        ) -> None:
            try:
                result = self.download_stream(
                    url=url,
                    output_path=out_path,
                    expected_size=exp_size,
                    resume=resume_flag,
                )
                with results_lock:
                    results[out_path] = result
            except Exception as exc:
                with results_lock:
                    results[out_path] = exc

        for url, out_path, exp_size, resume_flag in streams:
            t = threading.Thread(
                target=_worker,
                args=(url, out_path, exp_size, resume_flag),
                daemon=True,
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        return results

    def cancel_download(self, output_path: str) -> bool:
        """Attempt to cancel an in-progress download.

        Marks the progress state as failed with a
        :class:`DownloadError`.  Active I/O in another thread may
        continue briefly until the current chunk completes.

        Args:
            output_path: Destination file path of the download to cancel.

        Returns:
            ``True`` if the download was found and marked as cancelled,
            ``False`` if no active download matches *output_path*.
        """
        with self._download_lock:
            progress = self._active_downloads.get(output_path)

        if progress is None:
            return False

        progress.mark_failed(DownloadError("Download cancelled by user."))
        self._logger.info("Download cancelled: %s", output_path)
        return True

    def get_progress(self, output_path: str) -> Optional[DownloadProgress]:
        """Return the current progress state for a download.

        Args:
            output_path: Destination file path.

        Returns:
            The :class:`DownloadProgress` state, or ``None`` if no
            active download matches *output_path*.
        """
        with self._download_lock:
            return self._active_downloads.get(output_path)

    def wait_for_download(
        self,
        output_path: str,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
    ) -> DownloadProgress:
        """Block until the download at *output_path* completes.

        Args:
            output_path: Destination file path to wait for.
            timeout: Maximum seconds to wait.  ``None`` waits indefinitely.
            poll_interval: Seconds between progress polls.

        Returns:
            The final :class:`DownloadProgress` state.

        Raises:
            TimeoutError: If *timeout* is reached before completion.
            DownloadError: If the download fails.
        """
        start = time.monotonic()
        while True:
            progress = self.get_progress(output_path)
            if progress is None:
                if os.path.isfile(os.path.abspath(output_path)):
                    return DownloadProgress(
                        url="",
                        output_path=output_path,
                        is_complete=True,
                        downloaded=self._get_file_size(output_path),
                    )
                raise DownloadError(
                    f"No active or completed download found at '{output_path}'."
                )
            if progress.is_complete or progress.error is not None:
                return progress
            if timeout is not None and (time.monotonic() - start) >= timeout:
                raise TimeoutError(
                    f"Timed out waiting for download of '{output_path}' "
                    f"after {timeout:.1f}s."
                )
            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> DownloadManager:
        """Support usage as a context manager.

        Returns:
            This :class:`DownloadManager` instance.
        """
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        """Cancel all active downloads and close the HTTP client.

        Args:
            exc_type: Exception type, if any.
            exc_val: Exception instance, if any.
            exc_tb: Traceback, if any.
        """
        for path in list(self._active_downloads.keys()):
            self.cancel_download(path)
        try:
            self._http_client.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        """Return a developer-friendly representation.

        Returns:
            String showing active download count and config summary.
        """
        active = len(self._active_downloads)
        return (
            f"DownloadManager("
            f"chunk_size={self._config.chunk_size}, "
            f"max_retries={self._config.max_retries}, "
            f"active_downloads={active})"
        )
