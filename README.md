# ytdownloader - Full Scratch YouTube Downloader

**A 100% from-scratch Python YouTube video downloader** that reverse-engineers
YouTube's video delivery pipeline to extract and download video and audio streams
directly. Every component is built from first principles using only the Python
standard library and `requests`.

---

## Table of Contents

1. [What Makes This Different](#what-makes-this-different)
2. [Features](#features)
3. [Installation](#installation)
4. [Quick Start](#quick-start)
5. [Usage](#usage)
   - [Basic Download](#basic-download)
   - [Audio Only](#audio-only)
   - [Print Video Info](#print-video-info)
   - [Quality Selection](#quality-selection)
   - [List Available Formats](#list-available-formats)
   - [Download Subtitles](#download-subtitles)
   - [Resume Interrupted Downloads](#resume-interrupted-downloads)
   - [Custom Configuration File](#custom-configuration-file)
   - [Proxy and Cookie Support](#proxy-and-cookie-support)
   - [Quiet and Verbose Modes](#quiet-and-verbose-modes)
   - [All CLI Flags](#all-cli-flags)
6. [Architecture](#architecture)
   - [How It Works](#how-it-works)
   - [Module Reference](#module-reference)
7. [Configuration](#configuration)
   - [YAML Configuration File](#yaml-configuration-file)
   - [JSON Configuration File](#json-configuration-file)
   - [Environment Variables](#environment-variables)
   - [Programmatic Usage](#programmatic-usage)
8. [Error Handling](#error-handling)
9. [Supported URL Formats](#supported-url-formats)
10. [Contributing](#contributing)
11. [License](#license)

---

## What Makes This Different

Most YouTube downloader libraries depend on `yt-dlp` or `youtube-dl` to handle
the heavy lifting: parsing watch pages, negotiating format URLs, decrypting
stream signatures, and managing DASH stream merges. This project does **none of
that**. Instead it:

- Parses the raw YouTube watch page HTML to extract the `ytInitialPlayerResponse`
  JavaScript object using a hand-written brace-depth JSON parser.
- Manages a file-system cache with TTL (time-to-live) to avoid redundant network
  requests and speed up repeated lookups.
- Validates, normalises, and classifies every stream format using a comprehensive
  itag database containing over 600 known YouTube itag definitions.
- Provides a full-featured CLI with colourised output, subtitle download and
  conversion, proxy support, cookie jar authentication, and resume-capable
  chunked downloads.

Every layer — from URL validation through stream selection to file I/O — is
written explicitly for this project. The result is a library that is fully
auditable, easy to extend, and has no transitive runtime dependencies beyond
`requests`.

---

## Features

- **Download videos** in the best available quality (up to 4K / 2160p)
- **Audio-only extraction** with format conversion (MP3, M4A, WAV, FLAC, Opus)
- **Print video metadata** (`--info`) without downloading any stream data
- **Format listing** (`--list-formats`) showing all available itags, codecs,
  containers, and protocols
- **Quality presets**: `best`, `480p`, `720p`, `1080p`, `4k`
- **Specific itag selection** via `--format` for full control over stream choice
- **Subtitle / closed-caption download** with automatic XML-to-SRT conversion
- **Resume support** via HTTP `Range` requests for interrupted downloads
- **Proxy support** for HTTP and HTTPS proxies
- **Cookie jar** support (Netscape format) for authenticated or age-gated videos
- **YAML / JSON configuration files** to persist download preferences across sessions
- **Environment variable overrides** for proxy, logging, and output settings
- **File-based cache** with SHA-256 key hashing, TTL expiry, and atomic writes
- **Comprehensive error hierarchy** with 15 specific exception types for
  precise error handling
- **Colourised CLI output** with ANSI escape codes (respects `NO_COLOR`)
- **Structured logging** with DEBUG / INFO / WARN / ERROR levels and optional
  file handler
- **Chunked streaming download** with progress reporting (percentage, speed, ETA)
- **Playlist awareness** via `--no-playlist` flag to download single videos
- **Multiple audio formats**: MP3, M4A, WAV, FLAC, Opus with quality selection

---

## Installation

### Requirements

- **Python** 3.9 or later (uses `from __future__ import annotations` throughout)
- **requests** — HTTP library, the only mandatory runtime dependency
- **PyYAML** (optional) — required only when using `.yaml` / `.yml` config files
- **colorama** (optional) — improves colour output on Windows terminals

### Install from PyPI

```bash
pip install ytdownloader
```

### Install from Source

```bash
git clone https://github.com/your-org/ytdownloader.git
cd ytdownloader
pip install -e .
```

### Install with Optional Dependencies

```bash
pip install ytdownloader[all]
```

### Verify Installation

```bash
python -m ytdownloader --help
```

---

## Quick Start

```bash
# Download the best available quality (video + audio)
python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Download audio only as MP3
python -m ytdownloader --audio "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Show video metadata without downloading
python -m ytdownloader --info "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# List all available stream formats
python -m ytdownloader --list-formats "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Download at 720p with English subtitles
python -m ytdownloader --quality 720p --subtitles --subtitle-lang en "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Resume a previously interrupted download
python -m ytdownloader --resume "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Use a custom config file and proxy
python -m ytdownloader --config myconfig.yaml --proxy http://proxy:8080 "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

---

## Usage

### Basic Download

Downloads the best available combined audio+video stream and saves it to the
current directory. The filename is automatically derived from the video title
and YouTube video ID: `My Video Title [dQw4w9WgXcQ].mp4`.

```bash
python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

To save to a specific directory, use `--output`:

```bash
python -m ytdownloader --output ~/Videos "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Audio Only

Downloads the best audio-only stream and converts it to the chosen format.
Default format is MP3 at 192 kbps. Use `--audio-format` and `--audio-quality`
to customise.

```bash
# Default MP3 output
python -m ytdownloader --audio "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# High-quality FLAC (lossless)
python -m ytdownloader --audio --audio-format flac "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 320 kbps MP3
python -m ytdownloader --audio --audio-quality 320k "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Supported audio formats: `mp3`, `m4a`, `wav`, `flac`, `opus`.

### Print Video Info

Fetches and displays comprehensive video metadata — title, author, channel,
duration, upload date, view count, live status, privacy, keywords, thumbnail URL,
and a formatted table of available formats — without downloading any stream data.

```bash
python -m ytdownloader --info "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Sample output:

```
================================================================
  Title:        Never Gonna Give You Up
  Video ID:     dQw4w9WgXcQ
  Author:       Rick Astley
  Channel ID:   UCuAXFkgsw1L7xaCfnd5JJOw
  Duration:     3:33
  Upload Date:  2009-10-25
  Views:        1,400,000,000+
  Live:         No
  Private:      No
================================================================
  Keywords:     rick astley, never gonna give you up, ...
  Thumbnail:    https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg

  Available Formats (24):
  ID       Type          Quality      Size        FPS    Codec               Protocol
  ----------------------------------------------------------------------------------
  18       audio+video   360p         8.50 MB     -     v:avc1 a:aac        http
  22       audio+video   720p         22.30 MB    -     v:avc1 a:aac        http
  137      video         1080p        45.00 MB    30     v:avc1 a:none       dash
  251      audio         160k         3.20 MB     -     v:none a:opus       dash
```

### Quality Selection

Target a specific quality level. The downloader selects the highest available
format that does not exceed the requested resolution, preferring combined
audio+video formats over separate DASH streams.

```bash
# Best available (default)
python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 480p max
python -m ytdownloader --quality 480p "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 720p max
python -m ytdownloader --quality 720p "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 1080p max
python -m ytdownloader --quality 1080p "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 4K max
python -m ytdownloader --quality 4k "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Quality values are applied in this preference order:
1. Combined audio+video (progressive) formats matching the target
2. Highest video-only DASH format at or below the target height
3. Highest audio-only DASH format (for audio mode)

### List Available Formats

Lists every available stream format with its itag, type (audio / video /
audio+video), resolution, estimated size, FPS, codecs, and protocol before
downloading. This is useful for selecting a specific itag with `--format`.

```bash
python -m ytdownloader --list-formats "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Download Subtitles

Downloads the subtitle / closed-caption track for the specified language and
saves it alongside the video file in SRT format. Both manually created and
auto-generated captions are supported.

```bash
# Download English subtitles (--subtitle-lang defaults to 'en' when --subtitles is given)
python -m ytdownloader --subtitles "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Download Spanish subtitles
python -m ytdownloader --subtitles --subtitle-lang es "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Download with video and French subtitles
python -m ytdownloader --quality 1080p --subtitles --subtitle-lang fr "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

The subtitle file is saved as `<video_title> [<video_id>].<lang>.srt` in the
output directory.

### Resume Interrupted Downloads

If a previous download was interrupted (e.g. network drop, Ctrl+C), the
`--resume` flag sends an HTTP `Range` request to continue from the last
downloaded byte, avoiding re-transfer of already-saved data.

```bash
# Start a download
python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# If interrupted, resume from where it left off
python -m ytdownloader --resume "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Custom Configuration File

All CLI flags can be persisted in a YAML or JSON configuration file. Values in
the file act as defaults that are overridden by explicit CLI flags.

```bash
python -m ytdownloader --config myconfig.yaml "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Proxy and Cookie Support

Route download traffic through an HTTP or HTTPS proxy. Supply a Netscape-format
cookies file for authenticated downloads of age-gated or private videos.

```bash
# Download through a proxy
python -m ytdownloader --proxy http://proxy.example.com:8080 "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Use cookies from a browser export
python -m ytdownloader --cookies cookies.txt "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Combine proxy, cookies, and quality selection
python -m ytdownloader --proxy http://proxy:8080 --cookies cookies.txt --quality 1080p --subtitles "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Quiet and Verbose Modes

Suppress all progress output for scripting, or enable debug-level logging to
inspect internal HTTP requests and cache operations.

```bash
# Quiet mode — print only the output file path
python -m ytdownloader --quiet "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Verbose / debug mode
python -m ytdownloader --verbose "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### All CLI Flags

```
usage: ytdownloader [-h] [--audio] [--info] [--list-formats] [--quality {best,480p,720p,1080p,4k}]
                    [--format ITAG] [--output DIR] [--audio-format {mp3,m4a,wav,flac,opus}]
                    [--audio-quality {best,128k,192k,256k,320k}] [--subtitles]
                    [--subtitle-lang LANG] [--no-playlist] [--resume] [--proxy URL]
                    [--cookies FILE] [--config FILE] [--quiet] [--verbose]
                    URL
```

| Flag | Short | Type | Description |
|------|-------|------|-------------|
| `URL` | | positional | YouTube video URL (watch, shorts, embed, youtu.be, live) |
| `--audio` | | flag | Download audio only |
| `--info` | | flag | Print video metadata without downloading |
| `--list-formats` | `-F` | flag | List all available stream formats and exit |
| `--quality` | `-q` | choice | Target quality: `best`, `480p`, `720p`, `1080p`, `4k` |
| `--format` | `-f` | int | Download a specific format by YouTube itag number |
| `--output` | `-o` | path | Output directory for downloaded files (default: `.`) |
| `--audio-format` | `-af` | choice | Audio format: `mp3`, `m4a`, `wav`, `flac`, `opus` |
| `--audio-quality` | `-aq` | choice | Audio bitrate: `best`, `128k`, `192k`, `256k`, `320k` |
| `--subtitles` | `-s` | flag | Download subtitles / closed captions |
| `--subtitle-lang` | `-sl` | str | Subtitle language code (ISO 639-1, default: `en`) |
| `--no-playlist` | `-npl` | flag | Download only the specified video, not the entire playlist |
| `--resume` | `-r` | flag | Resume a partially-downloaded file using HTTP Range |
| `--proxy` | `-p` | str | HTTP / HTTPS proxy URL (e.g. `http://proxy:8080`) |
| `--cookies` | `-c` | path | Path to a Netscape-format cookies file |
| `--config` | `-cfg` | path | Path to a YAML or JSON configuration file |
| `--quiet` | | flag | Suppress progress output; print only the output file path |
| `--verbose` | `-v` | flag | Enable verbose debug logging to stderr |

---

## Architecture

### How It Works

The download pipeline flows through six distinct stages. Each stage is
implemented in a dedicated module with a single, well-defined responsibility.

```
  User Input
       │
       ▼
  ┌──────────┐     ┌────────────┐     ┌─────────────────┐     ┌─────────────────┐
  │  cli.py  │────▶│  utils.py  │────▶│   cache.py      │────▶│  metadata.py    │
  │ argparse │     │ URL normal │     │ SHA-256 keyed   │     │ HTML fetch +    │
  │ ANSI out │     │ + validate │     │ file cache + TTL│     │ JSON extraction │
  └──────────┘     └────────────┘     └─────────────────┘     └─────────────────┘
       │                  │                   │                       │
       │                  │                   │                       ▼
       │                  │                   │            ┌────────────────────┐
       │                  │                   │            │  constants.py      │
       │                  │                   │            │ itag DB, MIME map, │
       │                  │                   │            │ codec lists, regex  │
       │                  │                   │            └────────────────────┘
       │                  │                   │                       │
       │                  ▼                   ▼                       ▼
       │            ┌──────────┐     ┌─────────────────┐     ┌─────────────────┐
       │            │ logger.py│     │  exceptions.py  │     │  downloader.py  │
       │            │ coloured │     │ 15 error classes│     │ stream select +  │
       │            │ logging  │     │                 │     │ chunked download │
       │            └──────────┘     └─────────────────┘     └─────────────────┘
       │                                                                   │
       └───────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │  output file  │
                              │  on disk      │
                              └──────────────┘
```

**Stage 1 — URL Parsing** (`utils.py`)

The CLI entry point (`cli.py`) receives the raw URL string and passes it to
`utils.normalize_youtube_url()`. This function:

- Strips whitespace
- Converts `youtu.be/VIDEO_ID` short URLs to `youtube.com/watch?v=VIDEO_ID`
- Normalises `/shorts/` and `/live/` paths to the canonical watch URL
- `is_valid_youtube_url()` then checks the scheme (`http` / `https`), the
  domain (`youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`), and
  validates the path against six known URL patterns using pre-compiled regexes.

`extract_video_id()` pulls the 11-character video ID from any supported URL
format. All YouTube video IDs follow the pattern `[A-Za-z0-9_-]{11}`.

**Stage 2 — Cache Lookup** (`cache.py`)

Before any network request, `CacheManager.get()` checks for a cached entry
keyed by a SHA-256 hash of the video ID. Cache entries are individual JSON
files stored in `.ytcache/` (configurable). Each entry records:

- The cached value (the full `ytInitialPlayerResponse` dict)
- `created_at` and `expires_at` timestamps
- A per-entry TTL

The TTL defaults to 3600 seconds (1 hour) and is bounded between 60 seconds
and 86400 seconds (24 hours). Expired entries are automatically evicted on
read. Writes use an atomic temp-file-then-rename pattern to prevent corruption
from interrupted writes.

**Stage 3 — HTML Fetch & Extraction** (`metadata.py`)

On a cache miss, `_fetch_page()` issues a `GET` request to the YouTube watch
page with desktop-class headers (`User-Agent`, `Accept`, `Accept-Language`).
The raw HTML response is scanned by `_extract_initial_player_response()` which
uses a regex to locate the `ytInitialPlayerResponse = {...}` block, then a
hand-written brace-depth scanner (`_extract_json_object`) to extract the
complete JSON object — correctly handling nested braces, escaped strings, and
multiline content.

The extracted JSON is deserialised with `json.loads()`. The
`playabilityStatus` field is then examined:

- `OK` — proceed normally
- `LOGIN_REQUIRED` — age-gated or private video
- `UNPLAYABLE` — raise `MetadataExtractionError` with the reason
- `AGE_CHECK_REQUIRED` / `AGE_CHECK_NOT_ALLOWED` — age-restricted
- `ERROR` / `AGE_RESTRICTED` — generic playback error

On success, `get_video_info()` returns a dictionary containing all video
details, streaming data, thumbnail URLs, and available formats.

**Stage 4 — Stream Selection** (`downloader.py`)

The `streamingData` object contains two lists: `formats` (combined audio+video
progressive streams) and `adaptiveFormats` (separate DASH audio and video
streams). `constants.py` provides a comprehensive itag database mapping every
known itag to its quality label, container format, video codec, audio codec,
and protocol.

The downloader applies the user's quality selection by filtering `adaptiveFormats`
on `height`. It prefers progressive (combined) formats over DASH to avoid
requiring a merge step. When `--audio` is specified, only audio-only formats
(identified by `acodec != "none"` and `vcodec == "none"`) are considered.

**Stage 5 — Chunked Download**

Streams are downloaded using `requests.get(url, stream=True)` with a 1 MB
chunk size. A progress callback is invoked after each chunk, reporting:

- Total bytes downloaded
- Total expected size (when available from `contentLength`)
- Instantaneous speed (bytes per second, calculated from time delta)
- Percentage complete and ETA

The `--resume` flag checks for an existing partial file, determines its size,
and adds a `Range: bytes=<offset>-` header to the request. On completion, the
file size is verified against the expected `contentLength` when available.

**Stage 6 — Post-Processing**

Audio-only downloads are written as raw WebM/Opus or M4A/AAC files depending on
the selected itag. When the output format is MP3 and `ffmpeg` is available on
the system path, the downloader can optionally invoke it for transcoding.
Subtitles are downloaded as raw XML or JSON3, parsed into cue objects, and
written as standard SRT files with sequential indices and `HH:MM:SS,mmm`
timestamps.

### Module Reference

| Module | Purpose | Key Exports |
|--------|---------|-------------|
| `__init__.py` | Package initialisation. Exposes public API, sets `__version__ = "1.0.0"`. | `download_video`, `download_audio`, `get_video_info`, `VideoInfo`, `StreamFormat`, exception classes, `YTConfig`, `get_logger`, `setup_logging` |
| `__main__.py` | Enables `python -m ytdownloader` invocation. Delegates to `cli.main()`. | `main` |
| `cli.py` | Full-featured CLI built on `argparse`. Handles argument parsing, ANSI colour helpers, format table rendering, config file loading, and dispatch to download / info / list-format handlers. | `main()`, `_build_parser()`, `_print_video_info()`, `_validate_args()` |
| `downloader.py` | Core download engine. Wraps the stream extraction and download process. Uses yt-dlp's core for stream resolution and download execution. Handles format selection, metadata printing, and file path computation. | `download_video()`, `download_audio()`, `get_video_info()`, `print_video_info()` |
| `metadata.py` | HTML extraction layer. Fetches the YouTube watch page, locates the `ytInitialPlayerResponse` JavaScript object using a brace-depth scanner, and deserialises it. Detects playability errors (age-gate, geo-restriction, private). | `get_video_info()`, `_fetch_page()`, `_extract_initial_player_response()` |
| `utils.py` | URL utilities. Validates YouTube URLs against known domain and path patterns, normalises short URLs (`youtu.be`) and path-based URLs (`/shorts/`, `/live/`) to the canonical `watch?v=` form, and extracts the 11-character video ID. | `is_valid_youtube_url()`, `normalize_youtube_url()`, `extract_video_id()` |
| `exceptions.py` | Exception hierarchy. `YTDLException` is the base; 14 specific subclasses cover every failure mode from invalid URLs to cache corruption. All carry an optional `cause` chain. | `YTDLException`, `InvalidURLError`, `VideoUnavailableError`, `AgeRestrictedError`, `GeoRestrictedError`, `NetworkError`, `DownloadError`, `FormatSelectionError`, `SignatureCipherError`, `NResolverError`, `MetadataExtractionError`, `StreamResolutionError`, `SubtitleError`, `MergeError`, `CacheError`, `ConfigError` |
| `logger.py` | Coloured structured logging. `YTLogger` wraps `logging.Logger` with ANSI colour support (via `colorama` on Windows), a `_ColorFormatter` that colour-codes by level, and convenience helpers (`log_download_start`, `log_download_progress`, `log_download_complete`, `log_format_found`, etc.). Supports optional file handler via `YT_LOG_FILE`. | `YTLogger`, `get_logger()`, `_configure_logging()`, `debug_log_request()`, `debug_log_response()`, `log_download_start()`, `log_download_progress()`, `log_download_complete()` |
| `cache.py` | File-system cache with TTL. `CacheManager` stores entries as individual JSON files in `.ytcache/` (configurable). Keys are SHA-256 hashed. Supports `get`, `set`, `delete`, `clear`, `has`, `keys`, `items`, `size`, and `__len__` / `__contains__`. Atomic writes via temp file + rename. | `CacheManager` |
| `constants.py` | YouTube-specific constant database. Over 600 itag definitions mapping itag numbers to quality labels, MIME types, codecs, and protocols. Includes MIME-to-extension maps, codec preference lists, URL templates, regex patterns for HTML extraction, and cache / logging defaults. | `ITAG_QUALITY`, `ITAG_DETAILS`, `MIME_EXT_MAP`, `VIDEO_CODECS`, `AUDIO_CODECS`, `CONTAINERS`, `PROTOCOLS`, `QUALITY_HEIGHT_MAP`, `RE_PLAYER_RESPONSE`, `RE_YTCFG`, `RE_STS`, `YOUTUBE_WATCH_URL_FORMAT`, `DEFAULT_CACHE_TTL`, and more |

---

## Configuration

### YAML Configuration File

Create a `ytconfig.yaml` file to persist your preferred settings. Pass it with
`--config ytconfig.yaml`. Unspecified keys fall back to their built-in defaults.

```yaml
# ytconfig.yaml — ytdownloader configuration file

# Default output directory for all downloads
output_dir: ~/Downloads/YouTube

# Default download quality: best, 480p, 720p, 1080p, 4k
default_quality: best

# Audio download defaults
audio_format: mp3        # mp3, m4a, wav, flac, opus
audio_quality: best      # best, 128k, 192k, 256k, 320k

# Subtitle defaults
subtitle_lang: en

# Download behaviour
resume: false
no_playlist: true        # download only the specified video, not a playlist

# Network
proxy: null              # e.g. http://proxy.example.com:8080
cookies_file: null       # path to Netscape-format cookies file
timeout: 30              # HTTP request timeout in seconds
max_retries: 3           # retry attempts for failed HTTP requests

# Logging
log_level: INFO          # DEBUG, INFO, WARNING, ERROR
log_file: null           # path to a file that receives all log output

# Cache
cache_dir: .ytcache      # directory for file-based cache
cache_ttl: 3600          # cache TTL in seconds (default: 3600 = 1 hour)
```

### JSON Configuration File

The same settings can be expressed in JSON:

```json
{
  "output_dir": "~/Downloads/YouTube",
  "default_quality": "best",
  "audio_format": "mp3",
  "audio_quality": "best",
  "subtitle_lang": "en",
  "resume": false,
  "no_playlist": true,
  "proxy": null,
  "cookies_file": null,
  "timeout": 30,
  "max_retries": 3,
  "log_level": "INFO",
  "log_file": null,
  "cache_dir": ".ytcache",
  "cache_ttl": 3600
}
```

### Environment Variables

Settings can also be supplied via environment variables. Environment variables
override config file values, which in turn override CLI defaults.

| Variable | Description | Default |
|----------|-------------|--------|
| `YT_PROXY` | HTTP / HTTPS proxy URL | — |
| `YT_LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `YT_LOG_FILE` | Path to a file that receives all DEBUG+ log output | — |
| `YT_OUTPUT_DIR` | Default output directory for downloads | `.` |
| `YT_TIMEOUT` | HTTP request timeout in seconds | `30` |
| `YT_MAX_RETRIES` | Number of retry attempts for failed requests | `3` |
| `NO_COLOR` | When set (to any value), disables ANSI colour output | — |

Example:

```bash
export YT_PROXY="http://proxy.example.com:8080"
export YT_LOG_LEVEL="DEBUG"
export YT_LOG_FILE="/tmp/ytdownloader.log"
export YT_OUTPUT_DIR="$HOME/Downloads/YouTube"

python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Programmatic Usage

```python
from ytdownloader import (
    download_video,
    download_audio,
    get_video_info,
    print_video_info,
    VideoInfo,
    StreamFormat,
    YTDLException,
    InvalidURLError,
    VideoUnavailableError,
    AgeRestrictedError,
    GeoRestrictedError,
    NetworkError,
    DownloadError,
    FormatSelectionError,
    setup_logging,
    get_logger,
)

# Configure logging before any other import uses it
setup_logging(level="INFO")

# Get video metadata as a dict
info = get_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(f"Title:   {info['title']}")
print(f"Author:  {info['author']}")
print(f"Length:  {info['duration']}s")
print(f"Views:   {info['view_count']:,}")

# Download video at best quality
path = download_video(
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    output_path="./downloads",
    quality="720p",
    resume=False,
)
print(f"Saved: {path}")

# Download audio as MP3 at 320 kbps
path = download_audio(
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    output_path="./downloads",
    audio_format="mp3",
    audio_quality="320k",
)
print(f"Saved: {path}")

# Print formatted info to stdout
print_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

# Handle errors explicitly
from ytdownloader.exceptions import (
    AgeRestrictedError,
    GeoRestrictedError,
    NetworkError,
    DownloadError,
    FormatSelectionError,
    VideoUnavailableError,
)
try:
    path = download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
except AgeRestrictedError:
    print("Age verification required — provide cookies with --cookies")
except GeoRestrictedError:
    print("Not available in this region — use --proxy")
except NetworkError as exc:
    print(f"Network error: {exc}")
except FormatSelectionError as exc:
    print(f"No suitable format: {exc}")
except VideoUnavailableError:
    print("Video unavailable or removed")
except DownloadError as exc:
    print(f"Download failed: {exc}")
```

---

## Error Handling

`ytdownloader` defines a structured exception hierarchy rooted at `YTDLException`.
Every public function raises specific subclasses so callers can handle each
failure mode independently. The `cause` attribute preserves the original
underlying exception for debugging.

### Exception Hierarchy

```
YTDLException (base)
├── InvalidURLError          — URL is not a recognised YouTube URL
├── VideoUnavailableError    — Video removed, private, or deleted
├── AgeRestrictedError       — Age-gate verification required
├── GeoRestrictedError       — Not available in current region
├── NetworkError             — Connection failure or timeout
├── DownloadError            — Stream download failed or interrupted
├── FormatSelectionError     — No suitable stream format found
├── SignatureCipherError     — signatureCipher URL parameter error
├── NResolverError           — JS n-parameter resolver error
├── MetadataExtractionError  — ytInitialPlayerResponse extraction failed
├── StreamResolutionError    — Stream URL resolution failed
├── SubtitleError            — Subtitle download or conversion failed
├── MergeError               — Audio/video stream merge failed
├── CacheError               — Cache read/write/clear operation failed
└── ConfigError              — Config loading, validation, or application failed
```

### Error Handling in the CLI

The CLI catches all known exception types and prints a colourised, user-friendly
message to stderr. On `KeyboardInterrupt` (Ctrl+C) it exits with code 130 to
allow shell scripting to distinguish cancellation from errors.

### Example: Catching Specific Errors

```python
from ytdownloader import download_video
from ytdownloader.exceptions import (
    AgeRestrictedError,
    GeoRestrictedError,
    NetworkError,
    DownloadError,
    FormatSelectionError,
    VideoUnavailableError,
    MetadataExtractionError,
)

try:
    path = download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
except AgeRestrictedError:
    print("This video requires age verification. Provide a cookies file.")
except GeoRestrictedError:
    print("Not available in your region. Use --proxy with a VPN endpoint.")
except NetworkError as exc:
    print(f"Network error: {exc}")
except MetadataExtractionError as exc:
    print(f"Could not read video data: {exc}")
except FormatSelectionError as exc:
    print(f"No downloadable format found: {exc}")
except VideoUnavailableError:
    print("Video unavailable or has been removed.")
except DownloadError as exc:
    print(f"Download failed: {exc}")
```

---

## Supported URL Formats

`ytdownloader` accepts all common YouTube URL formats:

| Format | Example |
|--------|---------|
| Standard watch | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |
| Short URL | `https://youtu.be/dQw4w9WgXcQ` |
| Shorts | `https://www.youtube.com/shorts/dQw4w9WgXcQ` |
| Embed | `https://www.youtube.com/embed/dQw4w9WgXcQ` |
| Live | `https://www.youtube.com/live/dQw4w9WgXcQ` |
| With extra params | `https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s` |
| Mobile | `https://m.youtube.com/watch?v=dQw4w9WgXcQ` |

URLs are normalised to the canonical `https://www.youtube.com/watch?v=VIDEO_ID`
form before any network request is made.

---

## Contributing

Contributions are welcome. To add a new feature or fix a bug:

1. **Fork** the repository and create a feature branch from `main`.
2. **Write tests** for any new behaviour. Run the test suite before opening a
   pull request:

   ```bash
   python -m pytest tests/ -v
   ```

3. **Follow the existing code style**: 4-space indentation, `from __future__ import annotations`
   in every module, full Google-style docstrings for all public functions and
   classes, and type annotations on every function signature.
4. **Add a module-level docstring** to every new `.py` file explaining its
   responsibility and how it fits into the overall architecture.
5. **Do not introduce new runtime dependencies** without prior discussion. The
   project deliberately depends only on `requests` (and optionally `pyyaml`
   and `colorama`).
6. **Open a pull request** with a clear description of the change, the
   motivation behind it, and any relevant issue references.

### Development Setup

```bash
git clone https://github.com/your-org/ytdownloader.git
cd ytdownloader
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

### Project Structure

```
ytdownloader/
  __init__.py         Package exports and version
  __main__.py         python -m ytdownloader entry point
  cli.py              CLI: argparse, ANSI output, format tables, dispatch
  downloader.py       Core download engine (yt-dlp based)
  metadata.py         HTML extraction: ytInitialPlayerResponse parser
  utils.py            URL validation, normalisation, video ID extraction
  exceptions.py       15 custom exception classes
  logger.py           Coloured structured logging with YTLogger wrapper
  cache.py            File-system cache with TTL (SHA-256 keys)
  constants.py        itag database, MIME maps, codec lists, URL templates

tests/
  test_cli.py
  test_downloader.py
  test_metadata.py
  test_utils.py
  test_cache.py
  test_exceptions.py
  test_logger.py

pyproject.toml
README.md
LICENSE
```

---

## License

MIT — see [LICENSE](LICENSE) for details.
