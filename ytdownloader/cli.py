"""
CLI entry point for ytdownloader.

Usage:
    python -m ytdownloader "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --audio "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --info "https://www.youtube.com/watch?v=..."
    python -m ytdownloader --output ./downloads "https://www.youtube.com/watch?v=..."
"""

from __future__ import annotations

import argparse
import sys

from .downloader import download_audio, download_video, print_video_info
from .utils import is_valid_youtube_url


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytdownloader",
        description="Download videos and audio from YouTube by reverse-engineering video delivery.",
    )
    parser.add_argument(
        "url",
        help="YouTube video URL (watch, shorts, embed, or youtu.be format)",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        default=False,
        help="Download audio only (converts to MP3)",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        default=False,
        help="Print video metadata without downloading",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=".",
        help="Output directory for downloaded files (default: current directory)",
    )
    parser.add_argument(
        "--quality",
        default="best",
        choices=["best", "4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"],
        help="Video quality to download (default: best)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        default=False,
        help="Suppress progress output",
    )

    parsed = parser.parse_args(args)

    url = parsed.url.strip()

    if not is_valid_youtube_url(url):
        print(f"Error: Invalid YouTube URL: {url}", file=sys.stderr)
        print("\nSupported URL formats:", file=sys.stderr)
        print("  - https://www.youtube.com/watch?v=VIDEO_ID", file=sys.stderr)
        print("  - https://youtu.be/VIDEO_ID", file=sys.stderr)
        print("  - https://www.youtube.com/shorts/VIDEO_ID", file=sys.stderr)
        print("  - https://www.youtube.com/embed/VIDEO_ID", file=sys.stderr)
        return 1

    try:
        if parsed.info:
            print_video_info(url)
            return 0

        if parsed.audio:
            print(f"Downloading audio from: {url}")
            output_path = download_audio(url, output_path=parsed.output, quiet=parsed.quiet)
            print(f"Audio saved to: {output_path}")
        else:
            print(f"Downloading video from: {url}")
            output_path = download_video(
                url,
                output_path=parsed.output,
                quiet=parsed.quiet,
                quality=parsed.quality,
            )
            print(f"Video saved to: {output_path}")

        return 0

    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
