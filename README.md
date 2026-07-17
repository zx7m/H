# ytdownloader

A Python YouTube video downloader that reverse-engineers YouTube's video delivery to extract and download video/audio streams.

## Features

- Download videos from YouTube in the best available quality
- Extract audio only (MP3 format)
- Print video metadata without downloading (`--info`)
- Supports all common YouTube URL formats: watch, shorts, embed, youtu.be
- Clean separation of concerns: URL parsing, metadata extraction, download logic

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

### Download a video

```bash
python -m ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Download audio only (MP3)

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

## Supported URL Formats

- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/shorts/VIDEO_ID`
- `https://www.youtube.com/embed/VIDEO_ID`
- `https://www.youtube.com/live/VIDEO_ID`

## Architecture

```
ytdownloader/
  __init__.py      - Package exports
  cli.py           - CLI entry point (argparse)
  downloader.py    - Core download logic (pure Python requests)
  metadata.py      - Metadata extraction from ytInitialPlayerResponse
  utils.py         - URL parsing and validation
```

### How it works

1. **URL Parsing** (`utils.py`): Validates and normalizes YouTube URLs, extracting the video ID.
2. **Metadata Extraction** (`metadata.py`): Fetches the YouTube watch page HTML and extracts the embedded `ytInitialPlayerResponse` JSON object. This contains all video metadata, available formats, and stream URLs.
3. **Download** (`downloader.py`): Uses pure Python `requests` to fetch and download streams directly, selecting the best available format.

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
