"""
ytdownloader - A YouTube video downloader that reverse-engineers
YouTube's video delivery to extract and download video/audio streams.
"""

from __future__ import annotations

import importlib.util
import warnings

__version__ = "1.0.0"

_OPTIONAL_IMPORTS: list[tuple[str, list[str]]] = [
    (".downloader", ["download_video", "download_audio", "get_video_info", "select_format"]),
    (".utils", ["is_valid_youtube_url", "extract_video_id"]),
]

_available_names: list[str] = []

for module_path, public_names in _OPTIONAL_IMPORTS:
    if importlib.util.find_spec(module_path) is not None:
        try:
            mod = __import__(module_path, fromlist=public_names, level=1)
            for name in public_names:
                globals()[name] = getattr(mod, name)
                _available_names.append(name)
        except ImportError:
            warnings.warn(
                f"Optional import of {module_path} failed; "
                f"core functionality ({', '.join(public_names)}) is unavailable.",
                ImportWarning,
                stacklevel=2,
            )
    else:
        warnings.warn(
            f"Optional module {module_path} is not installed; "
            f"core functionality ({', '.join(public_names)}) is unavailable.",
            ImportWarning,
            stacklevel=2,
        )

__all__ = _available_names
