# ytdownloader

A Python package and CLI for downloading YouTube videos and audio, and inspecting video metadata. Built on top of `yt-dlp` for stream negotiation and a custom `ytInitialPlayerResponse` parser for metadata.

## Installation

```bash
pip install .
```

## Usage

### CLI

```bash
# Download a video
ytdownloader "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Download audio only (MP3)
ytdownloader --audio "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Print video metadata
ytdownloader --info "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Specify output directory
ytdownloader --output ~/Videos "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Suppress yt-dlp output
ytdownloader --quiet "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Library

```python
from ytdownloader import (
    download_video,
    download_audio,
    get_video_info,
    is_valid_youtube_url,
    normalize_youtube_url,
    extract_video_id,
)

# Download video
path = download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", output_path="~/Videos")

# Download audio only
path = download_audio("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

# Get metadata
info = get_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
print(info["title"], info["duration"])
```

## Supported URL Formats

- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/shorts/VIDEO_ID`
- `https://www.youtube.com/embed/VIDEO_ID`
- `https://www.youtube.com/live/VIDEO_ID`

## Architecture

- **utils.py** – URL parsing, validation, and normalization.
- **metadata.py** – Fetches the YouTube watch page, extracts `ytInitialPlayerResponse` via a bracket-aware parser, and returns a normalized metadata dict.
- **downloader.py** – Wraps `yt-dlp` for actual download, with format selection that prefers ffmpeg when available.
- **cli.py** – Argparse-based CLI.

## Error Handling

Raises `ValueError` for invalid URLs, `MetadataExtractionError` for unavailable or geo-restricted content, and propagates `ImportError` when `yt-dlp` is not installed.
