"""
URL parsing and validation utilities.
"""

import re
from urllib.parse import urlparse, parse_qs


VALID_DOMAINS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

URL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]{11}"),
    re.compile(r"https?://(?:www\.)?youtube\.com/embed/[A-Za-z0-9_-]{11}"),
    re.compile(r"https?://(?:www\.)?youtube\.com/v/[A-Za-z0-9_-]{11}"),
    re.compile(r"https?://youtu\.be/[A-Za-z0-9_-]{11}"),
    re.compile(r"https?://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]{11}"),
    re.compile(r"https?://(?:www\.)?youtube\.com/live/[A-Za-z0-9_-]{11}"),
]

VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")


def is_valid_youtube_url(url: str) -> bool:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc.lower() not in VALID_DOMAINS:
        return False
    for pattern in URL_PATTERNS:
        if pattern.match(url):
            return True
    return False


def normalize_youtube_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)

    if parsed.netloc == "youtu.be":
        video_id = parsed.path.lstrip("/")
        return f"https://www.youtube.com/watch?v={video_id}"

    if "/shorts/" in url:
        video_id = extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

    if "/live/" in url:
        video_id = extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

    return url


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    v = params.get("v")
    if v:
        return v[0]
    match = re.search(r"/(?:embed|v|shorts|live)/([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    return None
