from __future__ import annotations

import copy
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

from .exceptions import ConfigError
from .logger import get_logger

_logger = get_logger(__name__)


__all__ = [
    "YTConfig",
    "load_config",
    "save_config",
    "get_default_config",
    "apply_env_overrides",
]


SUPPORTED_CONFIG_FORMATS: frozenset[str] = frozenset({".yaml", ".yml", ".json"})


ENV_VAR_MAP: dict[str, str] = {
    "YT_PROXY": "proxy",
    "YT_LOG_LEVEL": "log_level",
    "YT_LOG_FILE": "log_file",
    "YT_OUTPUT_DIR": "output_dir",
    "YT_TIMEOUT": "timeout",
    "YT_MAX_RETRIES": "max_retries",
    "YT_CHUNK_SIZE": "chunk_size",
    "YT_MAX_CONCURRENT_DOWNLOADS": "max_concurrent_downloads",
    "YT_USER_AGENT": "user_agent",
    "YT_COOKIES_FILE": "cookies_file",
    "YT_AUDIO_FORMAT": "audio_format",
    "YT_VIDEO_FORMAT": "video_format",
    "YT_DEFAULT_QUALITY": "default_quality",
}


@dataclass
class YTConfig:
    """Central configuration container for the ytdownloader package.

    Every attribute has a sensible default so callers can create a
    :class:`YTConfig` with no arguments and get a usable configuration.

    Attributes:
        user_agent: HTTP ``User-Agent`` header sent with every request.
        headers: Additional HTTP headers merged into the default set.
        timeout: HTTP request timeout in seconds.  Must be greater than zero.
        max_retries: Number of times a failed request is retried.
        retry_delay_base: Base delay (seconds) for exponential backoff.
        chunk_size: Number of bytes read per chunk during streaming downloads.
        output_dir: Directory where downloaded files are written.
        audio_format: Default container format for audio-only downloads.
        video_format: Default container format for video downloads.
        default_quality: Preferred quality label (e.g. ``"720p"``, ``"best"``).
        proxy: Optional HTTP/HTTPS proxy URL (``None`` disables the proxy).
        cookies_file: Path to a Netscape-format cookies file, or ``None``.
        log_level: Minimum severity for log messages (e.g. ``"INFO"``).
        log_file: Optional file path for persistent log output.
        download_resume: ``True`` to resume partial downloads when possible.
        max_concurrent_downloads: Upper limit on simultaneous download threads.
    """

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    max_retries: int = 3
    retry_delay_base: float = 1.0
    chunk_size: int = 1024 * 1024  # 1 MB
    output_dir: str = "."
    audio_format: str = "mp3"
    video_format: str = "mp4"
    default_quality: str = "best"
    proxy: str | None = None
    cookies_file: str | None = None
    log_level: str = "INFO"
    log_file: str | None = None
    download_resume: bool = True
    max_concurrent_downloads: int = 3


def _validate_config(config: YTConfig) -> None:
    """Validate the logical consistency of a :class:`YTConfig` instance.

    Args:
        config: The configuration object to validate.

    Raises:
        ConfigError: If any field has an invalid or self-contradictory value.
    """
    errors: list[str] = []

    if config.timeout <= 0:
        errors.append(f"timeout must be > 0 (got {config.timeout}).")

    if config.chunk_size <= 0:
        errors.append(f"chunk_size must be > 0 (got {config.chunk_size}).")

    if config.max_retries < 0:
        errors.append(f"max_retries must be >= 0 (got {config.max_retries}).")

    if config.retry_delay_base < 0:
        errors.append(f"retry_delay_base must be >= 0 (got {config.retry_delay_base}).")

    if config.max_concurrent_downloads <= 0:
        errors.append(
            f"max_concurrent_downloads must be > 0 (got {config.max_concurrent_downloads})."
        )

    valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    level_upper = config.log_level.upper()
    if level_upper not in valid_log_levels:
        errors.append(
            f"log_level must be one of {sorted(valid_log_levels)} "
            f"(got '{config.log_level}')."
        )

    valid_audio_formats = {"mp3", "m4a", "wav", "flac", "opus", "aac", "weba"}
    if config.audio_format not in valid_audio_formats:
        errors.append(
            f"audio_format must be one of {sorted(valid_audio_formats)} "
            f"(got '{config.audio_format}')."
        )

    valid_video_formats = {"mp4", "webm", "flv", "3gp", "m4a"}
    if config.video_format not in valid_video_formats:
        errors.append(
            f"video_format must be one of {sorted(valid_video_formats)} "
            f"(got '{config.video_format}')."
        )

    if config.proxy is not None and not config.proxy.strip():
        errors.append("proxy must be a non-empty string when provided.")

    if config.cookies_file is not None:
        cookie_path = Path(config.cookies_file)
        if not cookie_path.is_absolute():
            cookie_path = Path.cwd() / cookie_path
        if not cookie_path.exists():
            errors.append(
                f"cookies_file '{config.cookies_file}' does not exist."
            )

    output = Path(config.output_dir)
    if not output.exists():
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(
                f"Cannot create output_dir '{config.output_dir}': {exc}"
            )

    if errors:
        joined = " ".join(errors)
        raise ConfigError(f"Configuration validation failed: {joined}")


def get_default_config() -> YTConfig:
    """Return a :class:`YTConfig` populated with sensible package defaults.

    Returns:
        A fresh :class:`YTConfig` instance with all fields set to their
        default values.
    """
    return YTConfig()


def apply_env_overrides(config: YTConfig) -> YTConfig:
    """Apply environment variable overrides to a :class:`YTConfig`.

    Each recognised ``YT_*`` environment variable overrides the
    corresponding field when the variable is present in the process
    environment.  Numeric fields (``timeout``, ``max_retries``, etc.)
    are coerced from strings automatically; invalid values are silently
    ignored and a warning is logged.

    Args:
        config: The base configuration to mutate.

    Returns:
        The same (mutated) :class:`YTConfig` instance, populated with
        environment-supplied values where applicable.
    """
    updated = copy.deepcopy(config)

    for env_var, field_name in ENV_VAR_MAP.items():
        raw_value = os.environ.get(env_var)
        if raw_value is None or raw_value.strip() == "":
            continue

        current = getattr(updated, field_name)

        if isinstance(current, bool):
            setattr(updated, field_name, raw_value.lower() not in {"0", "false", "no", ""})
            continue

        if isinstance(current, int):
            try:
                parsed = int(raw_value)
            except ValueError:
                _logger.warning(
                    "Env var %s=%r is not a valid integer; ignoring.", env_var, raw_value
                )
                continue
            setattr(updated, field_name, parsed)
            continue

        if isinstance(current, float):
            try:
                parsed = float(raw_value)
            except ValueError:
                _logger.warning(
                    "Env var %s=%r is not a valid float; ignoring.", env_var, raw_value
                )
                continue
            setattr(updated, field_name, parsed)
            continue

        setattr(updated, field_name, raw_value)

    return updated


def _parse_config_data(raw: Any) -> dict[str, Any]:
    """Normalise raw configuration data into a plain dictionary.

    Accepts either a ``dict`` or a JSON string.

    Args:
        raw: Raw configuration data to parse.

    Returns:
        A plain ``dict`` suitable for passing to :class:`YTConfig`.

    Raises:
        ConfigError: If *raw* is a string that is not valid JSON.
        TypeError: If *raw* is neither a dict nor a string.
    """
    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        raw_stripped = raw.strip()
        if not raw_stripped:
            return {}
        try:
            parsed = json.loads(raw_stripped)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Config string is not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ConfigError(
                f"Parsed config JSON must be a mapping, got {type(parsed).__name__}."
            )
        return parsed

    raise TypeError(
        f"Config data must be a dict or a JSON string; got {type(raw).__name__}."
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load configuration data from a YAML file.

    Args:
        path: Filesystem path to the YAML file.

    Returns:
        A plain ``dict`` parsed from the YAML content.

    Raises:
        ConfigError: If PyYAML is not installed or the file cannot be parsed.
    """
    if not _YAML_AVAILABLE:
        raise ConfigError(
            "PyYAML is required to load YAML config files. "
            "Install it with: pip install pyyaml"
        )
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML config '{path}': {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read config file '{path}': {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"YAML config root must be a mapping; got {type(data).__name__}."
        )
    return data


def _load_json(path: Path) -> dict[str, Any]:
    """Load configuration data from a JSON file.

    Args:
        path: Filesystem path to the JSON file.

    Returns:
        A plain ``dict`` parsed from the JSON content.

    Raises:
        ConfigError: If the file cannot be read or is not valid JSON.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ConfigError(f"Cannot read config file '{path}': {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config file '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"JSON config root must be a mapping; got {type(data).__name__}."
        )
    return data


def load_config(config_path: str | Path | None = None) -> YTConfig:
    """Load configuration from a file and apply environment variable overrides.

    The search order when *config_path* is ``None`` is:

    1. ``YT_CONFIG`` environment variable (if set).
    2. ``./ytdownloader.yaml``
    3. ``./ytdownloader.yml``
    4. ``./ytdownloader.json``

    When *config_path* is provided it is used directly regardless of
    whether the file exists on disk.

    The loaded data is applied on top of :func:`get_default_config` so
    only fields present in the file (or set via environment variables)
    override the defaults.  After loading, :func:`apply_env_overrides`
    is called so environment variables always take precedence over file
    values.

    Args:
        config_path: Optional explicit path to a YAML or JSON config file.
            Pass ``None`` to use the search order described above.

    Returns:
        A fully-populated and validated :class:`YTConfig` instance.

    Raises:
        ConfigError: If the config file cannot be found, parsed, or fails
            validation.
    """
    config = get_default_config()

    resolved_path: Path | None = None

    if config_path is not None:
        resolved_path = Path(config_path)
    else:
        env_configured = os.environ.get("YT_CONFIG")
        if env_configured:
            resolved_path = Path(env_configured)
        else:
            for candidate in ("ytdownloader.yaml", "ytdownloader.yml", "ytdownloader.json"):
                p = Path(candidate)
                if p.is_file():
                    resolved_path = p
                    break

    if resolved_path is not None:
        if not resolved_path.exists():
            raise ConfigError(f"Config file not found: {resolved_path}")

        suffix = resolved_path.suffix.lower()
        _logger.debug("Loading config from %s", resolved_path)

        if suffix in (".yaml", ".yml"):
            file_data = _load_yaml(resolved_path)
        elif suffix == ".json":
            file_data = _load_json(resolved_path)
        else:
            raise ConfigError(
                f"Unsupported config file extension '{suffix}'. "
                f"Use one of {sorted(SUPPORTED_CONFIG_FORMATS)}."
            )

        for key, value in file_data.items():
            if hasattr(config, key):
                setattr(config, key, value)
            else:
                _logger.warning("Unknown config key '%s' ignored.", key)

    config = apply_env_overrides(config)
    _validate_config(config)
    _logger.debug("Config loaded and validated successfully.")
    return config


def save_config(config: YTConfig, config_path: str | Path) -> None:
    """Persist a :class:`YTConfig` to a YAML or JSON file.

    The output format is determined by the file extension of *config_path*:
    ``.yaml`` / ``.yml`` writes YAML (requires PyYAML); ``.json`` writes
    JSON.  Parent directories are created automatically.

    Args:
        config: The configuration object to serialise.
        config_path: Destination file path.  The extension determines the
            output format.

    Raises:
        ConfigError: If PyYAML is not installed for a YAML target, or if
            the file cannot be written.
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _config_to_dict(config)
    suffix = path.suffix.lower()

    try:
        if suffix in (".yaml", ".yml"):
            if not _YAML_AVAILABLE:
                raise ConfigError(
                    "PyYAML is required to save YAML config files. "
                    "Install it with: pip install pyyaml"
                )
            with path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=True)
        elif suffix == ".json":
            with path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
        else:
            raise ConfigError(
                f"Unsupported config file extension '{suffix}'. "
                f"Use one of {sorted(SUPPORTED_CONFIG_FORMATS)}."
            )
    except OSError as exc:
        raise ConfigError(f"Cannot write config file '{path}': {exc}") from exc

    _logger.debug("Config saved to %s.", path)


def _config_to_dict(config: YTConfig) -> dict[str, Any]:
    """Serialise a :class:`YTConfig` to a plain dictionary.

    Args:
        config: The configuration object to serialise.

    Returns:
        A ``dict`` representation of *config* with all public fields included.
    """
    return {
        "user_agent": config.user_agent,
        "headers": dict(config.headers),
        "timeout": config.timeout,
        "max_retries": config.max_retries,
        "retry_delay_base": config.retry_delay_base,
        "chunk_size": config.chunk_size,
        "output_dir": config.output_dir,
        "audio_format": config.audio_format,
        "video_format": config.video_format,
        "default_quality": config.default_quality,
        "proxy": config.proxy,
        "cookies_file": config.cookies_file,
        "log_level": config.log_level,
        "log_file": config.log_file,
        "download_resume": config.download_resume,
        "max_concurrent_downloads": config.max_concurrent_downloads,
    }
