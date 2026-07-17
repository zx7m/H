# ytdownloader

A Python YouTube video downloader that reverse-engineers YouTube's video delivery to extract and download video/audio streams. **No yt-dlp dependency — 100% from-scratch implementation.**

## Features

- Download videos from YouTube in the best available quality or a specific resolution
- Quality selection via `--quality` flag: `best`, `480p`, `720p`, `1080p`, etc.
- Extract audio only (native stream format)
- Print video metadata without downloading (`--info`)
- Supports all common YouTube URL formats: watch, shorts, embed, youtu.be
- Clean separation of concerns across dedicated modules

## Installation

```bash
pip install ytdownloader
```

Or from source:

```bash
git clone <repo>
cd ytdownloader
pip install -e .
```

## Usage

### Download a video (best quality)

```bash
python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Download at a specific quality

```bash
python -m ytdownloader --quality 720p "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Accepted values: `best` (default), `4320p`, `2160p`, `1440p`, `1080p`, `720p`, `480p`, `360p`, `240p`, `144p`.
When a resolution is given, the highest-quality stream at or below that resolution is selected.

### Download audio only

```bash
python -m ytdownloader --audio "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Print video metadata without downloading

```bash
python -m ytdownloader --info "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Specify output directory

```bash
python -m ytdownloader --output ./downloads "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Suppress progress output

```bash
python -m ytdownloader --quiet "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

## Supported URL Formats

- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/shorts/VIDEO_ID`
- `https://www.youtube.com/embed/VIDEO_ID`
- `https://www.youtube.com/live/VIDEO_ID`

## Architecture

```
ytdownloader/
  __init__.py      - Package exports and version
  __main__.py      - python -m ytdownloader entry point
  cli.py           - CLI entry point (argparse)
  downloader.py    - Core download logic: format selection, chunked download
  metadata.py      - Metadata extraction from ytInitialPlayerResponse
  stream_resolver.py - Stream URL resolution and format parsing
  utils.py         - URL parsing, validation, and helpers
```

### How it works

1. **URL Parsing** (`utils.py`): Validates and normalizes YouTube URLs, extracting the video ID.
2. **Metadata Extraction** (`metadata.py`): Fetches the YouTube watch page HTML and extracts the embedded `ytInitialPlayerResponse` JSON object. This contains all video metadata, available formats, and stream URLs.
3. **Stream Resolution** (`stream_resolver.py`): Parses the raw format list from `ytInitialPlayerResponse`, extracting direct stream URLs, codecs, resolution, and container information.
4. **Format Selection** (`downloader.py`): Selects the best stream matching the requested quality, preferring combined audio+video formats. Downloads stream bytes directly with progress indication.

## Error Handling

The tool handles common error scenarios gracefully:

- **Invalid URL**: Reports unsupported URL format with examples
- **Geo-restricted content**: Reports restriction
- **Age-gated content**: Reports age verification requirement
- **Private/removed videos**: Reports unavailability
- **Network errors**: Reports connection failures

## Requirements

- Python 3.9+
- requests
