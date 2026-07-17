"""
CLI entry point for ytdownloader.

Provides a full-featured command-line interface for downloading YouTube videos
and audio with extensive format, quality, subtitle, proxy, and cookie support.

Usage examples:
    python -m ytdownloader "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --audio --quality 320k "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --info "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --output ./downloads --quality 720p "https://..."
    python -m ytdownloader --list-formats "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --subtitles --subtitle-lang en "https://..."
    python -m ytdownloader --resume --proxy http://proxy:8080 "https://..."
    python -m ytdownloader --config myconfig.yaml "https://..."
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .downloader import download_audio, download_video, print_video_info
from .exceptions import (
    AgeRestrictedError,
    ConfigError,
    DownloadError,
    FormatSelectionError,
    GeoRestrictedError,
    InvalidURLError,
    MetadataExtractionError,
    NetworkError,
    SubtitleError,
    VideoUnavailableError,
)
from .logger import get_logger, _configure_logging
from .metadata import get_video_info
from .utils import is_valid_youtube_url, normalize_youtube_url


# ---------------------------------------------------------------------------
# ANSI color escape sequences
# ---------------------------------------------------------------------------

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_UNDERLINE = "\033[4m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_MAGENTA = "\033[35m"
ANSI_CYAN = "\033[36m"
ANSI_WHITE = "\033[37m"
ANSI_GRAY = "\033[90m"


def _c(text: str, color: str) -> str:
    """Wrap *text* in ANSI color codes."""
    return f"{color}{text}{ANSI_RESET}"


def _is_color_enabled(force: bool = False) -> bool:
    """Return True if ANSI color output should be used."""
    if force:
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _info(text: str) -> str:
    """Format an informational message in green."""
    return _c(text, ANSI_GREEN) if _is_color_enabled() else text


def _warn(text: str) -> str:
    """Format a warning message in yellow."""
    return _c(text, ANSI_YELLOW) if _is_color_enabled() else text


def _err(text: str) -> str:
    """Format an error message in red."""
    return _c(text, ANSI_RED) if _is_color_enabled() else text


def _heading(text: str) -> str:
    """Format a heading string in bold cyan."""
    return _c(_c(text, ANSI_BOLD), ANSI_CYAN) if _is_color_enabled() else text


def _dim(text: str) -> str:
    """Format a dimmed/gray message."""
    return _c(text, ANSI_GRAY) if _is_color_enabled() else text


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_size(num_bytes: int | float | None) -> str:
    """Return a human-readable file-size string.

    Args:
        num_bytes: Raw byte count.  Returns ``"?"`` when *None* or invalid.

    Returns:
        A string such as ``"1.50 GB"`` or ``"320.00 KB"``.
    """
    if num_bytes is None:
        return "?"
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    abs_size = abs(size)
    if abs_size >= 1 << 40:
        return f"{size / (1 << 40):.2f} TB"
    if abs_size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    if abs_size >= 1 << 20:
        return f"{size / (1 << 20):.2f} MB"
    if abs_size >= 1 << 10:
        return f"{size / (1 << 10):.2f} KB"
    return f"{size:.0f} B"


def _print_video_info(info: dict[str, Any]) -> None:
    """Pretty-print video metadata to stdout.

    Args:
        info: Metadata dictionary returned by :func:`get_video_info`.
    """
    sep = "=" * 64 if _is_color_enabled() else "=" * 64
    print()
    print(_heading(sep))
    print(f"  {_c('Title:', ANSI_BOLD)}        {info.get('title', 'N/A')}")
    print(f"  {_c('Video ID:', ANSI_BOLD)}     {info.get('id', 'N/A')}")
    print(f"  {_c('Author:', ANSI_BOLD)}       {info.get('author', 'N/A')}")
    print(f"  {_c('Channel ID:', ANSI_BOLD)}   {info.get('channel_id', 'N/A')}")
    duration = info.get("duration", "N/A")
    print(f"  {_c('Duration:', ANSI_BOLD)}     {duration}")
    upload_date = info.get("upload_date", "N/A")
    print(f"  {_c('Upload Date:', ANSI_BOLD)}  {upload_date}")
    view_count = info.get("view_count")
    if view_count is not None:
        view_str = f"{view_count:,}"
    else:
        view_str = "N/A"
    print(f"  {_c('Views:', ANSI_BOLD)}        {view_str}")
    live = info.get("live_status")
    print(f"  {_c('Live:', ANSI_BOLD)}         {live if live is not None else 'No'}")
    private = info.get("is_private")
    print(f"  {_c('Private:', ANSI_BOLD)}      {private if private is not None else 'No'}")
    print(_heading(sep))

    keywords = info.get("keywords", [])
    if keywords:
        print(f"  {_c('Keywords:', ANSI_BOLD)}     {', '.join(keywords[:10])}")

    thumbnails = info.get("thumbnail", [])
    if thumbnails:
        best_thumb = thumbnails[-1].get("url", "N/A")
        print(f"  {_c('Thumbnail:', ANSI_BOLD)}    {best_thumb}")

    formats = info.get("formats", [])
    adaptive = info.get("adaptiveFormats", [])
    all_formats = formats + adaptive

    if all_formats:
        print()
        print(_heading(f"  Available Formats ({len(all_formats)}):"))
        header = f"  {'ID':<8} {'Type':<14} {'Quality':<12} {'Size':<12} {'FPS':<6} {'Codec':<20} {'Protocol'}"
        print(header)
        print(f"  {'-' * 82}")

        sorted_formats = sorted(
            all_formats,
            key=lambda f: (
                0 if f.get("vcodec", "") not in ("none", "") and f.get("acodec", "") not in ("none", "") else
                1 if f.get("vcodec", "") not in ("none", "") else
                2,
                -(f.get("height") or 0),
                -(f.get("tbr") or 0),
            ),
        )
        for fmt in sorted_formats:
            _print_single_format(fmt)
    print()


def _print_single_format(fmt: dict[str, Any]) -> None:
    """Print a single format row to stdout.

    Args:
        fmt: A single format dictionary from YouTube's streaming data.
    """
    itag = str(fmt.get("itag", "?"))
    vcodec = fmt.get("vcodec", "") or ""
    acodec = fmt.get("acodec", "") or ""
    ext = fmt.get("ext", "?") or "?"

    if vcodec == "none" and acodec != "none":
        fmt_type = "audio"
    elif vcodec != "none" and acodec == "none":
        fmt_type = "video"
    elif vcodec != "none" and acodec != "none":
        fmt_type = "audio+video"
    else:
        fmt_type = "unknown"

    height = fmt.get("height")
    width = fmt.get("width")
    fps = fmt.get("fps")
    tbr = fmt.get("tbr")
    abr = fmt.get("abr")
    vbr = fmt.get("vbr")
    approx_dur = fmt.get("approxDurationMs")
    content_length = fmt.get("contentLength")
    protocol = fmt.get("protocol", "?") or "?"
    mime_type = fmt.get("mimeType", "") or ""

    if height:
        if width:
            quality = f"{width}x{height}"
        else:
            quality = f"{height}p"
    elif abr:
        quality = f"{abr}k audio"
    else:
        quality = "unknown"

    if tbr:
        bitrate = f"{tbr}k"
    elif vbr:
        bitrate = f"{vbr}k"
    elif abr:
        bitrate = f"{abr}k"
    else:
        bitrate = ""

    if vcodec and vcodec != "none":
        codec_str = f"v:{vcodec}"
        if acodec and acodec != "none":
            codec_str += f" a:{acodec}"
    elif acodec and acodec != "none":
        codec_str = f"a:{acodec}"
    else:
        codec_str = "?"

    size_str = _format_size(content_length)
    fps_str = str(fps) if fps else ""

    print(f"  {itag:<8} {fmt_type:<14} {quality:<12} {size_str:<12} {fps_str:<6} {codec_str:<20} {protocol}")


def _parse_mime_type(mime: str) -> tuple[str, str, str]:
    """Parse a MIME type string into (container, vcodec, acodec).

    Args:
        mime: Raw MIME type string such as ``"video/webm; codecs=\\"vp9\\""``.

    Returns:
        A 3-tuple of ``(container, vcodec, acodec)`` with lower-case values.
        Missing components are returned as empty strings.
    """
    container = ""
    vcodec = ""
    acodec = ""

    parts = mime.split(";")
    media_type = parts[0].strip().lower() if parts else ""
    if "/" in media_type:
        container = media_type.split("/")[1].strip()

    for part in parts[1:]:
        part = part.strip()
        if part.startswith("codecs="):
            codecs_str = part[7:].strip().strip('"').strip("'")
            codecs = [c.strip() for c in codecs_str.split(",")]
            for codec in codecs:
                cl = codec.lower()
                if cl in ("vp8", "vp9", "avc1", "avc2", "h263", "mp4v", "h264"):
                    vcodec = cl
                elif cl in ("aac", "mp3", "opus", "vorbis", "flac"):
                    acodec = cl

    return container, vcodec, acodec


def _validate_args(args: argparse.Namespace) -> None:
    """Validate parsed CLI arguments for logical consistency.

    Args:
        args: Namespace produced by :func:`argparse.ArgumentParser.parse_args`.

    Raises:
        SystemExit: Exits with code 1 on validation failure.
    """
    if args.info and args.audio:
        print(
            _err("Error: --info and --audio cannot be used together."),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.list_formats and (args.audio or args.info):
        print(
            _err("Error: --list-formats cannot be combined with --audio or --info."),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.quality and args.format:
        print(
            _err("Error: --quality/-q and --format/-f cannot be used together."),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.audio_format not in (None, "mp3", "m4a", "wav", "flac", "opus"):
        print(
            _err(f"Error: Invalid audio format '{args.audio_format}'. ")
            + _dim("Choose from: mp3, m4a, wav, flac, opus."),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.audio_quality not in (None, "best", "128k", "192k", "256k", "320k"):
        print(
            _err(f"Error: Invalid audio quality '{args.audio_quality}'. ")
            + _dim("Choose from: best, 128k, 192k, 256k, 320k."),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.quality not in (None, "best", "480p", "720p", "1080p", "4k"):
        print(
            _err(f"Error: Invalid quality '{args.quality}'. ")
            + _dim("Choose from: best, 480p, 720p, 1080p, 4k."),
            file=sys.stderr,
        )
        sys.exit(1)

    if args.subtitles and not args.subtitle_lang:
        args.subtitle_lang = "en"

    if args.output:
        output_path = Path(args.output)
        if output_path.exists() and not output_path.is_dir():
            print(
                _err(f"Error: Output path '{args.output}' exists and is not a directory."),
                file=sys.stderr,
            )
            sys.exit(1)


def _load_config_file(config_path: str | None) -> dict[str, Any]:
    """Load a YAML or JSON configuration file.

    Args:
        config_path: Filesystem path to the config file.  When *None* the
            function returns an empty dict without attempting to load.

    Returns:
        A dictionary of configuration values.  Returns an empty dict when
        *config_path* is *None* or the file cannot be read.
    """
    if not config_path:
        return {}

    path = Path(config_path)
    if not path.exists():
        print(
            _warn(f"Warning: Config file not found: {config_path}"),
            file=sys.stderr,
        )
        return {}

    suffix = path.suffix.lower()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(_warn(f"Warning: Cannot read config file: {exc}"), file=sys.stderr)
        return {}

    if suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(raw)
            return dict(data) if isinstance(data, dict) else {}
        except ImportError:
            print(
                _warn("Warning: pyyaml is not installed; cannot parse YAML config."),
                file=sys.stderr,
            )
            return {}
        except Exception as exc:
            print(_warn(f"Warning: Failed to parse YAML config: {exc}"), file=sys.stderr)
            return {}

    if suffix == ".json":
        try:
            import json
            data = json.loads(raw)
            return dict(data) if isinstance(data, dict) else {}
        except Exception as exc:
            print(_warn(f"Warning: Failed to parse JSON config: {exc}"), file=sys.stderr)
            return {}

    print(
        _warn(f"Warning: Unsupported config format '{suffix}'. Use .yaml, .yml, or .json."),
        file=sys.stderr,
    )
    return {}


def _setup_logger(args: argparse.Namespace) -> logging.Logger:
    """Configure and return the ytdownloader logger based on CLI flags.

    Args:
        args: Parsed CLI argument namespace.

    Returns:
        A :class:`logging.Logger` instance configured to the requested level.
    """
    if args.verbose:
        _configure_logging(level=logging.DEBUG, format_type="detailed")
        logger = get_logger("cli")
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose debug logging enabled.")
        return logger

    if args.quiet:
        _configure_logging(level=logging.ERROR, format_type="simple")
        return get_logger("cli")

    _configure_logging(level=logging.INFO, format_type="detailed")
    return get_logger("cli")


def _handle_info_mode(url: str, args: argparse.Namespace) -> int:
    """Handle the ``--info`` display mode.

    Args:
        url: The YouTube URL to query.
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 on success, 1 on failure).
    """
    logger = get_logger("cli.info")
    logger.info("Fetching video info for: %s", url)

    try:
        info = get_video_info(normalize_youtube_url(url))
        _print_video_info(info)
        return 0
    except InvalidURLError as exc:
        print(_err(f"Error: Invalid URL: {exc}"), file=sys.stderr)
        return 1
    except VideoUnavailableError as exc:
        print(_err(f"Error: Video unavailable: {exc}"), file=sys.stderr)
        return 1
    except AgeRestrictedError as exc:
        print(_err(f"Error: Age-restricted video: {exc}"), file=sys.stderr)
        return 1
    except GeoRestrictedError as exc:
        print(_err(f"Error: Geo-restricted video: {exc}"), file=sys.stderr)
        return 1
    except NetworkError as exc:
        print(_err(f"Error: Network error: {exc}"), file=sys.stderr)
        return 1
    except MetadataExtractionError as exc:
        print(_err(f"Error: Could not extract video info: {exc}"), file=sys.stderr)
        return 1
    except Exception as exc:
        print(_err(f"Error: Unexpected error fetching video info: {exc}"), file=sys.stderr)
        return 1


def _handle_list_formats(url: str, info: dict[str, Any]) -> int:
    """Handle the ``--list-formats`` display mode.

    Args:
        url: The YouTube URL (used for display only).
        info: Pre-fetched metadata dict (may be empty to trigger a fetch).

    Returns:
        Exit code (0 on success, 1 on failure).
    """
    logger = get_logger("cli.formats")
    logger.info("Listing formats for: %s", url)

    try:
        if not info:
            info = get_video_info(normalize_youtube_url(url))

        print()
        print(_heading(f"Video: {info.get('title', 'Unknown')}"))
        print(_heading(f"URL:   {url}"))
        print()

        _print_video_info(info)
        return 0

    except InvalidURLError as exc:
        print(_err(f"Error: Invalid URL: {exc}"), file=sys.stderr)
        return 1
    except VideoUnavailableError as exc:
        print(_err(f"Error: Video unavailable: {exc}"), file=sys.stderr)
        return 1
    except AgeRestrictedError as exc:
        print(_err(f"Error: Age-restricted video: {exc}"), file=sys.stderr)
        return 1
    except GeoRestrictedError as exc:
        print(_err(f"Error: Geo-restricted video: {exc}"), file=sys.stderr)
        return 1
    except NetworkError as exc:
        print(_err(f"Error: Network error: {exc}"), file=sys.stderr)
        return 1
    except MetadataExtractionError as exc:
        print(_err(f"Error: Could not list formats: {exc}"), file=sys.stderr)
        return 1
    except Exception as exc:
        print(_err(f"Error: Unexpected error listing formats: {exc}"), file=sys.stderr)
        return 1


def _compute_output_path(
    output_dir: str,
    info: dict[str, Any],
    audio_format: str | None,
    is_audio: bool,
) -> str:
    """Compute the final output file path for a download.

    Args:
        output_dir: Directory in which to save the file.
        info: Video metadata dict (used to derive the filename).
        audio_format: Desired audio extension (e.g. ``"mp3"``), or *None*.
        is_audio: ``True`` when downloading audio-only.

    Returns:
        Full absolute path to the output file.
    """
    title = info.get("title", "download") or "download"
    video_id = info.get("id", "") or "unknown"
    safe_title = "".join(c if c.isalnum() or c in " -_." else "_" for c in title).strip()
    safe_title = safe_title[:100] or "download"

    if is_audio:
        ext = audio_format or "mp3"
        filename = f"{safe_title} [{video_id}].{ext}"
    else:
        filename = f"{safe_title} [{video_id}].mp4"

    return str(Path(output_dir) / filename)


def _handle_download(url: str, args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Handle the ``--audio`` and default video download modes.

    Args:
        url: The YouTube URL to download.
        args: Parsed CLI arguments.
        config: Merged configuration dictionary.

    Returns:
        Exit code (0 on success, 1 on failure).
    """
    logger = get_logger("cli.download")
    logger.info("Starting download for: %s", url)

    quality = args.quality or config.get("default_quality") or "best"
    audio_only = args.audio
    quiet = args.quiet
    output_dir = args.output or config.get("output_dir") or "."
    audio_format = args.audio_format or config.get("audio_format") or "mp3"
    audio_quality = args.audio_quality or config.get("audio_quality") or "best"
    subtitle_lang = args.subtitle_lang
    no_playlist = args.no_playlist
    resume = args.resume
    proxy = args.proxy or config.get("proxy")
    cookies = args.cookies or config.get("cookies_file")
    format_itag = args.format

    os.makedirs(output_dir, exist_ok=True)

    try:
        info = get_video_info(normalize_youtube_url(url))
    except InvalidURLError as exc:
        print(_err(f"Error: Invalid URL: {exc}"), file=sys.stderr)
        return 1
    except VideoUnavailableError as exc:
        print(_err(f"Error: Video unavailable: {exc}"), file=sys.stderr)
        return 1
    except AgeRestrictedError as exc:
        print(_err(f"Error: Age-restricted video: {exc}"), file=sys.stderr)
        return 1
    except GeoRestrictedError as exc:
        print(_err(f"Error: Geo-restricted video: {exc}"), file=sys.stderr)
        return 1
    except NetworkError as exc:
        print(_err(f"Error: Network error: {exc}"), file=sys.stderr)
        return 1
    except MetadataExtractionError as exc:
        print(_err(f"Error: Could not fetch video info: {exc}"), file=sys.stderr)
        return 1

    if not quiet:
        print()
        print(_heading(f"Title:  {info.get('title', 'Unknown')}"))
        print(_heading(f"ID:     {info.get('id', 'Unknown')}"))
        print(_heading(f"Author: {info.get('author', 'Unknown')}"))
        duration = info.get("duration", "N/A")
        print(_heading(f"Length: {duration}"))
        print()

    output_path = _compute_output_path(output_dir, info, audio_format, audio_only)

    kwargs: dict[str, Any] = {
        "quiet": quiet,
        "resume": resume,
        "proxy": proxy,
        "cookies": cookies,
        "no_playlist": no_playlist,
        "audio_format": audio_format,
        "audio_quality": audio_quality,
        "subtitle_lang": subtitle_lang,
    }

    if format_itag:
        try:
            kwargs["format_itag"] = int(format_itag)
        except ValueError:
            print(_err(f"Error: Invalid itag number: {format_itag}"), file=sys.stderr)
            return 1

    if not quiet:
        if audio_only:
            print(f"{_info('Downloading audio')} from: {url}")
            print(f"{_dim('Format:')} {audio_format} | {_dim('Quality:')} {audio_quality}")
        else:
            q_label = quality if quality != "best" else "best available"
            print(f"{_info('Downloading video')} from: {url}")
            print(f"{_dim('Quality:')} {q_label}")
        print(f"{_dim('Output:')} {output_path}")
        if proxy:
            print(f"{_dim('Proxy:')} {proxy}")
        if cookies:
            print(f"{_dim('Cookies:')} {cookies}")
        if resume:
            print(_dim("Resume: enabled"))
        print()

    try:
        if audio_only:
            result_path = download_audio(url, output_path=output_path, **kwargs)
        else:
            result_path = download_video(
                url,
                output_path=output_path,
                quality=quality,
                **kwargs,
            )

        if not quiet:
            print()
            print(
                f"{_info('Download complete:')} {result_path} "
                f"({_format_size(os.path.getsize(result_path))})"
            )
        else:
            print(result_path)

        return 0

    except InvalidURLError as exc:
        print(_err(f"Error: Invalid URL: {exc}"), file=sys.stderr)
        return 1
    except VideoUnavailableError as exc:
        print(_err(f"Error: Video unavailable: {exc}"), file=sys.stderr)
        return 1
    except AgeRestrictedError as exc:
        print(_err(f"Error: Age-restricted video: {exc}"), file=sys.stderr)
        return 1
    except GeoRestrictedError as exc:
        print(_err(f"Error: Geo-restricted video: {exc}"), file=sys.stderr)
        return 1
    except NetworkError as exc:
        print(_err(f"Error: Network error: {exc}"), file=sys.stderr)
        return 1
    except FormatSelectionError as exc:
        print(_err(f"Error: No suitable format found: {exc}"), file=sys.stderr)
        return 1
    except DownloadError as exc:
        print(_err(f"Error: Download failed: {exc}"), file=sys.stderr)
        return 1
    except SubtitleError as exc:
        print(_warn(f"Warning: Subtitle error: {exc}"), file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(_err(f"Error: Configuration error: {exc}"), file=sys.stderr)
        return 1
    except ImportError as exc:
        print(
            _err(f"Error: Missing dependency: {exc}"),
            file=sys.stderr,
        )
        print(
            _dim("Hint: Install required packages with: pip install -r requirements.txt"),
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print()
        print(_warn("Download cancelled by user."), file=sys.stderr)
        return 130
    except Exception as exc:
        print(_err(f"Error: Unexpected error during download: {exc}"), file=sys.stderr)
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            logger.debug(traceback.format_exc())
        return 1


# ---------------------------------------------------------------------------
# Argument parser factory
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser with all CLI options.

    Returns:
        A fully configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="ytdownloader",
        description=(
            "Download videos and audio from YouTube by reverse-engineering "
            "YouTube's video delivery system."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ytdownloader --audio --quality 320k URL\n"
            "  ytdownloader --info URL\n"
            "  ytdownloader --list-formats URL\n"
            "  ytdownloader --quality 720p --output ./dl URL\n"
            "  ytdownloader --subtitles --subtitle-lang en URL\n"
            "  ytdownloader --resume --proxy http://proxy:8080 URL\n"
            "  ytdownloader --config myconfig.yaml URL\n"
        ),
    )

    # Positional argument
    parser.add_argument(
        "url",
        help=(
            "YouTube video URL.  Supported formats: watch, shorts, embed, "
            "youtu.be (e.g. https://www.youtube.com/watch?v=VIDEO_ID)"
        ),
    )

    # --- Core action flags ---
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--audio",
        action="store_true",
        default=False,
        help="Download audio only (converts to the format given by --audio-format).",
    )
    mode_group.add_argument(
        "--info",
        action="store_true",
        default=False,
        help="Print video metadata without downloading.",
    )

    parser.add_argument(
        "--list-formats",
        "-F",
        action="store_true",
        default=False,
        help="List all available stream formats and exit.",
    )

    # --- Output options ---
    parser.add_argument(
        "--output",
        "-o",
        default=".",
        help="Output directory for downloaded files (default: current directory).",
    )

    # --- Quality / format selection ---
    parser.add_argument(
        "--quality",
        "-q",
        default=None,
        choices=["best", "480p", "720p", "1080p", "4k"],
        help="Target video quality (default: best).",
    )
    parser.add_argument(
        "--format",
        "-f",
        default=None,
        help="Download a specific format by its YouTube itag number.",
    )

    # --- Audio options ---
    parser.add_argument(
        "--audio-format",
        "-af",
        default=None,
        choices=["mp3", "m4a", "wav", "flac", "opus"],
        help="Audio format for --audio downloads (default: mp3).",
    )
    parser.add_argument(
        "--audio-quality",
        "-aq",
        default=None,
        choices=["best", "128k", "192k", "256k", "320k"],
        help="Audio bitrate quality (default: best).",
    )

    # --- Subtitle options ---
    parser.add_argument(
        "--subtitles",
        "-s",
        action="store_true",
        default=False,
        help="Download subtitles/captions alongside the video.",
    )
    parser.add_argument(
        "--subtitle-lang",
        "-sl",
        default=None,
        help="Subtitle language code (ISO 639-1, e.g. 'en', 'es', 'fr'). "
             "Defaults to 'en' when --subtitles is given without a language.",
    )

    # --- Playlist option ---
    parser.add_argument(
        "--no-playlist",
        "-npl",
        action="store_true",
        default=False,
        help="Download only the specified video, not the entire playlist.",
    )

    # --- Download options ---
    parser.add_argument(
        "--resume",
        "-r",
        action="store_true",
        default=False,
        help="Resume a partially-downloaded file using HTTP Range requests.",
    )

    # --- Network options ---
    parser.add_argument(
        "--proxy",
        "-p",
        default=None,
        help="HTTP/HTTPS proxy URL (e.g. http://proxy.example.com:8080).",
    )
    parser.add_argument(
        "--cookies",
        "-c",
        default=None,
        help="Path to a Netscape-format cookies file for authentication.",
    )

    # --- Config option ---
    parser.add_argument(
        "--config",
        "-cfg",
        default=None,
        help="Path to a YAML or JSON configuration file.",
    )

    # --- Display / logging options ---
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress progress output; print only the output file path.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable verbose debug logging to stderr.",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: list[str] | None = None) -> int:
    """Main CLI entry point.

    Parses *args* (defaults to ``sys.argv[1:]`` when *None*), validates the
    resulting namespace, loads optional configuration, and dispatches to the
    appropriate handler.

    Args:
        args: Argument list to parse.  Pass ``None`` to use ``sys.argv[1:]``.

    Returns:
        Integer exit code: ``0`` on success, ``1`` on error, ``130`` on
        keyboard interrupt.
    """
    parser = _build_parser()
    parsed = parser.parse_args(args)

    url = parsed.url.strip()

    if not is_valid_youtube_url(url):
        print(
            _err(f"Error: Invalid YouTube URL: {url}"),
            file=sys.stderr,
        )
        print(file=sys.stderr)
        print(
            _heading("Supported URL formats:"),
            file=sys.stderr,
        )
        print(
            _dim("  - https://www.youtube.com/watch?v=VIDEO_ID"),
            file=sys.stderr,
        )
        print(
            _dim("  - https://youtu.be/VIDEO_ID"),
            file=sys.stderr,
        )
        print(
            _dim("  - https://www.youtube.com/shorts/VIDEO_ID"),
            file=sys.stderr,
        )
        print(
            _dim("  - https://www.youtube.com/embed/VIDEO_ID"),
            file=sys.stderr,
        )
        print(
            _dim("  - https://www.youtube.com/live/VIDEO_ID"),
            file=sys.stderr,
        )
        return 1

    _validate_args(parsed)

    _setup_logger(parsed)

    config: dict[str, Any] = {}
    if parsed.config:
        config = _load_config_file(parsed.config)

    if parsed.info:
        return _handle_info_mode(url, parsed)

    if parsed.list_formats:
        return _handle_list_formats(url, {})

    return _handle_download(url, parsed, config)


if __name__ == "__main__":
    sys.exit(main())
