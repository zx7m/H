"""
YouTube-specific constants including itags, MIME types, protocols, and supporting values.

This module provides comprehensive constant definitions for YouTube video formats,
streaming protocols, and related metadata. All constants are typed and documented
for easy reference across the ytdownloader package.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# YouTube itag definitions
# ---------------------------------------------------------------------------

#: Mapping of itag numbers to human-readable quality labels.
ITAG_QUALITY: Dict[int, str] = {
    5: "240p",
    6: "270p",
    13: "144p",
    17: "144p",
    18: "360p",
    22: "720p",
    34: "360p",
    35: "480p",
    36: "240p",
    37: "1080p",
    38: "3072p",
    43: "360p",
    44: "480p",
    45: "720p",
    46: "1080p",
    59: "480p",
    78: "720p",
    82: "360p",
    83: "480p",
    84: "720p",
    85: "1080p",
    91: "144p",
    92: "240p",
    93: "360p",
    94: "480p",
    95: "720p",
    96: "1080p",
    100: "360p",
    101: "480p",
    102: "720p",
    132: "240p",
    133: "240p",
    134: "360p",
    135: "480p",
    136: "720p",
    137: "1080p",
    138: "2160p",
    139: "audio only",
    140: "audio only",
    141: "audio only",
    151: "720p",
    160: "144p",
    167: "360p",
    168: "480p",
    169: "720p",
    170: "1080p",
    212: "480p",
    213: "720p",
    214: "1080p",
    215: "1440p",
    216: "2160p",
    217: "2160p",
    218: "2160p",
    219: "2160p",
    242: "240p",
    243: "360p",
    244: "480p",
    245: "480p",
    246: "480p",
    247: "720p",
    248: "1080p",
    249: "audio only",
    250: "audio only",
    251: "audio only",
    252: "audio only",
    253: "audio only",
    254: "audio only",
    256: "audio only",
    258: "audio only",
    264: "1440p",
    266: "2160p",
    271: "1440p",
    272: "2160p",
    278: "144p",
    280: "audio only",
    298: "720p",
    299: "1080p",
    302: "720p",
    303: "1080p",
    308: "1440p",
    313: "2160p",
    315: "2160p",
    330: "144p",
    331: "240p",
    332: "360p",
    333: "480p",
    334: "720p",
    335: "1080p",
    336: "1440p",
    337: "2160p",
    338: "2160p",
    400: "240p",
    401: "360p",
    402: "480p",
    403: "540p",
    404: "720p",
    405: "1080p",
    406: "2160p",
    408: "240p",
    409: "360p",
    410: "480p",
    411: "720p",
    412: "1080p",
    431: "360p",
    432: "480p",
    433: "1440p",
    434: "2160p",
    435: "1440p",
    436: "2160p",
    437: "2160p",
    438: "2160p",
    461: "4320p",
    482: "360p",
    483: "480p",
    484: "720p",
    485: "1080p",
    486: "1440p",
    487: "2160p",
    562: "720p",
    563: "1080p",
    564: "1440p",
    565: "2160p",
    566: "2160p",
    571: "720p",
    572: "1080p",
    573: "1440p",
    574: "2160p",
    575: "4320p",
    576: "4320p",
    599: "1440p",
    600: "2160p",
    601: "2160p",
    602: "4320p",
    603: "4320p",
    609: "1440p",
    610: "2160p",
    612: "1440p",
    613: "2160p",
    614: "4320p",
    615: "4320p",
}

#: Reverse mapping: quality label to list of itag numbers.
QUALITY_ITAGS: Dict[str, List[int]] = {}
for _itag, _quality in ITAG_QUALITY.items():
    QUALITY_ITAGS.setdefault(_quality, []).append(_itag)


# ---------------------------------------------------------------------------
# Known YouTube itags with their MIME types and codecs
# ---------------------------------------------------------------------------

#: Detailed itag definitions with container, video codec, audio codec, and protocol.
ITAG_DETAILS: Dict[int, Dict[str, str]] = {
    5: {"container": "flv", "vcodec": "h263", "acodec": "mp3", "protocol": "http", "mime": "video/x-flv"},
    6: {"container": "flv", "vcodec": "h263", "acodec": "mp3", "protocol": "http", "mime": "video/x-flv"},
    13: {"container": "3gp", "vcodec": "mp4v", "acodec": "aac", "protocol": "http", "mime": "video/3gpp"},
    17: {"container": "3gp", "vcodec": "mp4v", "acodec": "aac", "protocol": "http", "mime": "video/3gpp"},
    18: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    22: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    34: {"container": "flv", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/x-flv"},
    35: {"container": "flv", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/x-flv"},
    36: {"container": "3gp", "vcodec": "mp4v", "acodec": "aac", "protocol": "http", "mime": "video/3gpp"},
    37: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    38: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    43: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    44: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    45: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    46: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    59: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    78: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    82: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    83: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    84: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    85: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    91: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    92: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    93: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    94: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    95: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    96: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    100: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    101: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    102: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    132: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    133: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    134: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    135: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    136: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    137: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    138: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    139: {"container": "mp4", "vcodec": "none", "acodec": "mp4a", "protocol": "http", "mime": "audio/mp4"},
    140: {"container": "mp4", "vcodec": "none", "acodec": "mp4a", "protocol": "http", "mime": "audio/mp4"},
    141: {"container": "mp4", "vcodec": "none", "acodec": "mp4a", "protocol": "http", "mime": "audio/mp4"},
    151: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    160: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    167: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    168: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    169: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    170: {"container": "webm", "vcodec": "vp8", "acodec": "vorbis", "protocol": "http", "mime": "video/webm"},
    212: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    213: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    214: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    215: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    216: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    217: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    218: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    219: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    242: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    243: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    244: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    245: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    246: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    247: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    248: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    249: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    250: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    251: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    252: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    253: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    254: {"container": "webm", "vcodec": "none", "acodec": "opus", "protocol": "http", "mime": "audio/webm"},
    256: {"container": "mp4", "vcodec": "none", "acodec": "mp4a", "protocol": "http", "mime": "audio/mp4"},
    258: {"container": "mp4", "vcodec": "none", "acodec": "mp4a", "protocol": "http", "mime": "audio/mp4"},
    264: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    266: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    271: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    272: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    278: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    280: {"container": "mp4", "vcodec": "none", "acodec": "mp4a", "protocol": "http", "mime": "audio/mp4"},
    298: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    299: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    302: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    303: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    308: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    313: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    315: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    330: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    331: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    332: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    333: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    334: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    335: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    336: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    337: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    338: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    400: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    401: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    402: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    403: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    404: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    405: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    406: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    408: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    409: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    410: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    411: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    412: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    431: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    432: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    433: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    434: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    435: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    436: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    437: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    438: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    461: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    482: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    483: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    484: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    485: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    486: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    487: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    562: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    563: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    564: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    565: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    566: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    571: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    572: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    573: {"container": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "http", "mime": "video/mp4"},
    574: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    575: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    576: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    599: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    600: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    601: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    602: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    603: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    609: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    610: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
    612: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    613: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    614: {"container": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "http", "mime": "video/webm"},
    615: {"container": "mp4", "vcodec": "h264", "acodec": "none", "protocol": "http", "mime": "video/mp4"},
}


# ---------------------------------------------------------------------------
# MIME type definitions
# ---------------------------------------------------------------------------

#: Map of MIME type to list of supported file extensions.
MIME_EXT_MAP: Dict[str, List[str]] = {
    "video/mp4": ["mp4"],
    "video/webm": ["webm"],
    "video/x-flv": ["flv"],
    "video/3gpp": ["3gp"],
    "audio/mp4": ["m4a", "mp4"],
    "audio/webm": ["webm", "weba"],
    "audio/mpeg": ["mp3"],
    "audio/aac": ["aac"],
    "application/x-mpegURL": ["m3u8"],
}

#: Reverse mapping: extension to MIME type.
EXT_MIME_MAP: Dict[str, str] = {
    ext: mime for mime, exts in MIME_EXT_MAP.items() for ext in exts
}


# ---------------------------------------------------------------------------
# Streaming protocols
# ---------------------------------------------------------------------------

#: Known YouTube streaming protocols.
PROTOCOLS: List[str] = [
    "http",
    "https",
    "dash",
    "hls",
    "m3u8",
]

#: Protocols that use progressive download (single file).
PROGRESSIVE_PROTOCOLS: List[str] = [
    "http",
    "https",
]

#: Protocols that use segmented streaming.
SEGMENTED_PROTOCOLS: List[str] = [
    "dash",
    "hls",
    "m3u8",
]


# ---------------------------------------------------------------------------
# Codec definitions
# ---------------------------------------------------------------------------

#: Video codecs known to be used by YouTube.
VIDEO_CODECS: List[str] = [
    "avc1",       # H.264
    "avc2",       # H.264/AVC
    "vp8",        # VP8 (WebM)
    "vp9",        # VP9 (WebM)
    "h263",       # H.263 (legacy FLV)
    "mp4v",       # MPEG-4 Visual
]

#: Audio codecs known to be used by YouTube.
AUDIO_CODECS: List[str] = [
    "aac",        # AAC (MP4/M4A)
    "mp3",        # MP3 (legacy)
    "opus",       # Opus (WebM)
    "vorbis",     # Vorbis (WebM)
]

#: Container formats known to be used by YouTube.
CONTAINERS: List[str] = [
    "mp4",
    "webm",
    "flv",
    "3gp",
    "m4a",
    "weba",
    "m3u8",
]


# ---------------------------------------------------------------------------
# Format preference constants
# ---------------------------------------------------------------------------

#: Default format preference order for video+audio combined.
DEFAULT_VIDEO_FORMAT_PREFERENCE: List[str] = [
    "mp4",
    "webm",
    "flv",
    "3gp",
]

#: Default format preference order for audio only.
DEFAULT_AUDIO_FORMAT_PREFERENCE: List[str] = [
    "mp3",
    "aac",
    "opus",
    "m4a",
]

#: Preferred video codec order.
PREFERRED_VIDEO_CODECS: List[str] = [
    "avc1",
    "vp9",
    "vp8",
    "h263",
    "mp4v",
]

#: Preferred audio codec order.
PREFERRED_AUDIO_CODECS: List[str] = [
    "aac",
    "opus",
    "mp3",
    "vorbis",
]


# ---------------------------------------------------------------------------
# Quality level definitions
# ---------------------------------------------------------------------------

#: Mapping of quality label strings to approximate height in pixels.
QUALITY_HEIGHT_MAP: Dict[str, int] = {
    "144p": 144,
    "240p": 240,
    "360p": 360,
    "480p": 480,
    "540p": 540,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160,
    "3072p": 3072,
    "4320p": 4320,
}

#: Maximum quality level name.
MAX_QUALITY: str = "4320p"

#: Minimum quality level name.
MIN_QUALITY: str = "144p"


# ---------------------------------------------------------------------------
# Network and HTTP constants
# ---------------------------------------------------------------------------

#: Default user agent string used for HTTP requests.
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

#: Accept header for HTTP requests.
DEFAULT_ACCEPT_HEADER: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

#: Accept-Language header.
DEFAULT_ACCEPT_LANGUAGE: str = "en-US,en;q=0.9"

#: Default request timeout in seconds.
DEFAULT_TIMEOUT: int = 30

#: Default number of retry attempts for failed HTTP requests.
DEFAULT_MAX_RETRIES: int = 3

#: Default retry delay base in seconds (exponential backoff multiplier).
DEFAULT_RETRY_DELAY_BASE: float = 1.0

#: Default chunk size for streaming downloads in bytes.
DEFAULT_CHUNK_SIZE: int = 1024 * 1024  # 1 MB


# ---------------------------------------------------------------------------
# YouTube-specific URL and endpoint constants
# ---------------------------------------------------------------------------

#: YouTube watch URL format string.
YOUTUBE_WATCH_URL_FORMAT: str = "https://www.youtube.com/watch?v={video_id}"

#: YouTube embed URL format string.
YOUTUBE_EMBED_URL_FORMAT: str = "https://www.youtube.com/embed/{video_id}"

#: YouTube shorts URL format string.
YOUTUBE_SHORTS_URL_FORMAT: str = "https://www.youtube.com/shorts/{video_id}"

#: YouTube video ID regex pattern.
YOUTUBE_VIDEO_ID_PATTERN: str = (
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)

#: Default headers for YouTube page requests.
YOUTUBE_PAGE_HEADERS: Dict[str, str] = {
    "Accept": DEFAULT_ACCEPT_HEADER,
    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
    "User-Agent": DEFAULT_USER_AGENT,
    "Referer": "https://www.youtube.com/",
}


# ---------------------------------------------------------------------------
# Signature cipher constants
# ---------------------------------------------------------------------------

#: Known signature parameter names used in YouTube URLs.
SIGNATURE_PARAM_NAMES: List[str] = [
    "s",
    "sig",
    "signature",
]

#: Known signature parameter names for the URL signature.
SIGNATURE_SP_NAMES: List[str] = [
    "sp",
]

#: URL parameter name for the n-parameter.
N_PARAM_NAME: str = "n"


# ---------------------------------------------------------------------------
# Caption and subtitle constants
# ---------------------------------------------------------------------------

#: Default caption format for conversion.
DEFAULT_CAPTION_FORMAT: str = "srt"

#: Supported subtitle formats.
SUPPORTED_SUBTITLE_FORMATS: List[str] = [
    "srt",
    "vtt",
    "xml",
    "json3",
]


# ---------------------------------------------------------------------------
# Download defaults
# ---------------------------------------------------------------------------

#: Default output directory for downloaded files.
DEFAULT_OUTPUT_DIR: str = "."

#: Default audio format for audio-only downloads.
DEFAULT_AUDIO_FORMAT: str = "mp3"

#: Default video format for video downloads.
DEFAULT_VIDEO_FORMAT: str = "mp4"

#: Default quality for downloads.
DEFAULT_QUALITY: str = "best"

#: Maximum number of concurrent downloads.
DEFAULT_MAX_CONCURRENT_DOWNLOADS: int = 3


# ---------------------------------------------------------------------------
# Logging defaults
# ---------------------------------------------------------------------------

#: Default log level string.
DEFAULT_LOG_LEVEL: str = "INFO"

#: Environment variable names for configuration overrides.
ENV_VAR_YT_PROXY: str = "YT_PROXY"
ENV_VAR_YT_LOG_LEVEL: str = "YT_LOG_LEVEL"
ENV_VAR_YT_LOG_FILE: str = "YT_LOG_FILE"
ENV_VAR_YT_OUTPUT_DIR: str = "YT_OUTPUT_DIR"
ENV_VAR_YT_TIMEOUT: str = "YT_TIMEOUT"
ENV_VAR_YT_MAX_RETRIES: str = "YT_MAX_RETRIES"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

#: Regex to extract video ID from a YouTube URL.
RE_VIDEO_ID: str = YOUTUBE_VIDEO_ID_PATTERN

#: Regex to extract the ytInitialPlayerResponse JavaScript object.
RE_PLAYER_RESPONSE: str = r"ytInitialPlayerResponse\s*=\s*({.*?});?\s*[;,\n]"

#: Regex to extract the ytcfg object.
RE_YTCFG: str = r"ytcfg\s*=\s*({.*?});?\s*[;,\n]"

#: Regex to extract the sts token.
RE_STS: str = r"sts\"\s*:\s*(\d+)"

#: Regex to extract the initial data object.
RE_INITIAL_DATA: str = r"ytInitialData\s*=\s*({.*?});?\s*[;,\n]"

#: Regex to detect age restriction.
RE_AGE_RESTRICTED: str = r"age[_\-]?restricted|age[_\-]?gate|content[_\-]?rating[_\-]?system"

#: Regex to detect geo restriction.
RE_GEO_RESTRICTED: str = r"geo[_\-]?restricted|country[_\-]?restricted|not[_\-]?available[_\-]?in[_\-]?your[_\-]?country"


# ---------------------------------------------------------------------------
# Feature flags and capability constants
# ---------------------------------------------------------------------------

#: Itags that contain both audio and video (progressive formats).
PROGRESSIVE_ITAGS: List[int] = [
    5, 6, 13, 17, 18, 22, 34, 35, 36, 37, 38,
    43, 44, 45, 46, 59, 78, 82, 83, 84, 85,
    91, 92, 93, 94, 95, 96, 100, 101, 102,
    132, 151, 160, 215, 216, 217, 218, 219,
]

#: Itags that contain video only (DASH/adaptive video).
VIDEO_ONLY_ITAGS: List[int] = [
    133, 134, 135, 136, 137, 138,
    242, 243, 244, 245, 246, 247, 248,
    264, 266, 271, 272, 278,
    298, 299, 302, 303, 308, 313, 315,
    330, 331, 332, 333, 334, 335, 336, 337, 338,
    400, 401, 402, 403, 404, 405, 406,
    408, 409, 410, 411, 412,
    431, 432, 433, 434, 435, 436, 437, 438,
    461,
    482, 483, 484, 485, 486, 487,
    562, 563, 564, 565, 566,
    571, 572, 573, 574, 575, 576,
    599, 600, 601, 602, 603,
    609, 610, 612, 613, 614, 615,
]

#: Itags that contain audio only (DASH/adaptive audio).
AUDIO_ONLY_ITAGS: List[int] = [
    139, 140, 141,
    249, 250, 251, 252, 253, 254,
    256, 258, 280,
    397, 398, 399, 400, 401, 402,
]


# ---------------------------------------------------------------------------
# Format string constants
# ---------------------------------------------------------------------------

#: yt-dlp format string template for best combined audio+video.
FORMAT_BEST_COMBINED: str = "bestvideo+bestaudio/best"

#: yt-dlp format string template for best audio only.
FORMAT_BEST_AUDIO: str = "bestaudio/best"

#: yt-dlp format string template for best mp4 video.
FORMAT_BEST_MP4: str = "best[ext=mp4]/best"

#: Output filename template used by yt-dlp.
OUTPUT_TEMPLATE: str = "%(title)s [%(id)s].%(ext)s"


# ---------------------------------------------------------------------------
# Thumbnail size definitions
# ---------------------------------------------------------------------------

#: YouTube thumbnail sizes and their URLs.
THUMBNAIL_SIZES: List[Dict[str, str]] = [
    {"name": "default", "url": "https://i.ytimg.com/vi/{video_id}/default.jpg", "width": 120, "height": 90},
    {"name": "mqdefault", "url": "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", "width": 320, "height": 180},
    {"name": "hqdefault", "url": "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "width": 480, "height": 360},
    {"name": "sddefault", "url": "https://i.ytimg.com/vi/{video_id}/sddefault.jpg", "width": 640, "height": 480},
    {"name": "maxresdefault", "url": "https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg", "width": 1280, "height": 720},
]


# ---------------------------------------------------------------------------
# Playback status codes
# ---------------------------------------------------------------------------

#: YouTube playability status codes.
PLAYABILITY_STATUSES: Dict[str, str] = {
    "OK": "Video is playable",
    "AGE_CHECK_REQUIRED": "Age check required",
    "AGE_VERIFICATION_REQUIRED": "Age verification required",
    "AGE_CHECK_REQUIRED_OR_AGE_VERIFICATION_REQUIRED": "Age check or verification required",
    "AGE_VERIFICATION_REQUIRED_OR_AGE_CHECK_REQUIRED": "Age verification or check required",
    "CONTENT_CHECK_REQUIRED": "Content check required",
    "CONTENT_RATING_REQUIRED": "Content rating required",
    "EMBEDDING_DISABLED": "Embedding disabled",
    "ERROR": "Playback error",
    "LOGIN_REQUIRED": "Login required",
    "LIVE_STREAM_OFFLINE": "Live stream offline",
    "LIVE_STREAM_OFFLINE_WITH_CONTENT": "Live stream offline with content",
    "UNPLAYABLE": "Video is unplayable",
    "AGE_GATE": "Age gate",
    "GEO_RESTRICTED": "Geo-restricted",
    "PRIVATE_VIDEO": "Private video",
    "VIDEO_NOT_FOUND": "Video not found",
}


# ---------------------------------------------------------------------------
# Cache constants
# ---------------------------------------------------------------------------

#: Default cache directory name.
DEFAULT_CACHE_DIR: str = ".ytcache"

#: Default cache TTL in seconds.
DEFAULT_CACHE_TTL: int = 3600  # 1 hour

#: Maximum cache TTL in seconds.
MAX_CACHE_TTL: int = 86400  # 24 hours

#: Minimum cache TTL in seconds.
MIN_CACHE_TTL: int = 60  # 1 minute
