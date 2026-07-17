from __future__ import annotations

import logging
import os
import sys
from typing import Any

try:
    import colorama

    colorama.init()
    _COLORAMA_AVAILABLE = True
except ImportError:
    _COLORAMA_AVAILABLE = False


_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_UNDERLINE = "\033[4m"

_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_MAGENTA = "\033[35m"
_ANSI_WHITE = "\033[37m"
_ANSI_GRAY = "\033[90m"
_ANSI_BLUE = "\033[34m"


__all__ = [
    "YTLogger",
    "get_logger",
    "_configure_logging",
    "log_format_info",
    "log_format_debug",
    "debug_log_request",
    "debug_log_response",
    "log_extract_start",
    "log_extract_success",
    "log_download_start",
    "log_download_progress",
    "log_download_complete",
    "log_format_found",
    "log_warning",
    "log_error",
    "log_critical",
    "_format_size",
    "_format_speed",
    "_is_tty",
]


def _is_tty(stream: Any = None) -> bool:
    """Return ``True`` if *stream* is attached to an interactive terminal.

    Args:
        stream: File-like object to inspect.  Defaults to ``sys.stdout``.

    Returns:
        ``True`` when the stream is a TTY or when ``colorama`` is installed.
    """
    if _COLORAMA_AVAILABLE:
        return True
    if stream is None:
        stream = sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


def _colorize(text: str, color: str, force: bool = False) -> str:
    """Wrap *text* in ANSI color codes when colors are enabled.

    Colors are suppressed when stdout is not a TTY unless *force* is set
    to ``True`` or ``colorama`` is available.

    Args:
        text: The string to colorize.
        color: ANSI escape sequence (e.g. ``_ANSI_RED``).
        force: If ``True``, emit color codes regardless of TTY state.

    Returns:
        The colorized string, or the original *text* when colors are disabled.
    """
    if not force and not _is_tty() and not _COLORAMA_AVAILABLE:
        return text
    return f"{color}{text}{_ANSI_RESET}"


class _ColorFormatter(logging.Formatter):
    """Logging formatter that injects ANSI colors per-level."""

    LEVEL_COLORS = {
        logging.DEBUG: _ANSI_CYAN,
        logging.INFO: _ANSI_GREEN,
        logging.WARNING: _ANSI_YELLOW,
        logging.ERROR: _ANSI_RED,
        logging.CRITICAL: _ANSI_MAGENTA,
    }

    LEVEL_PREFIXES = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.ERROR: "ERROR",
        logging.WARNING: "WARN",
        logging.CRITICAL: "CRIT",
    }

    def __init__(self, use_color: bool = True, detailed: bool = False) -> None:
        fmt = (
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            if detailed
            else "%(levelname)s | %(message)s"
        )
        super().__init__(fmt, datefmt="%H:%M:%S")
        self.use_color = use_color and sys.stdout.isatty()
        self.detailed = detailed

    def format(self, record: logging.LogRecord) -> str:
        levelno = record.levelno
        color = self.LEVEL_COLORS.get(levelno, "")
        prefix = self.LEVEL_PREFIXES.get(levelno, str(levelno))

        if self.use_color:
            record.levelname = _colorize(f"{prefix}", color)
            record.name = _colorize(record.name, _ANSI_GRAY)
        else:
            record.levelname = prefix

        result = super().format(record)

        if self.use_color and levelno >= logging.ERROR:
            result = _colorize(result, color)

        return result


class YTLogger:
    """Thin wrapper around :class:`logging.Logger` providing colored output and convenience helpers.

    Attributes:
        _logger: The underlying :class:`logging.Logger` instance.
    """

    def __init__(self, name: str = "ytdownloader") -> None:
        """Initialise the logger wrapper.

        Args:
            name: Logger name passed to :func:`logging.getLogger`.
        """
        self._logger = logging.getLogger(name)
        self._logger.propagate = False
        self._configured = False

    def _ensure_handlers(self) -> None:
        """Attach a console handler (and optional file handler) if not yet configured."""
        if self._configured:
            return

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(_ColorFormatter(use_color=True, detailed=True))

        self._logger.addHandler(console_handler)
        self._logger.setLevel(logging.INFO)

        log_file = os.environ.get("YT_LOG_FILE")
        if log_file:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            self._logger.addHandler(file_handler)

        self._configured = True

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a debug-level message.

        Args:
            message: Format string.
            *args: Positional arguments for string formatting.
            **kwargs: Keyword arguments forwarded to the underlying logger.
        """
        self._ensure_handlers()
        self._logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an info-level message.

        Args:
            message: Format string.
            *args: Positional arguments for string formatting.
            **kwargs: Keyword arguments forwarded to the underlying logger.
        """
        self._ensure_handlers()
        self._logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a warning-level message.

        Args:
            message: Format string.
            *args: Positional arguments for string formatting.
            **kwargs: Keyword arguments forwarded to the underlying logger.
        """
        self._ensure_handlers()
        self._logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an error-level message.

        Args:
            message: Format string.
            *args: Positional arguments for string formatting.
            **kwargs: Keyword arguments forwarded to the underlying logger.
        """
        self._ensure_handlers()
        self._logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a critical-level message.

        Args:
            message: Format string.
            *args: Positional arguments for string formatting.
            **kwargs: Keyword arguments forwarded to the underlying logger.
        """
        self._ensure_handlers()
        self._logger.critical(message, *args, **kwargs)

    def set_level(self, level: int) -> None:
        """Set the minimum log level for this logger.

        Args:
            level: Logging level constant (e.g. ``logging.DEBUG``).
        """
        self._ensure_handlers()
        self._logger.setLevel(level)


_module_logger = YTLogger()


def get_logger(name: str) -> logging.Logger:
    """Return a child logger bound to *name*.

    This factory function is the preferred way for other modules in the
    package to obtain a logger without instantiating :class:`YTLogger`
    themselves.

    Args:
        name: Dotted module name (typically ``__name__``).

    Returns:
        A :class:`logging.Logger` instance configured with the package defaults.
    """
    return logging.getLogger(f"ytdownloader.{name}")


def _configure_logging(
    level: int = logging.INFO,
    format_type: str = "detailed",
    log_file: str | None = None,
) -> None:
    """Reconfigure the module-level logger with the supplied settings.

    Calling this function mutates the global ``_module_logger``.  It is
    intentionally module-level so callers can tweak logging before any
    other module retrieves a child logger.

    Args:
        level: Minimum severity to emit (e.g. ``logging.DEBUG``).
        format_type: ``"detailed"`` for timestamps and module names,
            ``"simple"`` for level + message only.
        log_file: Optional path to a file that should receive all log output.
            Pass ``None`` to disable file logging.
    """
    global _module_logger

    if log_file:
        os.environ.setdefault("YT_LOG_FILE", log_file)

    _module_logger = YTLogger()
    _module_logger.set_level(level)

    for handler in list(_module_logger._logger.handlers):
        use_color = handler.stream == sys.stdout
        detailed = format_type == "detailed"

        if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
            handler.setFormatter(_ColorFormatter(use_color=use_color, detailed=detailed))
        elif isinstance(handler, logging.FileHandler):
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )


def log_format_info() -> logging.Formatter:
    """Return a preset formatter for info-level console output.

    Returns:
        A :class:`logging.Formatter` that omits the timestamp for concise logs.
    """
    return logging.Formatter("%(levelname)s | %(message)s")


def log_format_debug() -> logging.Formatter:
    """Return a preset formatter for verbose debug output.

    Returns:
        A :class:`logging.Formatter` including timestamps and module names.
    """
    return logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def debug_log_request(url: str, method: str = "GET", headers: dict[str, str] | None = None) -> None:
    """Log an outgoing HTTP request at DEBUG level.

    Args:
        url: The request URL.
        method: HTTP method (``GET``, ``POST``, etc.).
        headers: Optional mapping of request headers to include in the log.
    """
    _module_logger.debug("HTTP %s %s", method, url)
    if headers:
        header_str = ", ".join(f"{k}={v}" for k, v in headers.items())
        _module_logger.debug("Headers: %s", header_str)


def debug_log_response(
    status: int,
    headers: dict[str, str] | None = None,
    body_preview: str = "",
) -> None:
    """Log an HTTP response at DEBUG level.

    Args:
        status: Numeric HTTP status code.
        headers: Optional mapping of response headers.
        body_preview: Short excerpt of the response body for inspection.
    """
    _module_logger.debug("HTTP %d", status)
    if headers:
        header_str = ", ".join(f"{k}={v}" for k, v in headers.items())
        _module_logger.debug("Headers: %s", header_str)
    if body_preview:
        preview = body_preview[:200]
        _module_logger.debug("Body preview: %s", preview)


def log_extract_start(url: str) -> None:
    """Log the beginning of a video extraction attempt.

    Args:
        url: The YouTube URL being processed.
    """
    _module_logger.info(_colorize("Extracting video info: %s", _ANSI_CYAN), url)


def log_extract_success(video_id: str) -> None:
    """Log a successful video info extraction.

    Args:
        video_id: The 11-character YouTube video identifier.
    """
    _module_logger.info(_colorize("Extracted video: %s", _ANSI_GREEN), video_id)


def log_download_start(url: str, path: str, size: int | None = None) -> None:
    """Log the start of a stream download.

    Args:
        url: The stream URL being downloaded.
        path: Local filesystem path where the stream will be saved.
        size: Expected total size in bytes, or ``None`` if unknown.
    """
    size_str = f" ({_format_size(size)})" if size else ""
    _module_logger.info(
        _colorize("Downloading%s -> %s", _ANSI_YELLOW),
        size_str,
        path,
    )
    _module_logger.debug("URL: %s", url)


def log_download_progress(downloaded: int, total: int | None, speed: float) -> None:
    """Log download progress.

    Args:
        downloaded: Number of bytes downloaded so far.
        total: Expected total size in bytes, or ``None`` if unknown.
        speed: Current download speed in bytes per second.
    """
    total_str = f"/ {_format_size(total)}" if total else ""
    pct = f"{(downloaded / total * 100):.1f}%" if total else "?.?%"
    _module_logger.info(
        "%s | %s%s | %s/s",
        _colorize(f"[{pct}]", _ANSI_CYAN),
        _format_size(downloaded),
        total_str,
        _format_speed(speed),
    )


def log_download_complete(path: str, size: int) -> None:
    """Log the successful completion of a download.

    Args:
        path: Local filesystem path of the downloaded file.
        size: Final file size in bytes.
    """
    _module_logger.info(
        _colorize("Download complete: %s (%s)", _ANSI_GREEN),
        path,
        _format_size(size),
    )


def log_format_found(itag: int, quality: str, size: int | None) -> None:
    """Log the selection of a specific stream format.

    Args:
        itag: YouTube itag number identifying the format.
        quality: Human-readable quality label (e.g. ``"720p"``).
        size: Estimated file size in bytes, or ``None``.
    """
    size_str = f" ~ {_format_size(size)}" if size else ""
    _module_logger.info(
        _colorize("Selected itag=%d (%s%s)", _ANSI_MAGENTA),
        itag,
        quality,
        size_str,
    )


def log_warning(message: str, *args: Any, **kwargs: Any) -> None:
    """Log a structured warning message.

    Args:
        message: Format string.
        *args: Positional arguments for string formatting.
        **kwargs: Keyword arguments forwarded to the underlying logger.
    """
    _module_logger.warning(message, *args, **kwargs)


def log_error(message: str, *args: Any, **kwargs: Any) -> None:
    """Log a structured error message.

    Args:
        message: Format string.
        *args: Positional arguments for string formatting.
        **kwargs: Keyword arguments forwarded to the underlying logger.
    """
    _module_logger.error(message, *args, **kwargs)


def log_critical(message: str, *args: Any, **kwargs: Any) -> None:
    """Log a structured critical message.

    Args:
        message: Format string.
        *args: Positional arguments for string formatting.
        **kwargs: Keyword arguments forwarded to the underlying logger.
    """
    _module_logger.critical(message, *args, **kwargs)


def _format_size(num_bytes: int | None) -> str:
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


def _format_speed(bytes_per_sec: float) -> str:
    """Format a speed value in bytes/sec as a human-readable string.

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
