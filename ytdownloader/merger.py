from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .exceptions import MergeError
from .logger import get_logger


logger = get_logger(__name__)

_FFMPEG_BINARIES = ("ffmpeg", "ffmpeg.exe", "ffmpeg-arm64", "ffmpeg-x86_64")
_FFMPEG_CACHE: Optional[str] = None
_FFMPEG_AVAILABLE_CACHE: Optional[bool] = None

SUPPORTED_CONTAINERS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".ts",
    ".m4a",
    ".mp3",
    ".aac",
    ".flac",
    ".wav",
    ".ogg",
    ".opus",
}

COMPATIBLE_CONTAINER_CODECS: dict[str, list[str]] = {
    ".mp4": ["libx264", "libx265", "mpeg4", "libsvtav1", "copy"],
    ".webm": ["libvpx-vp9", "libvpx", "libopus", "copy"],
    ".mkv": ["libx264", "libx265", "libvpx-vp9", "libopus", "libaac", "copy"],
    ".mov": ["libx264", "libx265", "copy"],
    ".avi": ["mpeg4", "copy"],
}

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_OUTPUT_CONTAINER = ".mp4"

_FFPROBE_BINARIES = ("ffprobe", "ffprobe.exe")


def get_ffmpeg_path() -> str:
    """Locate the ``ffmpeg`` binary in the system ``PATH``.

    Returns:
        The absolute or relative path to the ``ffmpeg`` executable.

    Raises:
        MergeError: When no ffmpeg binary can be found.
    """
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is not None:
        return _FFMPEG_CACHE

    for name in _FFMPEG_BINARIES:
        path = shutil.which(name)
        if path:
            _FFMPEG_CACHE = path
            logger.debug("Found ffmpeg binary: %s", path)
            return path

    candidates = [
        os.path.join(os.environ.get("FFMPEG_DIR", ""), "ffmpeg"),
        os.path.join(os.environ.get("FFMPEG_DIR", ""), "bin", "ffmpeg"),
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
        os.path.expanduser("~/ffmpeg/bin/ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            _FFMPEG_CACHE = candidate
            logger.debug("Found ffmpeg binary at candidate path: %s", candidate)
            return candidate

    raise MergeError(
        "ffmpeg binary not found. "
        "Please install ffmpeg (https://ffmpeg.org/download.html) "
        "and ensure it is in your PATH."
    )


def get_ffprobe_path() -> str:
    """Locate the ``ffprobe`` binary bundled with ffmpeg.

    Returns:
        The absolute or relative path to ``ffprobe``.

    Raises:
        MergeError: When ffprobe cannot be located.
    """
    ffmpeg_dir = os.path.dirname(get_ffmpeg_path())
    for name in _FFPROBE_BINARIES:
        candidate = os.path.join(ffmpeg_dir, name)
        if os.path.isfile(candidate):
            return candidate
        path = shutil.which(name)
        if path:
            return path
    raise MergeError(
        "ffprobe binary not found alongside ffmpeg. "
        "Ensure ffprobe is installed and accessible."
    )


def _check_ffmpeg() -> bool:
    """Return ``True`` if ffmpeg is available on the system.

    Returns:
        ``True`` when an ``ffmpeg`` binary is discoverable, ``False`` otherwise.
    """
    global _FFMPEG_AVAILABLE_CACHE
    if _FFMPEG_AVAILABLE_CACHE is not None:
        return _FFMPEG_AVAILABLE_CACHE
    try:
        get_ffmpeg_path()
        _FFMPEG_AVAILABLE_CACHE = True
    except MergeError:
        _FFMPEG_AVAILABLE_CACHE = False
    return _FFMPEG_AVAILABLE_CACHE


def _validate_input_file(path: str, label: str) -> None:
    """Assert that *path* exists and is a regular file.

    Args:
        path: Filesystem path to validate.
        label: Human-readable name used in error messages.

    Raises:
        MergeError: When *path* does not exist, is not a file, or is empty.
    """
    resolved = os.path.abspath(path)
    if not os.path.exists(resolved):
        raise MergeError(
            f"{label} not found: '{resolved}'. "
            "Verify the path and try again."
        )
    if not os.path.isfile(resolved):
        raise MergeError(
            f"{label} is not a regular file: '{resolved}'."
        )
    size = os.path.getsize(resolved)
    if size == 0:
        raise MergeError(
            f"{label} exists but is empty (0 bytes): '{resolved}'."
        )
    if size < 1024:
        logger.warning(
            "%s is unusually small (%d bytes): '%s'. "
            "The file may be corrupt.",
            label,
            size,
            resolved,
        )
    logger.debug("Validated %s: %s (%d bytes)", label, resolved, size)


def _get_file_extension(path: str) -> str:
    """Return the lowercase file extension of *path*.

    Args:
        path: File path to inspect.

    Returns:
        Lowercase extension including the leading dot, or an empty string.
    """
    return Path(path).suffix.lower()


def _probe_stream_info(path: str) -> dict:
    """Probe *path* with ffprobe and return its stream metadata.

    Args:
        path: Media file to probe.

    Returns:
        A dict with keys ``vcodec``, ``acodec``, ``format_name``,
        ``duration``, ``width``, ``height``, ``fps``.

    Raises:
        MergeError: When ffprobe fails or the file is unreadable.
    """
    ffprobe = get_ffprobe_path()
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        path,
    ]
    logger.debug("Running ffprobe: %s", " ".join(cmd))
    try:
        result = subprocess.run(  # nosec: command list constructed internally, no shell=True
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise MergeError(
            "ffprobe executable not found during probing."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MergeError(
            f"ffprobe timed out while probing '{path}'."
        ) from exc

    if result.returncode != 0:
        raise MergeError(
            f"ffprobe failed for '{path}'. "
            f"stderr: {result.stderr.strip()}"
        )

    try:
        import json
        probe_data = json.loads(result.stdout)
    except ValueError as exc:
        raise MergeError(
            f"ffprobe returned invalid JSON for '{path}'."
        ) from exc

    info: dict = {
        "vcodec": None,
        "acodec": None,
        "format_name": "",
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "stream_count": 0,
        "video_streams": 0,
        "audio_streams": 0,
    }
    fmt = probe_data.get("format", {})
    info["format_name"] = fmt.get("format_name", "")
    try:
        info["duration"] = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        info["duration"] = 0.0

    for stream in probe_data.get("streams", []):
        info["stream_count"] += 1
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            info["video_streams"] += 1
            info["vcodec"] = stream.get("codec_name")
            try:
                info["width"] = int(stream.get("width", 0))
            except (TypeError, ValueError):
                info["width"] = 0
            try:
                info["height"] = int(stream.get("height", 0))
            except (TypeError, ValueError):
                info["height"] = 0
            avg_rate = stream.get("avg_frame_rate", "0/1")
            try:
                num, den = avg_rate.split("/")
                num, den = int(num), int(den)
                info["fps"] = num / den if den else 0.0
            except (TypeError, ValueError):
                info["fps"] = 0.0
        elif codec_type == "audio":
            info["audio_streams"] += 1
            info["acodec"] = stream.get("codec_name")

    logger.debug(
        "Probed '%s': vcodec=%s, acodec=%s, format=%s, "
        "duration=%.2fs, %dx%d, fps=%.2f",
        path,
        info["vcodec"],
        info["acodec"],
        info["format_name"],
        info["duration"],
        info["width"],
        info["height"],
        info["fps"],
    )
    return info


def _is_compatible_container_codec(container: str, codec: str) -> bool:
    """Return ``True`` when *codec* is compatible with *container*.

    Args:
        container: Lowercase file extension (e.g. ``.mp4``).
        codec: Codec name (e.g. ``libx264``).

    Returns:
        ``True`` if the codec is known to work in the container.
    """
    if codec is None:
        return True
    allowed = COMPATIBLE_CONTAINER_CODECS.get(container, [])
    if codec in allowed:
        return True
    if codec == "copy":
        return True
    if container == ".mp4" and codec.startswith("libx") and codec != "libvpx":
        return True
    if container == ".mkv" and codec in {
        "libx264", "libx265", "libvpx-vp9", "libvpx", "libopus",
        "libaac", "aac", "copy",
    }:
        return True
    return False


def _select_output_container(
    video_ext: str, audio_ext: str, requested: str
) -> str:
    """Pick a compatible output container extension.

    Chooses from *requested*, then *video_ext*, then a sensible default,
    falling back to ``.mp4``.

    Args:
        video_ext: Lowercase extension of the video stream file.
        audio_ext: Lowercase extension of the audio stream file.
        requested: User-requested output extension (may be empty).

    Returns:
        Lowercase output extension including the leading dot.
    """
    candidates: list[str] = []
    if requested:
        candidates.append(requested.lower())
    candidates.extend([video_ext, audio_ext, DEFAULT_OUTPUT_CONTAINER])
    for ext in candidates:
        if ext in SUPPORTED_CONTAINERS:
            return ext
    return DEFAULT_OUTPUT_CONTAINER


def _select_merge_codecs(
    output_ext: str,
    video_info: dict,
    audio_info: dict,
) -> tuple[str, str]:
    """Choose video and audio codecs for the merge output.

    Prefers ``copy`` when streams are already in a compatible codec for
    the target container.  Falls back to re-encoding otherwise.

    Args:
        output_ext: Lowercase output file extension.
        video_info: Probe result dict for the video stream.
        audio_info: Probe result dict for the audio stream.

    Returns:
        A ``(video_codec, audio_codec)`` tuple.
    """
    vcodec = video_info.get("vcodec") or ""
    acodec = audio_info.get("acodec") or ""

    if _is_compatible_container_codec(output_ext, vcodec):
        v_codec_out = "copy"
    else:
        v_codec_out = DEFAULT_VIDEO_CODEC
        logger.debug(
            "Video codec '%s' not directly compatible with '%s'; "
            "re-encoding as '%s'.",
            vcodec,
            output_ext,
            v_codec_out,
        )

    if _is_compatible_container_codec(output_ext, acodec):
        a_codec_out = "copy"
    else:
        a_codec_out = DEFAULT_AUDIO_CODEC
        logger.debug(
            "Audio codec '%s' not directly compatible with '%s'; "
            "re-encoding as '%s'.",
            acodec,
            output_ext,
            a_codec_out,
        )

    return v_codec_out, a_codec_out


def _run_command(
    cmd: list[str],
    timeout: int = 600,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run *cmd* as a subprocess and return the result.

    Args:
        cmd: Command and arguments as a list of strings.
        timeout: Maximum run time in seconds.
        cwd: Working directory for the subprocess.

    Returns:
        A :class:`subprocess.CompletedProcess` instance.

    Raises:
        MergeError: When the command exits with a non-zero status or times out.
    """
    logger.debug("Running command: %s", " ".join(cmd))
    try:
        result = subprocess.run(  # nosec: command list constructed internally, no shell=True
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise MergeError(
            f"Command not found: '{cmd[0]}'. "
            "Ensure the required binary is installed and in PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MergeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}"
        ) from exc

    if result.returncode != 0:
        stderr_snippet = result.stderr.strip()[-500:]
        raise MergeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}. "
            f"stderr: {stderr_snippet}"
        )
    return result


def _build_ffmpeg_merge_cmd(
    ffmpeg_bin: str,
    video_path: str,
    audio_path: str,
    output_path: str,
    video_codec: str,
    audio_codec: str,
    fast_start: bool = True,
) -> list[str]:
    """Construct the ffmpeg command list for merging streams.

    Args:
        ffmpeg_bin: Path to the ffmpeg executable.
        video_path: Path to the video input file.
        audio_path: Path to the audio input file.
        output_path: Destination path for the merged file.
        video_codec: Video codec string (e.g. ``"copy"`` or ``"libx264"``).
        audio_codec: Audio codec string (e.g. ``"copy"`` or ``"aac"``).
        fast_start: When ``True``, add ``-movflags +faststart`` for MP4.

    Returns:
        A list of command arguments ready for :func:`subprocess.run`.
    """
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", video_codec,
        "-c:a", audio_codec,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
    ]
    if video_codec != "copy" and video_codec in ("libx264", "libx265"):
        cmd += ["-preset", "fast", "-crf", "23"]
    if audio_codec != "copy" and audio_codec in ("aac", "libopus"):
        cmd += ["-b:a", "192k"]
    ext = _get_file_extension(output_path)
    if ext == ".mp4" and fast_start:
        cmd += ["-movflags", "+faststart"]
    cmd += [output_path]
    return cmd


def _merge_with_ffmpeg(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    """Merge *video_path* and *audio_path* using ffmpeg.

    Probes both files for codec information, selects compatible output
    codecs, runs ffmpeg, and verifies the output file exists and is
    non-empty.

    Args:
        video_path: Path to the video stream file.
        audio_path: Path to the audio stream file.
        output_path: Destination path for the merged file.

    Returns:
        The absolute path to the merged output file.

    Raises:
        MergeError: When ffmpeg is unavailable or the merge fails.
    """
    logger.info(
        "Merging with ffmpeg: '%s' + '%s' -> '%s'",
        video_path,
        audio_path,
        output_path,
    )
    ffmpeg_bin = get_ffmpeg_path()
    video_info = _probe_stream_info(video_path)
    audio_info = _probe_stream_info(audio_path)
    output_ext = _select_output_container(
        _get_file_extension(video_path),
        _get_file_extension(audio_path),
        _get_file_extension(output_path),
    )
    actual_output = output_path if output_path.lower().endswith(output_ext) else (
        str(Path(output_path).with_suffix(output_ext))
    )
    v_codec, a_codec = _select_merge_codecs(output_ext, video_info, audio_info)
    logger.info(
        "Selected codecs: video=%s, audio=%s, container=%s",
        v_codec,
        a_codec,
        output_ext,
    )
    cmd = _build_ffmpeg_merge_cmd(
        ffmpeg_bin, video_path, audio_path, actual_output, v_codec, a_codec
    )
    _run_command(cmd, timeout=600)
    if not os.path.isfile(actual_output):
        raise MergeError(
            f"ffmpeg completed but output file not found: '{actual_output}'."
        )
    out_size = os.path.getsize(actual_output)
    if out_size == 0:
        raise MergeError(
            f"ffmpeg output file is empty: '{actual_output}'. "
            "The merge may have failed silently."
        )
    logger.info(
        "ffmpeg merge complete: '%s' (%d bytes)",
        actual_output,
        out_size,
    )
    return os.path.abspath(actual_output)


def _merge_basic(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    """Perform a basic stream copy merge without re-encoding.

    Uses ``cat`` on raw compatible streams (e.g. ``.ts``) or falls back
    to a minimal copy strategy.  This is a last-resort fallback when
    ffmpeg is unavailable.

    Args:
        video_path: Path to the video stream file.
        audio_path: Path to the audio stream file.
        output_path: Destination path for the merged file.

    Returns:
        The absolute path to the merged output file.

    Raises:
        MergeError: When the merge cannot be completed.
    """
    logger.info(
        "Performing basic merge: '%s' + '%s' -> '%s'",
        video_path,
        audio_path,
        output_path,
    )
    resolved_output = os.path.abspath(output_path)
    ext = _get_file_extension(video_path)
    if ext == ".ts":
        tmp_ts = resolved_output + ".tmp.ts"
        logger.debug("Concatenating TS segments.")
        with open(tmp_ts, "wb") as out_f:
            for src in (video_path, audio_path):
                with open(src, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f, length=1024 * 1024)
        if os.path.getsize(tmp_ts) == 0:
            raise MergeError(
                "Basic TS concatenation produced an empty file."
            )
        os.replace(tmp_ts, resolved_output)
    else:
        tmp_path = resolved_output + ".tmp"
        try:
            with open(tmp_path, "wb") as out_f:
                with open(video_path, "rb") as vf:
                    shutil.copyfileobj(vf, out_f, length=1024 * 1024)
                with open(audio_path, "rb") as af:
                    shutil.copyfileobj(af, out_f, length=1024 * 1024)
        except OSError as exc:
            raise MergeError(
                f"Failed during basic merge of '{video_path}' and "
                f"'{audio_path}': {exc}"
            ) from exc
        if os.path.getsize(tmp_path) == 0:
            raise MergeError(
                "Basic merge produced an empty output file."
            )
        os.replace(tmp_path, resolved_output)

    out_size = os.path.getsize(resolved_output)
    logger.info(
        "Basic merge complete: '%s' (%d bytes). "
        "Note: the output may lack proper audio/video sync or container headers.",
        resolved_output,
        out_size,
    )
    return resolved_output


def _determine_output_path(
    output_path: Optional[str],
    video_path: str,
    output_ext: str,
) -> str:
    """Resolve the final output path, applying a default if needed.

    Args:
        output_path: User-supplied output path (may be ``None``).
        video_path: Path to the video input file.
        output_ext: Desired output extension.

    Returns:
        Absolute path string for the output file.
    """
    if output_path:
        candidate = str(output_path)
        if not candidate.lower().endswith(output_ext):
            candidate = str(Path(candidate).with_suffix(output_ext))
        return os.path.abspath(candidate)
    video_p = Path(video_path)
    return os.path.abspath(str(video_p.with_name(video_p.stem + "_merged" + output_ext)))


def _cleanup_temp_files(*paths: str) -> None:
    """Remove one or more temporary files, logging any failures.

    Args:
        *paths: File paths to remove.
    """
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.debug("Removed temp file: '%s'", path)
        except OSError as exc:
            logger.warning("Failed to remove temp file '%s': %s", path, exc)


def merge_audio_video(
    video_path: str,
    audio_path: str,
    output_path: Optional[str] = None,
) -> str:
    """Merge a video-only stream with an audio-only stream into one file.

    Attempts to use ``ffmpeg`` first (with automatic codec selection and
    re-encoding when necessary).  If ffmpeg is unavailable, falls back to
    a basic byte-level concatenation.

    Args:
        video_path: Path to the video-only stream file.
        audio_path: Path to the audio-only stream file.
        output_path: Optional destination path.  When ``None``, the
            output file is created next to *video_path* with a
            ``_merged`` suffix and an extension inferred from the inputs.

    Returns:
        Absolute path to the merged output file.

    Raises:
        MergeError: When either input is invalid, the merge fails, or the
            output file cannot be created or verified.
    """
    logger.info(
        "merge_audio_video called: video='%s', audio='%s', output='%s'",
        video_path,
        audio_path,
        output_path or "(auto)",
    )
    _validate_input_file(video_path, "Video file")
    _validate_input_file(audio_path, "Audio file")
    if os.path.abspath(video_path) == os.path.abspath(audio_path):
        raise MergeError(
            "Video and audio paths refer to the same file. "
            "Provide two distinct input files."
        )
    output_ext = _select_output_container(
        _get_file_extension(video_path),
        _get_file_extension(audio_path),
        _get_file_extension(output_path) if output_path else "",
    )
    resolved_output = _determine_output_path(
        output_path, video_path, output_ext
    )
    output_dir = os.path.dirname(resolved_output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    temp_files: list[str] = []
    try:
        if _check_ffmpeg():
            logger.info("ffmpeg is available; using ffmpeg merge path.")
            return _merge_with_ffmpeg(video_path, audio_path, resolved_output)
        logger.warning(
            "ffmpeg is not available. Falling back to basic merge."
        )
        return _merge_basic(video_path, audio_path, resolved_output)
    except MergeError:
        raise
    except Exception as exc:
        raise MergeError(
            f"Unexpected error during merge: {exc}"
        ) from exc
    finally:
        _cleanup_temp_files(*temp_files)


def cleanup_merge_artifacts(output_path: str) -> None:
    """Remove merge temporary files adjacent to *output_path*.

    Looks for files with ``.tmp`` or ``.tmp.<ext>`` suffixes in the same
    directory and removes them.

    Args:
        output_path: Path to the final merged file.
    """
    output_dir = os.path.dirname(os.path.abspath(output_path))
    prefix = os.path.splitext(os.path.basename(output_path))[0]
    removed: list[str] = []
    try:
        for entry in os.listdir(output_dir):
            entry_path = os.path.join(output_dir, entry)
            if not os.path.isfile(entry_path):
                continue
            if entry.startswith(prefix) and (".tmp." in entry or entry.endswith(".tmp")):
                os.remove(entry_path)
                removed.append(entry_path)
    except OSError as exc:
        logger.warning(
            "Could not fully clean up merge artifacts in '%s': %s",
            output_dir,
            exc,
        )
    if removed:
        logger.debug("Cleaned up %d merge temp file(s).", len(removed))
