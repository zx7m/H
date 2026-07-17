"""
Shared constants for ytdownloader.

These values are used across CLI, download logic, and format selection
to avoid duplication and keep supported options in sync.
"""

from __future__ import annotations

QUALITY_CHOICES: list[str] = [
    "best",
    "4320p",
    "2160p",
    "1440p",
    "1080p",
    "720p",
    "480p",
    "360p",
    "240p",
    "144p",
]
