"""
ytdownloader - A YouTube video downloader that reverse-engineers
YouTube's video delivery to extract and download video/audio streams.
"""

from __future__ import annotations

import importlib.util
import warnings
import sys

__version__ = "2.0.0"

_pkg = __name__

def _safe_import(module_path: str, public_names: list[str]) -> None:
    try:
        mod = importlib.import_module(module_path)
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

_modules_to_import = [
    ("ytdownloader.downloader", [
        "download_video", "download_audio", "print_video_info", "get_video_info",
    ]),
    ("ytdownloader.http_downloader", [
        "download_stream", "download_audio_from_info", "download_video_from_info",
        "compute_output_path",
    ]),
    ("ytdownloader.stream_resolver", [
        "StreamFormat", "parse_streaming_data", "resolve_streams",
        "get_best_format", "filter_formats", "sort_formats",
        "get_format_by_itag", "get_audio_only_formats",
        "get_video_only_formats", "get_combined_formats",
    ]),
    ("ytdownloader.utils", ["is_valid_youtube_url", "extract_video_id"]),
    ("ytdownloader.n_resolver", ["resolve_n_param", "NResolver"]),
    ("ytdownloader.cipher", ["decipher_url", "parse_signature_cipher", "apply_signature"]),
]

_available_names: list[str] = []

for module_path, public_names in _modules_to_import:
    if importlib.util.find_spec(module_path) is not None:
        _safe_import(module_path, public_names)
    else:
        warnings.warn(
            f"Optional module {module_path} is not installed; "
            f"core functionality ({', '.join(public_names)}) is unavailable.",
            ImportWarning,
            stacklevel=2,
        )

__all__ = _available_names
