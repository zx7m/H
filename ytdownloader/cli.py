"""
CLI entry point for ytdownloader.

Wires argparse, URL validation, and download/metadata operations into
a user-facing ``python -m ytdownloader`` command.
"""

from __future__ import annotations

import sys

from .downloader import download_audio, download_video, print_video_info
from .utils import is_valid_youtube_url


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="ytdownloader",
        description="Download YouTube videos or inspect their metadata.",
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Download audio only (MP3 via ffmpeg when available).",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Print video metadata instead of downloading.",
    )
    parser.add_argument(
        "--output",
        default=".",
        help="Output directory (default: current directory).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress yt-dlp output.",
    )

    args = parser.parse_args(argv)

    url = args.url.strip()

    if not is_valid_youtube_url(url):
        print(
            f"Error: Invalid YouTube URL: {url}\n"
            "Supported formats:\n"
            "  - https://www.youtube.com/watch?v=VIDEO_ID\n"
            "  - https://youtu.be/VIDEO_ID\n"
            "  - https://www.youtube.com/shorts/VIDEO_ID\n"
            "  - https://www.youtube.com/embed/VIDEO_ID",
            file=sys.stderr,
        )
        return 1

    try:
        if args.info:
            print_video_info(url)
            return 0

        if args.audio:
            path = download_audio(url, output_path=args.output, quiet=args.quiet)
            print(f"Audio saved to: {path}")
        else:
            path = download_video(url, output_path=args.output, quiet=args.quiet)
            print(f"Video saved to: {path}")
        return 0

    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1
