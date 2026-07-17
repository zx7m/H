"""
File-based caching layer for YouTube player response data.

This module provides a persistent JSON-file cache with TTL support,
keyed by SHA-256 hash of the cache key to ensure valid filenames.
Each cached entry stores its value, TTL override, and creation timestamp.

Typical usage::

    from ytdownloader.cache import CacheManager

    cache = CacheManager(cache_dir=".ytcache", ttl=3600)

    # Try to get a cached player response
    data = cache.get("yt_player_response:abc123")
    if data is None:
        data = fetch_player_response(...)
        cache.set("yt_player_response:abc123", data)

    cache.delete("yt_player_response:abc123")
    cache.clear()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .exceptions import CacheError
from .constants import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL,
    MAX_CACHE_TTL,
    MIN_CACHE_TTL,
)

logger = logging.getLogger(__name__)

_CACHE_FILE_PREFIX: str = "yt_cache_"
_CACHE_FILE_SUFFIX: str = ".json"
_HASH_ALGORITHM: str = "sha256"
_KEY_ENCODING: str = "utf-8"
_JSON_INDENT: int = 2


class CacheManager:
    """Manage a file-based JSON cache with TTL (time-to-live) expiry.

    All cache entries are stored as individual JSON files within a
    dedicated ``cache_dir`` directory. The cache key is hashed with
    SHA-256 to produce a safe, filesystem-friendly filename.

    Args:
        cache_dir: Directory path for storing cache files. Defaults to
            ``DEFAULT_CACHE_DIR`` (``.ytcache``) relative to the current
            working directory when ``None`` is given.
        ttl: Default time-to-live in seconds for entries that do not
            specify their own TTL. Must be between ``MIN_CACHE_TTL``
            (60) and ``MAX_CACHE_TTL`` (86400). Defaults to
            ``DEFAULT_CACHE_TTL`` (3600).

    Raises:
        CacheError: If ``cache_dir`` cannot be created or ``ttl`` is out
            of the accepted range.
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        ttl: int = DEFAULT_CACHE_TTL,
    ) -> None:
        if cache_dir is None:
            cache_dir = DEFAULT_CACHE_DIR

        self._cache_dir: Path = Path(cache_dir)

        if not isinstance(ttl, int) or ttl < MIN_CACHE_TTL or ttl > MAX_CACHE_TTL:
            raise CacheError(
                f"ttl must be an integer between {MIN_CACHE_TTL} and "
                f"{MAX_CACHE_TTL} (got {ttl!r})."
            )
        self._default_ttl: int = ttl

        self._ensure_cache_dir()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Retrieve a value from the cache by its key.

        Args:
            key: Arbitrary string used to look up the cached entry. The
                key is hashed before being used as a filename.

        Returns:
            The cached value, or ``None`` if the key is not found or the
            entry has expired.

        Raises:
            CacheError: If the cache file exists but cannot be read or
                decoded.
        """
        cache_path = self._get_cache_path(key)

        if not cache_path.exists():
            logger.debug("Cache miss (not found): %s", key)
            return None

        try:
            if self._is_expired(cache_path):
                logger.debug("Cache miss (expired): %s", key)
                self.delete(key)
                return None

            with cache_path.open("r", encoding=_KEY_ENCODING) as fh:
                entry: dict[str, Any] = json.load(fh)

            value = entry.get("value")
            logger.debug("Cache hit: %s", key)
            return value

        except (OSError, json.JSONDecodeError) as exc:
            raise CacheError(
                f"Failed to read cache entry for key {key!r}: {exc}"
            ) from exc

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value in the cache under the given key.

        Args:
            key: Arbitrary string used to identify the cache entry.
            value: Any JSON-serialisable value to cache.
            ttl: Optional per-entry TTL in seconds. When ``None`` the
                default TTL configured at construction time is used.

        Raises:
            CacheError: If the value cannot be serialised or written to
                the cache file.
        """
        if ttl is None:
            effective_ttl = self._default_ttl
        else:
            if not MIN_CACHE_TTL <= ttl <= MAX_CACHE_TTL:
                raise CacheError(
                    f"TTL {ttl!r} for key {key!r} is outside the allowed range "
                    f"[{MIN_CACHE_TTL}, {MAX_CACHE_TTL}]"
                )
            effective_ttl = ttl
        cache_path = self._get_cache_path(key)
        timestamp = time.time()

        entry: dict[str, Any] = {
            "key": key,
            "value": value,
            "created_at": timestamp,
            "expires_at": timestamp + effective_ttl,
            "ttl": effective_ttl,
        }

        try:
            json_data = json.dumps(entry, indent=_JSON_INDENT)
        except (TypeError, ValueError) as exc:
            raise CacheError(
                f"Failed to serialise cache value for key {key!r}: {exc}"
            ) from exc

        try:
            tmp_path = cache_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding=_KEY_ENCODING) as fh:
                fh.write(json_data)
            tmp_path.replace(cache_path)
            logger.debug("Cache set: %s (ttl=%ds)", key, effective_ttl)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise CacheError(
                f"Failed to write cache entry for key {key!r}: {exc}"
            ) from exc

    def delete(self, key: str) -> bool:
        """Remove a single cache entry by key.

        Args:
            key: The cache key to delete.

        Returns:
            ``True`` if the entry existed and was removed, ``False`` if
            the key was not found in the cache.

        Raises:
            CacheError: If the cache file cannot be removed.
        """
        cache_path = self._get_cache_path(key)

        if not cache_path.exists():
            logger.debug("Cache delete: key not found (%s)", key)
            return False

        try:
            cache_path.unlink()
            logger.debug("Cache deleted: %s", key)
            return True
        except OSError as exc:
            raise CacheError(
                f"Failed to delete cache entry for key {key!r}: {exc}"
            ) from exc

    def clear(self) -> int:
        """Remove all cache entries.

        Iterates over every file inside ``cache_dir`` matching the
        internal cache filename pattern and removes it.

        Returns:
            The number of cache entries that were removed.

        Raises:
            CacheError: If any individual file cannot be removed.  The
                operation attempts to continue removing remaining files
                even after an error, and the total count of successfully
                removed entries is still returned.
        """
        if not self._cache_dir.exists():
            return 0

        removed = 0
        errors: list[str] = []

        for entry in self._cache_dir.iterdir():
            if entry.is_file() and self._is_cache_file(entry):
                try:
                    entry.unlink()
                    removed += 1
                except OSError as exc:
                    errors.append(str(exc))

        for pattern in ("*.tmp",):
            for tmp_file in self._cache_dir.glob(pattern):
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

        if errors:
            raise CacheError(
                f"Failed to remove {len(errors)} cache file(s) during "
                f"clear: {'; '.join(errors)}"
            )

        logger.debug("Cache cleared (%d entries removed).", removed)
        return removed

    def cache_dir(self) -> Path:
        """Return the path to the cache directory."""
        return self._cache_dir

    def ttl(self) -> int:
        """Return the default TTL in seconds."""
        return self._default_ttl

    def size(self) -> int:
        """Return the number of valid (non-expired) entries in the cache.

        Expired entries are silently removed during counting.

        Returns:
            Count of non-expired cache entries.
        """
        if not self._cache_dir.exists():
            return 0

        count = 0
        for entry in self._cache_dir.iterdir():
            if entry.is_file() and self._is_cache_file(entry):
                if not self._is_expired(entry):
                    count += 1
                else:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
        return count

    def has(self, key: str) -> bool:
        """Return ``True`` if the key exists in the cache and is not expired.

        Args:
            key: The cache key to check.

        Returns:
            ``True`` when the key has a valid, non-expired entry.
        """
        cache_path = self._get_cache_path(key)
        if not cache_path.exists():
            return False
        if self._is_expired(cache_path):
            self.delete(key)
            return False
        return True

    def _load_entry(self, entry: Path) -> tuple[str | None, Any | None, bool]:
        """Load a cache entry once and determine its expiry status.

        Returns:
            A tuple of ``(stored_key, value, is_expired)``.  On any
            error, ``stored_key`` and ``value`` are ``None`` and
            ``is_expired`` is ``True`` so callers can treat the entry as
            invalid/expired.
        """
        try:
            with entry.open("r", encoding=_KEY_ENCODING) as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None, None, True

        stored_key = payload.get("key")
        stored_value = payload.get("value")
        is_expired = self._is_expired(entry, payload=payload)

        return stored_key, stored_value, is_expired

    def keys(self) -> list[str]:
        """Return a list of all non-expired cache keys.

        Returns:
            Sorted list of cache key strings.  Expired entries are
            removed before the list is built.
        """
        if not self._cache_dir.exists():
            return []

        result: list[str] = []
        for entry in self._cache_dir.iterdir():
            if entry.is_file() and self._is_cache_file(entry):
                stored_key, _, is_expired = self._load_entry(entry)
                if is_expired:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
                    continue
                if stored_key is not None:
                    result.append(stored_key)
        result.sort()
        return result

    def items(self) -> list[tuple[str, Any]]:
        """Return a list of ``(key, value)`` tuples for all valid entries.

        Returns:
            List of ``(key, value)`` pairs, sorted by key.  Expired
            entries are silently removed.
        """
        if not self._cache_dir.exists():
            return []

        result: list[tuple[str, Any]] = []
        for entry in self._cache_dir.iterdir():
            if entry.is_file() and self._is_cache_file(entry):
                stored_key, stored_value, is_expired = self._load_entry(entry)
                if is_expired:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
                    continue
                if stored_key is not None:
                    result.append((stored_key, stored_value))
        result.sort(key=lambda pair: pair[0])
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_cache_dir(self) -> None:
        """Create the cache directory if it does not already exist.

        Raises:
            CacheError: If the directory cannot be created.
        """
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CacheError(
                f"Failed to create cache directory {self._cache_dir!s}: {exc}"
            ) from exc

    def _get_cache_path(self, key: str) -> Path:
        """Compute the filesystem path for a given cache key.

        The key is UTF-8 encoded and hashed with SHA-256 to produce a
        deterministic, filesystem-safe filename.

        Args:
            key: The raw cache key string.

        Returns:
            Absolute ``Path`` object pointing to the cache file.
        """
        key_hash = self._hash_key(key)
        filename = f"{_CACHE_FILE_PREFIX}{key_hash}{_CACHE_FILE_SUFFIX}"
        return self._cache_dir / filename

    def _hash_key(self, key: str) -> str:
        """Return the SHA-256 hex digest of the UTF-8 encoded key.

        Args:
            key: Raw cache key string.

        Returns:
            Lowercase hexadecimal SHA-256 digest string.
        """
        return hashlib.new(
            _HASH_ALGORITHM, key.encode(_KEY_ENCODING)
        ).hexdigest()

    def _is_expired(self, cache_path: Path, ttl: int | None = None, payload: dict[str, Any] | None = None) -> bool:
        """Determine whether the cache file at *cache_path* has expired.

        Args:
            cache_path: Path to the cache JSON file.
            ttl: Optional TTL override in seconds.  When ``None`` the
                TTL stored inside the cache entry (or the default TTL
                if the entry format is unexpected) is used.  When
                provided, *ttl* takes precedence over any stored
                ``expires_at`` value.
            payload: Optional already-parsed JSON payload.  When given
                the file is not re-read, avoiding redundant I/O.

        Returns:
            ``True`` if the entry's ``expires_at`` timestamp is in the
            past (or cannot be determined), ``False`` otherwise.
        """
        if payload is None:
            try:
                with cache_path.open("r", encoding=_KEY_ENCODING) as fh:
                    entry: dict[str, Any] = json.load(fh)
            except (OSError, json.JSONDecodeError):
                return True
        else:
            entry = payload

        if ttl is not None:
            try:
                created_at = float(entry.get("created_at", 0))
            except (TypeError, ValueError):
                return True
            expires_at = created_at + float(ttl)
        else:
            try:
                expires_at = float(entry.get("expires_at", 0))
            except (TypeError, ValueError):
                return True

            if expires_at <= 0:
                effective_ttl = entry.get("ttl", self._default_ttl)
                try:
                    created_at = float(entry.get("created_at", 0))
                    expires_at = created_at + float(effective_ttl)
                except (TypeError, ValueError):
                    return True

        return time.time() >= expires_at

    @staticmethod
    def _is_cache_file(path: Path) -> bool:
        """Return ``True`` if *path* looks like a cache file managed by this class.

        Args:
            path: File path to inspect.

        Returns:
            ``True`` when the filename starts with ``_CACHE_FILE_PREFIX``
            and ends with ``_CACHE_FILE_SUFFIX``.
        """
        name = path.name
        return name.startswith(_CACHE_FILE_PREFIX) and name.endswith(_CACHE_FILE_SUFFIX)

    def __repr__(self) -> str:
        """Return a non-mutating representation of this CacheManager."""
        return (
            f"CacheManager(cache_dir={self._cache_dir!s}, "
            f"ttl={self._default_ttl})"
        )

    def __len__(self) -> int:
        return self.size()

    def __contains__(self, key: str) -> bool:
        return self.has(key)
