from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, TextIO

from ytdownloader.logger import get_logger, _format_size, _format_speed, _ANSI_CYAN
from ytdownloader.exceptions import YTDLException


_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"

_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_CYAN = "\033[36m"
_ANSI_MAGENTA = "\033[35m"
_ANSI_WHITE = "\033[37m"
_ANSI_GRAY = "\033[90m"
_ANSI_BLUE = "\033[34m"

_BAR_WIDTH = 40
_MIN_UPDATE_INTERVAL = 0.1
_SPEED_SAMPLES_MAX = 10


class ProgressError(YTDLException):
    """Raised when a progress display encounters an unrecoverable error."""


class _SpeedTracker:
    """Track download speed over a rolling window of recent samples.

    Attributes:
        _samples: Ordered list of ``(timestamp, bytes_delta)`` pairs.
        _max_samples: Maximum number of samples to retain.
        _total_bytes: Cumulative bytes recorded across all samples.
    """

    def __init__(self, max_samples: int = _SPEED_SAMPLES_MAX) -> None:
        """Initialise the speed tracker.

        Args:
            max_samples: Maximum number of speed samples to keep.
        """
        self._samples: list[tuple[float, float]] = []
        self._max_samples = max_samples
        self._total_bytes: float = 0.0
        self._lock = threading.Lock()

    def record(self, bytes_delta: float) -> None:
        """Record a new sample for the current time slice.

        Args:
            bytes_delta: Number of bytes downloaded since the last sample.
        """
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, float(bytes_delta)))
            self._total_bytes += float(bytes_delta)
            if len(self._samples) > self._max_samples:
                old = self._samples.pop(0)
                self._total_bytes -= old[1]

    def get_speed(self) -> float:
        """Compute the current speed in bytes per second.

        Returns:
            Estimated bytes per second, or ``0.0`` if there are too few
            samples or the time window is too short.
        """
        with self._lock:
            if len(self._samples) < 2:
                return 0.0
            oldest_time = self._samples[0][0]
            newest_time = self._samples[-1][0]
            elapsed = newest_time - oldest_time
            if elapsed <= 0:
                return 0.0
            return self._total_bytes / elapsed

    def reset(self) -> None:
        """Clear all recorded samples."""
        with self._lock:
            self._samples.clear()
            self._total_bytes = 0.0


class ProgressBar:
    """Custom terminal progress bar with ANSI color support.

    Renders a live-updating progress bar using ``\\r`` carriage return
    so that subsequent output occupies the same terminal line.

    Attributes:
        _total_size: Expected total download size in bytes.
        _desc: Human-readable description prepended to the bar.
        _unit: Unit label for byte formatting (default ``"B"``).
        _unit_scale: When ``True``, format bytes using KB/MB/GB prefixes.
        _downloaded: Cumulative bytes reported so far.
        _finished: Whether :meth:`finish` has been called.
        _start_time: Monotonic timestamp when the bar was first rendered.
        _speed_tracker: Rolling-speed estimator.
        _stream: Output stream to write to.
        _use_color: Whether ANSI colors are enabled for this stream.
        _last_rendered: Cached rendered string (avoids redundant writes).
    """

    def __init__(
        self,
        total_size: int | None,
        desc: str = "Downloading",
        unit: str = "B",
        unit_scale: bool = True,
        stream: TextIO | None = None,
    ) -> None:
        """Initialise the progress bar.

        Args:
            total_size: Expected total size in bytes, or ``None`` when the
                total size is unknown.
            desc: Short description shown before the bar (e.g. ``"Video"``).
            unit: Unit suffix appended to the formatted size.
            unit_scale: If ``True``, scale byte counts to KB/MB/GB.
            stream: Output stream.  Defaults to ``sys.stderr``.
        """
        self._total_size = total_size
        self._desc = desc
        self._unit = unit
        self._unit_scale = unit_scale
        self._downloaded: int = 0
        self._finished = False
        self._start_time = time.monotonic()
        self._speed_tracker = _SpeedTracker()
        self._stream = stream if stream is not None else sys.stderr
        self._use_color = _is_color_enabled(self._stream)
        self._last_rendered: str = ""
        self._eta_seconds: float | None = None
        self._lock = threading.Lock()

    def update(self, downloaded_bytes: int) -> None:
        """Advance the progress bar by the given number of bytes.

        If the new state differs from the last rendered state and enough
        time has elapsed, the bar is re-rendered in-place.

        Args:
            downloaded_bytes: Number of additional bytes to record.
        """
        with self._lock:
            if self._finished:
                return
            self._downloaded += downloaded_bytes
            if downloaded_bytes > 0:
                self._speed_tracker.record(float(downloaded_bytes))

    def _record_delta(self, new_downloaded: int) -> None:
        """Record the delta between the current and previous downloaded count.

        Args:
            new_downloaded: New absolute downloaded byte count.
        """
        delta = new_downloaded - self._downloaded
        if delta > 0:
            self._speed_tracker.record(float(delta))
        self._downloaded = new_downloaded

    def finish(self) -> None:
        """Mark the download as complete and render the final state."""
        with self._lock:
            self._finished = True
            self._render(force=True)

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format a speed value in bytes/sec as a human-readable string.

        Args:
            bytes_per_sec: Transfer speed in bytes per second.

        Returns:
            A string such as ``"1.2 MB/s"`` or ``"450 KB/s"``.
        """
        return _format_speed_internal(bytes_per_sec, self._unit_scale)

    def _format_eta(self, seconds: float | None) -> str:
        """Format an ETA value in seconds as a human-readable string.

        Args:
            seconds: Seconds remaining, or ``None`` if unknown.

        Returns:
            A string such as ``"01:23"``, ``"?:??"``, or ``"unknown"``.
        """
        return _format_eta_internal(seconds)

    def _format_size(self, num_bytes: int | None) -> str:
        """Format a byte count as a human-readable string.

        Args:
            num_bytes: Raw byte count.  If ``None``, returns ``"?"``.

        Returns:
            A string such as ``"1.5 MB"`` or ``"320 KB"``.
        """
        return _format_size_internal(num_bytes, self._unit_scale)

    def _compute_percentage(self) -> float | None:
        """Compute the current completion percentage.

        Returns:
            A float between ``0.0`` and ``100.0``, or ``None`` when the
            total size is unknown.
        """
        if self._total_size is None or self._total_size <= 0:
            return None
        return min(100.0, max(0.0, self._downloaded / self._total_size * 100.0))

    def _compute_eta(self) -> float | None:
        """Estimate the remaining time in seconds.

        Returns:
            Estimated seconds remaining, or ``None`` if the estimate
            cannot be computed.
        """
        speed = self._speed_tracker.get_speed()
        if speed <= 0 or self._total_size is None:
            return None
        remaining = self._total_size - self._downloaded
        if remaining <= 0:
            return 0.0
        return remaining / speed

    def _build_bar(self, percentage: float | None) -> str:
        """Construct the visual bar segment.

        Args:
            percentage: Completion percentage between ``0.0`` and ``100.0``.

        Returns:
            A string of length :data:`_BAR_WIDTH` containing the filled
            and empty portions of the bar.
        """
        if percentage is None:
            filled = 0
        else:
            filled = int(round(percentage / 100.0 * _BAR_WIDTH))
            filled = max(0, min(_BAR_WIDTH, filled))
        empty = _BAR_WIDTH - filled
        bar_char = self._colorize("#", _ANSI_GREEN) if self._use_color else "#"
        empty_char = self._colorize("-", _ANSI_GRAY) if self._use_color else "-"
        return bar_char * filled + empty_char * empty

    def _build_info(self, percentage: float | None, speed: float, eta: float | None) -> str:
        """Build the info segment that appears to the right of the bar.

        Args:
            percentage: Completion percentage.
            speed: Current speed in bytes per second.
            eta: Estimated seconds remaining.

        Returns:
            Formatted info string.
        """
        downloaded_str = self._format_size(self._downloaded)

        if self._total_size is not None:
            total_str = self._format_size(self._total_size)
            size_info = f"{downloaded_str}/{total_str}"
        else:
            size_info = downloaded_str

        if percentage is not None:
            pct_str = f"{percentage:5.1f}%"
        else:
            pct_str = "  ?.?"

        speed_str = self._format_speed(speed)
        eta_str = self._format_eta(eta)

        return (
            f"  {pct_str} [{size_info}] {speed_str}  ETA {eta_str}"
        )

    def _render(self, force: bool = False) -> str:
        """Render the full progress line to the configured stream.

        Args:
            force: If ``True``, render even if the state has not changed.

        Returns:
            The rendered string that was written (or would be written).
        """
        speed = self._speed_tracker.get_speed()
        percentage = self._compute_percentage()
        eta = self._compute_eta()

        if self._finished:
            percentage = 100.0
            eta = 0.0
            speed = max(speed, 0.0)

        bar = self._build_bar(percentage)
        info = self._build_info(percentage, speed, eta)

        desc_part = f"{self._desc}: " if self._desc else ""
        line = f"\r{desc_part}|{bar}|{info}"

        if self._finished:
            line = line + " " + self._colorize("[DONE]", _ANSI_GREEN)

        if line == self._last_rendered and not force:
            return line

        self._last_rendered = line

        try:
            self._stream.write(line)
            self._stream.flush()
        except (OSError, ValueError) as exc:
            raise ProgressError(
                f"Failed to write progress bar to stream: {exc}"
            ) from exc

        return line

    def render(self) -> str:
        """Public render method — forces an immediate redraw.

        Returns:
            The rendered string.
        """
        with self._lock:
            return self._render(force=True)

    def colorize(self, text: str, color: str) -> str:
        """Wrap *text* in ANSI color codes.

        Args:
            text: The string to colorize.
            color: ANSI escape sequence constant.

        Returns:
            The colorized string, or *text* unchanged when colors are
            disabled.
        """
        return self._colorize(text, color)

    def _colorize(self, text: str, color: str) -> str:
        """Internal helper to wrap *text* in ANSI color codes.

        Args:
            text: The string to colorize.
            color: ANSI escape sequence constant.

        Returns:
            The colorized string, or *text* unchanged when colors are
            disabled.
        """
        if not self._use_color:
            return text
        return f"{color}{text}{_ANSI_RESET}"

    def __enter__(self) -> ProgressBar:
        """Support usage as a context manager.

        Returns:
            This :class:`ProgressBar` instance.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Finish the bar when the context exits.

        Args:
            exc_type: Exception type, if any.
            exc_val: Exception instance, if any.
            exc_tb: Traceback, if any.
        """
        self.finish()

    def __repr__(self) -> str:
        """Return a developer-readable representation.

        Returns:
            A string showing the current state of the bar.
        """
        pct = self._compute_percentage()
        pct_str = f"{pct:.1f}%" if pct is not None else "unknown"
        return (
            f"ProgressBar(desc={self._desc!r}, "
            f"downloaded={self._downloaded}, "
            f"total={self._total_size}, "
            f"progress={pct_str})"
        )


class SilentProgress:
    """No-op progress sink used when quiet output is requested.

    All public methods are no-ops so that callers can pass either a
    :class:`ProgressBar` or a :class:`SilentProgress` without any
    conditional logic.

    Attributes:
        _total_size: Placeholder total size attribute.
        _downloaded: Placeholder downloaded count attribute.
        _finished: Placeholder finished flag attribute.
    """

    def __init__(self, total_size: int | None = None, desc: str = "Downloading") -> None:
        """Initialise the silent sink.

        Args:
            total_size: Unused; accepted for API compatibility with
                :class:`ProgressBar`.
            desc: Unused; accepted for API compatibility.
        """
        self._total_size = total_size
        self._downloaded = 0
        self._finished = False

    def update(self, downloaded_bytes: int) -> None:
        """No-op update.

        Args:
            downloaded_bytes: Number of additional bytes to record.
        """
        self._downloaded += downloaded_bytes

    def finish(self) -> None:
        """No-op finish."""
        self._finished = True

    def render(self) -> str:
        """No-op render.

        Returns:
            An empty string.
        """
        return ""

    def colorize(self, text: str, color: str) -> str:
        """Return *text* unchanged.

        Args:
            text: The string to pass through.
            color: Ignored.

        Returns:
            *text* unmodified.
        """
        return text

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed; returns a plain string.

        Args:
            bytes_per_sec: Transfer speed in bytes per second.

        Returns:
            Formatted speed string.
        """
        return _format_speed_internal(bytes_per_sec, unit_scale=True)

    def _format_eta(self, seconds: float | None) -> str:
        """Format ETA; returns a plain string.

        Args:
            seconds: Seconds remaining.

        Returns:
            Formatted ETA string.
        """
        return _format_eta_internal(seconds)

    def _format_size(self, num_bytes: int | None) -> str:
        """Format size; returns a plain string.

        Args:
            num_bytes: Byte count.

        Returns:
            Formatted size string.
        """
        return _format_size_internal(num_bytes, unit_scale=True)

    def __enter__(self) -> SilentProgress:
        """Support usage as a context manager.

        Returns:
            This :class:`SilentProgress` instance.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """No-op context manager exit.

        Args:
            exc_type: Exception type, if any.
            exc_val: Exception instance, if any.
            exc_tb: Traceback, if any.
        """
        self.finish()

    def __repr__(self) -> str:
        """Return a developer-readable representation.

        Returns:
            ``"SilentProgress()"``
        """
        return "SilentProgress()"


class _ProgressEntry:
    """Internal container tracking a single bar within :class:`MultiProgress`.

    Attributes:
        bar: The underlying :class:`ProgressBar` (or :class:`SilentProgress`).
        _lock: Threading lock protecting mutable state.
        _last_render_time: Monotonic timestamp of the last render.
        _render_interval: Minimum seconds between renders for this entry.
    """

    def __init__(self, bar: ProgressBar | SilentProgress, render_interval: float = _MIN_UPDATE_INTERVAL) -> None:
        """Initialise the entry.

        Args:
            bar: Progress bar instance to manage.
            render_interval: Minimum seconds between consecutive renders.
        """
        self.bar = bar
        self._lock = threading.Lock()
        self._last_render_time: float = 0.0
        self._render_interval = render_interval

    def maybe_render(self, force: bool = False) -> None:
        """Render the bar if enough time has elapsed since the last render.

        Args:
            force: If ``True``, render regardless of timing.
        """
        now = time.monotonic()
        with self._lock:
            if force or (now - self._last_render_time >= self._render_interval):
                self.bar.render()
                self._last_render_time = now


class MultiProgress:
    """Manage multiple simultaneous :class:`ProgressBar` instances.

    Renders all bars in-place, one per line, using ANSI cursor movement
    so that concurrent downloads each have their own visual slot.

    Attributes:
        _entries: Ordered list of :class:`_ProgressEntry` instances.
        _stream: Output stream shared by all bars.
        _use_color: Whether ANSI colors are active.
        _lock: Global lock protecting the render sequence.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        render_interval: float = _MIN_UPDATE_INTERVAL,
    ) -> None:
        """Initialise the multi-progress manager.

        Args:
            stream: Output stream.  Defaults to ``sys.stderr``.
            render_interval: Minimum seconds between renders.
        """
        self._entries: list[_ProgressEntry] = []
        self._stream = stream if stream is not None else sys.stderr
        self._use_color = _is_color_enabled(self._stream)
        self._render_interval = render_interval
        self._lock = threading.Lock()
        self._finished_count = 0

    def new_bar(
        self,
        total_size: int | None,
        desc: str = "Downloading",
        unit: str = "B",
        unit_scale: bool = True,
        bar_class: type[ProgressBar | SilentProgress] = ProgressBar,
    ) -> ProgressBar | SilentProgress:
        """Create and register a new progress bar.

        Args:
            total_size: Expected total size in bytes.
            desc: Short description shown before the bar.
            unit: Unit label for byte formatting.
            unit_scale: If ``True``, scale byte counts to KB/MB/GB.
            bar_class: Class to instantiate.  Pass
                :class:`SilentProgress` for quiet mode.

        Returns:
            The newly created and registered progress bar.
        """
        bar = bar_class(total_size=total_size, desc=desc, unit=unit, unit_scale=unit_scale, stream=self._stream)
        entry = _ProgressEntry(bar, render_interval=self._render_interval)
        with self._lock:
            self._entries.append(entry)
        return bar

    def update(self, bar: ProgressBar | SilentProgress, downloaded_bytes: int) -> None:
        """Advance *bar* and schedule a re-render of all visible bars.

        Args:
            bar: The progress bar to update.
            downloaded_bytes: Number of additional bytes downloaded.
        """
        bar.update(downloaded_bytes)
        self._render_all()

    def finish(self, bar: ProgressBar | SilentProgress) -> None:
        """Mark *bar* as complete and re-render all visible bars.

        Args:
            bar: The progress bar to finish.
        """
        bar.finish()
        with self._lock:
            self._finished_count += 1
        self._render_all(force=True)

    def _render_all(self, force: bool = False) -> None:
        """Render every registered bar in order.

        Args:
            force: If ``True``, render every bar regardless of timing.
        """
        lines: list[str] = []
        with self._lock:
            entries = list(self._entries)

        for entry in entries:
            entry.maybe_render(force=force)

        if self._use_color and entries:
            self._move_cursor_up(len(entries))

    def _move_cursor_up(self, n: int) -> None:
        """Emit an ANSI cursor-up sequence.

        Args:
            n: Number of lines to move the cursor up.
        """
        if n <= 0:
            return
        try:
            self._stream.write(f"\033[{n}A")
            self._stream.flush()
        except (OSError, ValueError):
            pass

    def clear(self) -> None:
        """Erase all rendered progress bars from the terminal."""
        try:
            with self._lock:
                count = len(self._entries)
            if count > 0:
                self._move_cursor_up(count)
                erase = "\033[J"
                self._stream.write(erase)
                self._stream.flush()
        except (OSError, ValueError):
            pass

    def __enter__(self) -> MultiProgress:
        """Support usage as a context manager.

        Returns:
            This :class:`MultiProgress` instance.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Clear all bars when the context exits.

        Args:
            exc_type: Exception type, if any.
            exc_val: Exception instance, if any.
            exc_tb: Traceback, if any.
        """
        self.clear()

    def __repr__(self) -> str:
        """Return a developer-readable representation.

        Returns:
            A string showing the number of registered bars.
        """
        return f"MultiProgress(bars={len(self._entries)})"


def _is_color_enabled(stream: TextIO) -> bool:
    """Determine whether ANSI colors should be emitted to *stream*.

    Colors are enabled when the stream is attached to an interactive TTY.

    Args:
        stream: The output stream to inspect.

    Returns:
        ``True`` when colors should be used.
    """
    try:
        return hasattr(stream, "isatty") and stream.isatty()
    except (OSError, ValueError):
        return False


def _format_size_internal(num_bytes: int | None, unit_scale: bool) -> str:
    """Format a byte count as a human-readable string.

    Args:
        num_bytes: Raw byte count.  If ``None``, returns ``"?"``.
        unit_scale: If ``True``, apply KB/MB/GB prefixes.

    Returns:
        A string such as ``"1.5 MB"`` or ``"320 KB"``.
    """
    if num_bytes is None:
        return "?"
    if not unit_scale:
        return f"{num_bytes} B"
    abs_bytes = abs(num_bytes)
    if abs_bytes >= 1 << 30:
        return f"{num_bytes / (1 << 30):.2f} GB"
    if abs_bytes >= 1 << 20:
        return f"{num_bytes / (1 << 20):.2f} MB"
    if abs_bytes >= 1 << 10:
        return f"{num_bytes / (1 << 10):.2f} KB"
    return f"{num_bytes} B"


def _format_speed_internal(bytes_per_sec: float, unit_scale: bool) -> str:
    """Format a speed value in bytes/sec as a human-readable string.

    Args:
        bytes_per_sec: Transfer speed in bytes per second.
        unit_scale: If ``True``, apply KB/MB/GB prefixes.

    Returns:
        A string such as ``"1.2 MB/s"`` or ``"450 KB/s"``.
    """
    if not _isfinite(bytes_per_sec) or bytes_per_sec < 0:
        return "0 B/s"
    if not unit_scale:
        return f"{bytes_per_sec:.0f} B/s"
    if bytes_per_sec >= 1 << 30:
        return f"{bytes_per_sec / (1 << 30):.2f} GB/s"
    if bytes_per_sec >= 1 << 20:
        return f"{bytes_per_sec / (1 << 20):.2f} MB/s"
    if bytes_per_sec >= 1 << 10:
        return f"{bytes_per_sec / (1 << 10):.2f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _format_eta_internal(seconds: float | None) -> str:
    """Format an ETA value in seconds as a human-readable string.

    Args:
        seconds: Seconds remaining, or ``None`` if unknown.

    Returns:
        A string such as ``"01:23"``, ``"?:??"``, or ``"unknown"``.
    """
    if seconds is None or seconds < 0 or not _isfinite(seconds):
        return "?:??"
    if seconds > 86399:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours:d}:{minutes:02d}:00"
    if seconds > 3599:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:d}:{secs:02d}"


def _isfinite(value: float) -> bool:
    """Return ``True`` if *value* is a finite float.

    Args:
        value: Numeric value to test.

    Returns:
        ``True`` when *value* is neither ``inf`` nor ``nan``.
    """
    return value == value and value != float("inf") and value != float("-inf")


__all__ = [
    "ProgressBar",
    "SilentProgress",
    "MultiProgress",
    "ProgressError",
    "_SpeedTracker",
    "_ProgressEntry",
    "_format_size_internal",
    "_format_speed_internal",
    "_format_eta_internal",
    "_is_color_enabled",
    "_isfinite",
    "_BAR_WIDTH",
    "_MIN_UPDATE_INTERVAL",
    "_SPEED_SAMPLES_MAX",
]
