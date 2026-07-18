"""
ytdownloader - A native YouTube video and audio downloader library.

This package provides a fully self-contained implementation for extracting and
downloading YouTube video and audio streams directly from YouTube's player
response data, with **no yt-dlp dependency**.  All stream resolution, format
selection, HTTP fetching, and file I/O are implemented natively within the
package's own modules.

Architecture overview::

    ytdownloader/
    ├── downloader.py       Core public API (download_video, download_audio, …)
    ├── video_info.py       VideoInfo dataclass – central metadata container
    ├── streaming_data.py   StreamFormat dataclass + format selection utilities
    ├── html_extractor.py   HTML parsing – extracts ytInitialPlayerResponse
    ├── player_response.py  Player response validation + field extraction
    ├── signature_cipher.py Signature / n-parameter cipher resolution
    ├── n_resolver.py       JavaScript n-parameter decoder
    ├── http_client.py      Thread-safe chunked HTTP download client
    ├── merger.py           ffmpeg-based audio+video stream merger
    ├── config.py           YTConfig dataclass + YAML/JSON config loading
    ├── logger.py           Coloured console + file logging utilities
    ├── constants.py        YouTube itags, MIME types, codecs, URL constants
    ├── subtitle_parser.py  Subtitle / caption track parsing and conversion
    ├── utils.py            URL validation, normalisation, and video-ID extraction
    ├── progress.py         Terminal progress bar for active downloads
    ├── cache.py            Disk + memory caching layer for video metadata
    ├── cli.py              ``python -m ytdownloader`` command-line interface
    └── exceptions.py       Full exception hierarchy (YTDLException base class)

Public API summary
==================

Core download functions
-----------------------
    download_video(url, ...)    Download the best video stream; automatically
                                merges separate audio+video tracks when needed.
    download_audio(url, ...)    Download audio-only stream (optionally convert
                                to MP3 via ffmpeg).
    get_video_info(url)         Fetch and parse video metadata; returns a
                                :class:`VideoInfo` instance.
    print_video_info(url)       Fetch metadata and print a human-readable
                                summary table to stdout.

Data models
-----------
    VideoInfo                   Structured dataclass for all YouTube video
                                metadata, stream formats, captions, and
                                playability status.
    StreamFormat                Structured dataclass for a single YouTube
                                stream format (itag, codecs, resolution, …).

Format selection
---------------
    select_format(streams, quality="best")   Pick the best format for a
                                             quality label.
    list_available_formats(streams)          Return a list of human-readable
                                             format description strings.

Configuration
-------------
    YTConfig                    Central configuration dataclass with all
                                package settings (timeout, chunk size, quality,
                                proxy, cookies, output directory, …).
    load_config(path=None)      Load configuration from a YAML or JSON file,
                                applying ``YT_*`` environment variable overrides.
    get_config()                Convenience wrapper returning the active config.
    get_default_config()        Return a :class:`YTConfig` with all defaults.

Logging
-------
    setup_logging(level="INFO") Configure package-level logging with optional
                                file output.
    get_logger(name)            Obtain a child logger for a module.

Utilities
---------
    is_valid_youtube_url(url)   Return ``True`` if *url* is a valid YouTube URL.
    normalize_youtube_url(url)  Convert short / embed / shorts URLs to the
                                canonical ``https://www.youtube.com/watch?v=…`` form.
    extract_video_id(url)       Extract the 11-character YouTube video ID.

Merging
-------
    merge_audio_video(video, audio, output)  Merge separate audio and video
                                             files into a single MP4 using
                                             ffmpeg.

Exceptions
----------
    YTDLException                Base class for all package exceptions.
    InvalidURLError              The supplied URL is not a valid YouTube URL.
    VideoUnavailableError        The video is private, removed, or deleted.
    AgeRestrictedError           Age-gate verification is required.
    GeoRestrictedError           The video is not available in this region.
    NetworkError                 A network-level communication error occurred.
    DownloadError                A stream download failed or was interrupted.
    FormatSelectionError         No suitable stream format could be found.
    MetadataExtractionError      Video metadata could not be extracted.
    StreamResolutionError        Stream URL resolution failed.
    SignatureCipherError         The signatureCipher parameter could not be
                                parsed or applied.
    NResolverError               The JavaScript n-parameter resolver failed.
    MergeError                   Audio+video stream merging failed.
    CacheError                   A cache read/write operation failed.
    ConfigError                  Configuration loading or validation failed.
    HtmlExtractionError          HTML data extraction failed.

Typical usage
=============

Quick start::

    import ytdownloader

    info = ytdownloader.get_video_info(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    print(info.title, info.duration_str)

    path = ytdownloader.download_video(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        quality="720p",
        output_path="./downloads",
    )

    path = ytdownloader.download_audio(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        audio_format="mp3",
    )

With custom configuration::

    import ytdownloader

    ytdownloader.setup_logging(level="DEBUG")

    config = ytdownloader.get_config()
    config.default_quality = "1080p"
    config.audio_format  = "m4a"
    config.proxy         = "http://proxy:8080"

    path = ytdownloader.download_video(url, quality=config.default_quality)
"""

from __future__ import annotations

import importlib.util
import logging
import warnings
from typing import Optional

# ---------------------------------------------------------------------------
# Core downloader API
# ---------------------------------------------------------------------------
# Provides download_video, download_audio, get_video_info, print_video_info,
# select_format, list_available_formats, and the downloader-local VideoInfo
# dataclass (see video_info.VideoInfo for the richer, slots-based version).
# ---------------------------------------------------------------------------

from .downloader import (
    download_audio,
    download_video,
    get_video_info,
    list_available_formats,
    print_video_info,
    select_format,
)

# ---------------------------------------------------------------------------
# Video metadata (rich VideoInfo dataclass – preferred over downloader.VideoInfo)
# ---------------------------------------------------------------------------

from .video_info import VideoInfo as VideoInfo
from .video_info import VideoInfoError

# ---------------------------------------------------------------------------
# Streaming data
# ---------------------------------------------------------------------------
# StreamFormat is the canonical representation of a single YouTube stream
# format; StreamDataError is raised on unrecoverable parsing errors.
# ---------------------------------------------------------------------------

from .streaming_data import (
    StreamDataError,
    StreamFormat,
)

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------
# All exceptions inherit from YTDLException so callers can catch the base
# class to handle any package-specific error.
# ---------------------------------------------------------------------------

from .exceptions import (
    AgeRestrictedError,
    CacheError,
    ConfigError,
    DownloadError,
    FormatSelectionError,
    GeoRestrictedError,
    HtmlExtractionError,
    InvalidURLError,
    MetadataExtractionError,
    MergeError,
    NResolverError,
    NetworkError,
    SignatureCipherError,
    StreamResolutionError,
    SubtitleError,
    VideoUnavailableError,
    YTDLException,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# YTConfig is a dataclass with sensible defaults for every package setting.
# load_config reads YAML or JSON files and applies YT_* env-var overrides.
# ---------------------------------------------------------------------------

from .config import (
    YTConfig,
    apply_env_overrides,
    get_default_config,
    load_config,
    save_config,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# get_logger returns a child logger bound to ytdownloader.<name>.
# setup_logging() (defined below) is the recommended entry point for users.
# ---------------------------------------------------------------------------

from .logger import YTLogger, get_logger

# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

from .utils import (
    extract_video_id,
    is_valid_youtube_url,
    normalize_youtube_url,
)

# ---------------------------------------------------------------------------
# Audio / video merger
# ---------------------------------------------------------------------------
# merge_audio_video combines separate video-only and audio-only streams into
# a single file using ffmpeg.
# ---------------------------------------------------------------------------

from .merger import merge_audio_video

# ---------------------------------------------------------------------------
# Subtitle / caption support
# ---------------------------------------------------------------------------
# SubtitleTrack represents a caption track; SubtitleCue is a single timed
# text entry within a track.
# ---------------------------------------------------------------------------

from .subtitle_parser import (
    SubtitleCue,
    SubtitleTrack,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Selected, commonly-used constants are re-exported at the package level.
# For the full set see ytdownloader.constants.
# ---------------------------------------------------------------------------

from .constants import (
    AUDIO_CODECS,
    AUDIO_ONLY_ITAGS,
    CONTAINERS,
    DEFAULT_ACCEPT_HEADER,
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_CAPTION_FORMAT,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_CONCURRENT_DOWNLOADS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    DEFAULT_RETRY_DELAY_BASE,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    DEFAULT_VIDEO_FORMAT,
    EXT_MIME_MAP,
    FORMAT_BEST_AUDIO,
    FORMAT_BEST_COMBINED,
    FORMAT_BEST_MP4,
    ITAG_DETAILS,
    ITAG_QUALITY,
    MAX_QUALITY,
    MIME_EXT_MAP,
    MIN_QUALITY,
    N_PARAM_NAME,
    PLAYABILITY_STATUSES,
    PREFERRED_AUDIO_CODECS,
    PREFERRED_VIDEO_CODECS,
    PROGRESSIVE_ITAGS,
    PROGRESSIVE_PROTOCOLS,
    PROTOCOLS,
    QUALITY_HEIGHT_MAP,
    QUALITY_ITAGS,
    RE_AGE_RESTRICTED,
    RE_GEO_RESTRICTED,
    RE_INITIAL_DATA,
    RE_PLAYER_RESPONSE,
    RE_STS,
    RE_VIDEO_ID,
    RE_YTCFG,
    SEGMENTED_PROTOCOLS,
    SIGNATURE_PARAM_NAMES,
    SIGNATURE_SP_NAMES,
    SUPPORTED_CONFIG_FORMATS,
    SUPPORTED_SUBTITLE_FORMATS,
    THUMBNAIL_SIZES,
    VIDEO_CODECS,
    VIDEO_ONLY_ITAGS,
    YOUTUBE_EMBED_URL_FORMAT,
    YOUTUBE_PAGE_HEADERS,
    YOUTUBE_SHORTS_URL_FORMAT,
    YOUTUBE_VIDEO_ID_PATTERN,
    YOUTUBE_WATCH_URL_FORMAT,
)

# Env-var name constants – sourced from config.ENV_VAR_MAP keys
from .config import ENV_VAR_MAP as _ENV_VAR_MAP  # noqa: E402

ENV_VAR_YT_PROXY: str = _ENV_VAR_MAP.get("YT_PROXY", "YT_PROXY")
ENV_VAR_YT_LOG_LEVEL: str = _ENV_VAR_MAP.get("YT_LOG_LEVEL", "YT_LOG_LEVEL")
ENV_VAR_YT_LOG_FILE: str = _ENV_VAR_MAP.get("YT_LOG_FILE", "YT_LOG_FILE")
ENV_VAR_YT_OUTPUT_DIR: str = _ENV_VAR_MAP.get("YT_OUTPUT_DIR", "YT_OUTPUT_DIR")
ENV_VAR_YT_TIMEOUT: str = _ENV_VAR_MAP.get("YT_TIMEOUT", "YT_TIMEOUT")
ENV_VAR_YT_MAX_RETRIES: str = _ENV_VAR_MAP.get("YT_MAX_RETRIES", "YT_MAX_RETRIES")
ENV_VAR_YT_CHUNK_SIZE: str = _ENV_VAR_MAP.get("YT_CHUNK_SIZE", "YT_CHUNK_SIZE")
ENV_VAR_YT_MAX_CONCURRENT_DOWNLOADS: str = _ENV_VAR_MAP.get(
    "YT_MAX_CONCURRENT_DOWNLOADS", "YT_MAX_CONCURRENT_DOWNLOADS"
)
ENV_VAR_YT_USER_AGENT: str = _ENV_VAR_MAP.get("YT_USER_AGENT", "YT_USER_AGENT")
ENV_VAR_YT_COOKIES_FILE: str = _ENV_VAR_MAP.get("YT_COOKIES_FILE", "YT_COOKIES_FILE")
ENV_VAR_YT_AUDIO_FORMAT: str = _ENV_VAR_MAP.get("YT_AUDIO_FORMAT", "YT_AUDIO_FORMAT")
ENV_VAR_YT_VIDEO_FORMAT: str = _ENV_VAR_MAP.get("YT_VIDEO_FORMAT", "YT_VIDEO_FORMAT")
ENV_VAR_YT_DEFAULT_QUALITY: str = _ENV_VAR_MAP.get(
    "YT_DEFAULT_QUALITY", "YT_DEFAULT_QUALITY"
)

# Cache constants
from .cache import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL,
    MAX_CACHE_TTL,
    MIN_CACHE_TTL,
)

# Merger / ffmpeg constants
from .merger import (  # noqa: E402
    COMPATIBLE_CONTAINER_CODECS,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_OUTPUT_CONTAINER,
    DEFAULT_VIDEO_CODEC,
    SUPPORTED_CONTAINERS,
)

# Re-export SUPPORTED_CONFIG_FORMATS from config
from .config import SUPPORTED_CONFIG_FORMATS as SUPPORTED_CONFIG_FORMATS  # noqa: E402

# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

__version__: str = "2.0.0"
__author__: str = "ytdownloader contributors"
__license__: str = "MIT"
__description__: str = (
    "A native YouTube video downloader library with no yt-dlp dependency."
)

# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """Configure logging for the ytdownloader package.

    Sets the log level for the ``ytdownloader`` logger hierarchy, attaches a
    console ``StreamHandler`` with a concise formatter, and optionally adds a
    file handler when *log_file* is provided.

    This function should be called before starting downloads so that all
    modules pick up the configured level.

    Args:
        level: Minimum log level as a string.  Accepted values (case-
            insensitive) are ``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
            ``"ERROR"``, and ``"CRITICAL"``.  Defaults to ``"INFO"``.
        log_file: Optional path to a file that should receive all log output.
            Pass ``None`` (the default) to disable file logging.

    Raises:
        ValueError: If *level* is not one of the recognised level strings.

    Example::

        import ytdownloader

        ytdownloader.setup_logging(level="DEBUG", log_file="ytdl.log")

        info = ytdownloader.get_video_info(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
    """
    level_upper = level.strip().upper()
    _VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
    if level_upper not in _VALID_LEVELS:
        raise ValueError(
            f"Invalid log level {level!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_LEVELS))}."
        )
    numeric_level = getattr(logging, level_upper, logging.INFO)

    root_logger = logging.getLogger("ytdownloader")
    root_logger.setLevel(numeric_level)

    if not root_logger.handlers:
        _console_handler = logging.StreamHandler()
        _console_handler.setLevel(numeric_level)
        _console_handler.setFormatter(
            logging.Formatter(
                "%(levelname)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root_logger.addHandler(_console_handler)

    if log_file:
        _file_handler = logging.FileHandler(log_file, encoding="utf-8")
        _file_handler.setLevel(numeric_level)
        _file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(_file_handler)

    root_logger.debug("Logging configured: level=%s, log_file=%s", level_upper, log_file)


def get_config(config_path: Optional[str] = None) -> YTConfig:
    """Return the active ytdownloader configuration.

    Loads the configuration from the default search locations when
    *config_path* is ``None``:

    1. The path in the ``YT_CONFIG`` environment variable (if set).
    2. ``./ytdownloader.yaml``
    3. ``./ytdownloader.yml``
    4. ``./ytdownloader.json``

    When *config_path* is provided it is loaded directly.  Environment
    variable overrides (``YT_*``) always take precedence over file values.

    Args:
        config_path: Optional explicit path to a YAML or JSON config file.
            Pass ``None`` to use the default search order described above.

    Returns:
        A fully-populated and validated :class:`YTConfig` instance.

    Example::

        import ytdownloader

        config = ytdownloader.get_config()
        config.default_quality = "720p"
        config.audio_format    = "m4a"

        path = ytdownloader.download_video(url, quality=config.default_quality)
    """
    return load_config(config_path)


# ---------------------------------------------------------------------------
# Package-level __all__
# ---------------------------------------------------------------------------
# Defines the complete public API.  Importing with ``from ytdownloader import *``
# will import exactly the names listed here.
# ---------------------------------------------------------------------------

__all__ = [
    # Version and package metadata
    "__version__",
    "__author__",
    "__license__",
    "__description__",
    # Core download API
    "download_video",
    "download_audio",
    "get_video_info",
    "print_video_info",
    "select_format",
    "list_available_formats",
    # Data models
    "VideoInfo",
    "VideoInfoError",
    "StreamFormat",
    "StreamDataError",
    # Configuration
    "YTConfig",
    "load_config",
    "get_config",
    "get_default_config",
    "save_config",
    "apply_env_overrides",
    # Logging
    "setup_logging",
    "get_logger",
    "YTLogger",
    # URL utilities
    "is_valid_youtube_url",
    "normalize_youtube_url",
    "extract_video_id",
    # Audio / video merger
    "merge_audio_video",
    # Subtitle / caption
    "SubtitleTrack",
    "SubtitleCue",
    # Exceptions
    "YTDLException",
    "InvalidURLError",
    "VideoUnavailableError",
    "AgeRestrictedError",
    "GeoRestrictedError",
    "NetworkError",
    "DownloadError",
    "FormatSelectionError",
    "SignatureCipherError",
    "NResolverError",
    "MetadataExtractionError",
    "StreamResolutionError",
    "SubtitleError",
    "MergeError",
    "CacheError",
    "ConfigError",
    "HtmlExtractionError",
    # Constants – format and quality
    "ITAG_QUALITY",
    "QUALITY_ITAGS",
    "ITAG_DETAILS",
    "MIME_EXT_MAP",
    "EXT_MIME_MAP",
    # Constants – protocols and codecs
    "PROTOCOLS",
    "PROGRESSIVE_PROTOCOLS",
    "SEGMENTED_PROTOCOLS",
    "VIDEO_CODECS",
    "AUDIO_CODECS",
    "CONTAINERS",
    # Constants – quality
    "QUALITY_HEIGHT_MAP",
    "MIN_QUALITY",
    "MAX_QUALITY",
    # Constants – itag groups
    "PROGRESSIVE_ITAGS",
    "VIDEO_ONLY_ITAGS",
    "AUDIO_ONLY_ITAGS",
    # Constants – format strings
    "FORMAT_BEST_COMBINED",
    "FORMAT_BEST_AUDIO",
    "FORMAT_BEST_MP4",
    "OUTPUT_TEMPLATE",
    # Constants – network
    "DEFAULT_USER_AGENT",
    "DEFAULT_ACCEPT_HEADER",
    "DEFAULT_ACCEPT_LANGUAGE",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAY_BASE",
    "DEFAULT_CHUNK_SIZE",
    # Constants – download defaults
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_AUDIO_FORMAT",
    "DEFAULT_VIDEO_FORMAT",
    "DEFAULT_QUALITY",
    "DEFAULT_MAX_CONCURRENT_DOWNLOADS",
    # Constants – logging
    "DEFAULT_LOG_LEVEL",
    # Constants – environment variable names
    "ENV_VAR_YT_PROXY",
    "ENV_VAR_YT_LOG_LEVEL",
    "ENV_VAR_YT_LOG_FILE",
    "ENV_VAR_YT_OUTPUT_DIR",
    "ENV_VAR_YT_TIMEOUT",
    "ENV_VAR_YT_MAX_RETRIES",
    "ENV_VAR_YT_CHUNK_SIZE",
    "ENV_VAR_YT_MAX_CONCURRENT_DOWNLOADS",
    "ENV_VAR_YT_USER_AGENT",
    "ENV_VAR_YT_COOKIES_FILE",
    "ENV_VAR_YT_AUDIO_FORMAT",
    "ENV_VAR_YT_VIDEO_FORMAT",
    "ENV_VAR_YT_DEFAULT_QUALITY",
    # Constants – URLs and endpoints
    "YOUTUBE_WATCH_URL_FORMAT",
    "YOUTUBE_EMBED_URL_FORMAT",
    "YOUTUBE_SHORTS_URL_FORMAT",
    "YOUTUBE_VIDEO_ID_PATTERN",
    "YOUTUBE_PAGE_HEADERS",
    # Constants – regex patterns
    "RE_VIDEO_ID",
    "RE_PLAYER_RESPONSE",
    "RE_YTCFG",
    "RE_STS",
    "RE_INITIAL_DATA",
    "RE_AGE_RESTRICTED",
    "RE_GEO_RESTRICTED",
    # Constants – subtitles
    "DEFAULT_CAPTION_FORMAT",
    "SUPPORTED_SUBTITLE_FORMATS",
    # Constants – thumbnails
    "THUMBNAIL_SIZES",
    # Constants – playability
    "PLAYABILITY_STATUSES",
    # Constants – cache
    "DEFAULT_CACHE_DIR",
    "DEFAULT_CACHE_TTL",
    "MAX_CACHE_TTL",
    "MIN_CACHE_TTL",
    # Constants – cipher
    "SIGNATURE_PARAM_NAMES",
    "SIGNATURE_SP_NAMES",
    "N_PARAM_NAME",
    # Constants – config
    "SUPPORTED_CONFIG_FORMATS",
    "ENV_VAR_MAP",
]
