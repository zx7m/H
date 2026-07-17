"""
JavaScript n-parameter resolver for YouTube stream URLs.

YouTube embeds a navigator token (the ``n`` parameter) inside encrypted
stream URLs.  The value of ``n`` is the *name* of a JS function defined
in the YouTube player JS bundle that transforms the URL path.  Without
resolving that function the server rejects the request.

This module re-implements the relevant JS algorithm in Python without
depending on ``yt-dlp`` or ``js2py``.  The resolver works in three phases:

1. **Locate** the YouTube player JS bundle URL by fetching the watch
   page and parsing ``ytcfg``.
2. **Extract** the named function source from the bundle via regex.
3. **Interpret** the extracted JS body as a sequence of primitive string
   operations (swap, reverse, slice) that are translated into Python.

If any step fails a safe fallback is used: the URL is returned without
the n parameter so the caller can attempt a best-effort download.

Typical usage::

    from ytdownloader.n_resolver import NResolver

    resolver = NResolver(http_client)
    resolved_url = resolver.resolve_n("A_vI6Ix_3g", "https://manifest.googlevideo.com/...")

"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from .exceptions import NResolverError, NetworkError, YTDLException
from .logger import get_logger

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

_logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of bytes of player JS to download and search.
_MAX_JS_BODY_SIZE: int = 5 * 1024 * 1024  # 5 MB

#: Maximum number of bytes of the named function body to extract.
_MAX_FUNC_BODY_SIZE: int = 8 * 192

#: User-agent header used when fetching player JS.
_PLAYER_JS_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Compiled regex patterns used for JS parsing
# ---------------------------------------------------------------------------

# Matches the ytcfg.set({"PLAYER_JS_URL": "..."}) pattern inside HTML/JS.
_RE_PLAYER_JS_URL_CFG: re.Pattern = re.compile(
    r'"PLAYER_JS_URL"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

# Matches s.assets = {...} or the assets object containing js.
_RE_PLAYER_ASSETS: re.Pattern = re.compile(
    r'"assets"\s*:\s*\{[^}]*"js"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

# Matches a function declaration of the form: func.Name(p1, p2) { ... }
# Captures the full function body including the leading signature.
_RE_FUNC_DECL: re.Pattern = re.compile(
    r"function\s+%s\s*\([^)]*\)\s*\{",
    re.IGNORECASE,
)

# Matches assignment form: a.Name = function(p1, p2) { ... }
_RE_FUNC_ASSIGN: re.Pattern = re.compile(
    r"[.\w]+\s*=\s*function\s*\([^)]*\)\s*\{",
    re.IGNORECASE,
)

# Matches character-index access: e = e[a % e.length] (or similar).
_RE_CHAR_INDEX: re.Pattern = re.compile(
    r"([a-zA-Z_$][\w$]*)\s*=\s*([a-zA-Z_$][\w$]*)\s*\[\s*([a-zA-Z_$][\w$]*)\s*%\s*\3\.length\s*\]",
    re.IGNORECASE,
)

# Matches simple reversal: a = a.split("").reverse().join("")
_RE_REVERSE: re.Pattern = re.compile(
    r"\.split\s*\(\s*['\"]\s*['\"]\s*\)\s*\.reverse\s*\(\s*\)\s*\.join\s*\(\s*['\"]([^'\"]*)['\"]\s*\)",
    re.IGNORECASE,
)

# Matches swap via a temporary variable of the form:
#   var t = a; a = b; b = t;
# or   let t = a; a = b; b = t;
_RE_SWAP_BLOCK: re.Pattern = re.compile(
    r"(?:var|let|const)\s+(\w+)\s*=\s*([^;]+);\s*\2\s*=\s*([^;]+);\s*\2\s*=\s*\1",
    re.IGNORECASE,
)

# Matches splice of the form: a.splice(b, c, ...)
_RE_SPLICE: re.Pattern = re.compile(
    r"\.splice\s*\(\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([^)]*))?\s*\)",
    re.IGNORECASE,
)

# Matches a slice call: a.slice(b, c)
_RE_SLICE: re.Pattern = re.compile(
    r"\.slice\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helper types
# ---------------------------------------------------------------------------


class _JSFunction:
    """Parsed representation of a JavaScript function body.

    Attributes:
        name: Function name as it appears in the JS source.
        source: Raw function source text (from ``function`` keyword to
            the matching closing ``}``).
        operations: List of operation tuples derived from the source.
            Each tuple has the form ``(op_name, *args)`` where ``op_name``
            is one of ``"reverse"``, ``"swap"``, ``"slice"``, ``"splice"``,
            ``"set_char"``, ``"unknown"``.
    """

    def __init__(self, name: str, source: str) -> None:
        """Initialise with raw source text.

        Args:
            name: JavaScript function name.
            source: Raw source text of the function.
        """
        self.name: str = name
        self.source: str = source
        self.operations: list[tuple[str, ...]] = []
        self._parse()

    def _parse(self) -> None:
        """Parse the JS source into a list of high-level operations."""
        src = self.source

        if _RE_REVERSE.search(src):
            match = _RE_REVERSE.search(src)
            sep = match.group(1) if match.group(1) else ""
            self.operations.append(("reverse", sep))
            src = _RE_REVERSE.sub("", src)

        if _RE_SWAP_BLOCK.search(src):
            for swap_match in _RE_SWAP_BLOCK.finditer(src):
                tmp_var = swap_match.group(1)
                expr_a = swap_match.group(2).strip()
                expr_b = swap_match.group(3).strip()
                self.operations.append(("swap", expr_a, expr_b, tmp_var))
                src = _RE_SWAP_BLOCK.sub("", src, count=1)

        if _RE_SPLICE.search(src):
            for sp_match in _RE_SPLICE.finditer(src):
                idx = int(sp_match.group(1))
                count = int(sp_match.group(2))
                items_str = (sp_match.group(3) or "").strip()
                self.operations.append(("splice", idx, count, items_str))
                src = _RE_SPLICE.sub("", src, count=1)

        if _RE_SLICE.search(src):
            for sl_match in _RE_SLICE.finditer(src):
                start = int(sl_match.group(1))
                end = int(sl_match.group(2))
                self.operations.append(("slice", start, end))
                src = _RE_SLICE.sub("", src, count=1)

        if _RE_CHAR_INDEX.search(src):
            for ci_match in _RE_CHAR_INDEX.finditer(src):
                var_name = ci_match.group(1)
                src_name = ci_match.group(2)
                idx_name = ci_match.group(3)
                self.operations.append(("set_char", var_name, src_name, idx_name))
                src = _RE_CHAR_INDEX.sub("", src, count=1)

        if not self.operations:
            self.operations.append(("unknown", src.strip()[:200]))


# ---------------------------------------------------------------------------
# NResolverError (thin re-export of the canonical exception)
# ---------------------------------------------------------------------------

# NResolverError is already defined in exceptions.py; we re-export it here
# so callers can do ``from ytdownloader.n_resolver import NResolverError``.
from .exceptions import NResolverError  # noqa: E402  (re-export)


# ---------------------------------------------------------------------------
# NResolver
# ---------------------------------------------------------------------------


class NResolver:
    """Resolve the YouTube ``n`` navigator parameter without a JS engine.

    YouTube appends an ``n`` query parameter to stream URLs.  The value is
    the name of a JavaScript function defined in the player JS bundle that
    transforms the URL path.  :class:`NResolver` fetches the player JS,
    extracts the named function, interprets its algorithm in Python, and
    applies it to the URL path.

    Results are cached so repeated calls with the same ``(n_value,
    base_url)`` pair do not require re-fetching or re-parsing.

    If resolution fails for any reason the
    :attr:`fallback_behavior` policy is applied: the URL is returned
    without the ``n`` parameter (best-effort mode).

    Attributes:
        http_client: An :class:`~ytdownloader.http_client.HttpClient`
            instance used to fetch the player JS and watch page.
        fallback_behavior: One of ``"strip_n"`` (default) or
            ``"raise"``.  When ``"strip_n"`` the ``n`` parameter is
            removed from the URL on failure; when ``"raise"`` the
            original error is propagated.
        max_cache_size: Maximum number of resolved ``n`` values to keep
            in the in-memory cache.
        _cache: Internal LRU-style cache mapping
            ``(n_value, path) -> resolved_str``.
        _player_js_cache: Cached raw player JS body, keyed by URL.
    """

    def __init__(self, http_client: Any) -> None:
        """Initialise the resolver with an HTTP client.

        Args:
            http_client: A configured
                :class:`~ytdownloader.http_client.HttpClient` instance.
        """
        self._http_client = http_client
        self._cache: dict[tuple[str, str], str] = {}
        self._player_js_cache: dict[str, str] = {}
        self._logger = _logger
        self._max_cache_size: int = 256
        self.fallback_behavior: str = "strip_n"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_n(self, n_value: str, base_url: str) -> str:
        """Resolve the n-parameter for *base_url*.

        Applies the JavaScript function named *n_value* to the path
        component of *base_url* and returns the resulting URL with the
        ``n`` query parameter set to the computed value.

        Results are cached: repeated calls with the same
        ``(n_value, base_url)`` pair return the cached result without
        network I/O.

        Args:
            n_value: Name of the JavaScript function (e.g.
                ``"A_vI6Ix_3g"``).
            base_url: The YouTube stream URL that requires the n
                parameter.  Must be a well-formed absolute URL.

        Returns:
            The *base_url* with the ``n`` query parameter added and set
            to the computed value.  If resolution fails and
            :attr:`fallback_behavior` is ``"strip_n"`` the ``n``
            parameter is removed from the URL (best-effort fallback).

        Raises:
            NResolverError: If resolution fails and
                :attr:`fallback_behavior` is ``"raise"``.
            ValueError: If *base_url* is not a valid absolute URL.
        """
        if not n_value or not n_value.strip():
            self._logger.warning("Empty n_value provided; returning base_url unchanged.")
            return base_url

        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"base_url must be a valid absolute URL; got: {base_url!r}"
            )

        path = parsed.path

        cache_key = (n_value, path)
        if cache_key in self._cache:
            self._logger.debug(
                "n-resolver cache hit for n=%r path=%r", n_value, path[:80]
            )
            return self._apply_n_param(base_url, self._cache[cache_key])

        try:
            result = self._resolve_impl(n_value, base_url, parsed)
            self._cache[cache_key] = result
            if len(self._cache) > self._max_cache_size:
                self._evict_cache()
            return self._apply_n_param(base_url, result)
        except NResolverError:
            raise
        except Exception as exc:
            self._logger.warning(
                "n-resolver unexpected error for n=%r: %s", n_value, exc
            )
            if self.fallback_behavior == "raise":
                raise NResolverError(
                    f"Failed to resolve n parameter '{n_value}'.",
                    cause=exc,
                ) from exc
            return self._strip_n_param(base_url)

    def clear_cache(self) -> None:
        """Clear all cached n-resolved values and player JS bodies."""
        self._cache.clear()
        self._player_js_cache.clear()
        self._logger.debug("NResolver caches cleared.")

    @property
    def cache_size(self) -> int:
        """Number of entries in the n-value resolution cache."""
        return len(self._cache)

    # ------------------------------------------------------------------
    # Private implementation
    # ------------------------------------------------------------------

    def _resolve_impl(
        self, n_value: str, base_url: str, parsed: Any
    ) -> str:
        """Core resolution logic.

        Args:
            n_value: JavaScript function name.
            base_url: Original stream URL.
            parsed: Pre-parsed :class:`urllib.parse.ParseResult`.

        Returns:
            The computed n-parameter string (raw, without the ``n=``
            prefix).

        Raises:
            NResolverError: If resolution cannot be completed.
        """
        video_id = self._extract_video_id(base_url)
        if not video_id:
            raise NResolverError(
                "Cannot determine video ID from base_url; "
                "player JS cannot be fetched."
            )

        player_js_url = self._get_player_js_url(video_id)
        if not player_js_url:
            raise NResolverError(
                "Could not locate PLAYER_JS_URL in ytcfg for video "
                f"{video_id!r}."
            )

        js_body = self._fetch_player_js(player_js_url)
        if not js_body:
            raise NResolverError(
                f"Player JS body is empty for URL: {player_js_url}."
            )

        func = self._extract_function(js_body, n_value)
        if func is None:
            raise NResolverError(
                f"JavaScript function '{n_value}' not found in player JS."
            )

        path_str = parsed.path
        n_computed = self._compute_n(func, path_str)
        if n_computed is None:
            raise NResolverError(
                f"Failed to compute n-value from function '{n_value}' "
                f"for path '{path_str}'."
            )

        self._logger.debug(
            "Resolved n='%s' for n_value='%s' (video_id=%s).",
            n_computed[:16] + "..." if len(n_computed) > 16 else n_computed,
            n_value,
            video_id,
        )
        return n_computed

    def _apply_n_param(self, url: str, n_value: str) -> str:
        """Return *url* with the ``n`` query parameter set to *n_value*.

        Existing ``n`` parameters are replaced; other query parameters are
        preserved.

        Args:
            url: The URL to modify.
            n_value: The resolved n-parameter value.

        Returns:
            URL with ``n=<n_value>`` in the query string.
        """
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["n"] = [n_value]
        new_query = urlencode({k: v[0] for k, v in query.items()})
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    def _strip_n_param(self, url: str) -> str:
        """Return *url* with the ``n`` query parameter removed.

        Args:
            url: The URL to strip the n parameter from.

        Returns:
            URL without the ``n`` parameter in the query string.
        """
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query.pop("n", None)
        new_query = urlencode({k: v[0] for k, v in query.items()})
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    # ------------------------------------------------------------------
    # Video ID extraction
    # ------------------------------------------------------------------

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract the YouTube video ID from *url*.

        Handles standard watch URLs, shorts URLs, embed URLs, and
        youtu.be short URLs.

        Args:
            url: A YouTube URL of any recognised form.

        Returns:
            The 11-character video ID, or ``None`` if not found.
        """
        patterns = [
            r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|v/))"
            r"([a-zA-Z0-9_-]{11})",
            r"youtu\.be/([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    # ------------------------------------------------------------------
    # Player JS URL discovery
    # ------------------------------------------------------------------

    def _get_player_js_url(self, video_id: str) -> Optional[str]:
        """Fetch the YouTube watch page and extract the player JS URL.

        The player JS URL is embedded inside the ``ytcfg`` object on the
        watch page under the ``PLAYER_JS_URL`` key.  The value is a
        relative path such as ``/s/player/abcd1234/player_ias.vflset/...``
        which is resolved against ``https://www.youtube.com``.

        Args:
            video_id: The 11-character YouTube video ID.

        Returns:
            Absolute URL of the player JS bundle, or ``None`` if not
            found.
        """
        watch_url = (
            f"https://www.youtube.com/watch?v={video_id}"
        )
        try:
            response = self._http_client.get(
                watch_url,
                headers={"User-Agent": _PLAYER_JS_USER_AGENT},
                timeout=20,
            )
            html_body = response.text
        except Exception as exc:
            self._logger.warning(
                "Failed to fetch watch page for video_id=%s: %s",
                video_id,
                exc,
            )
            return None

        match = _RE_PLAYER_JS_URL_CFG.search(html_body)
        if match:
            relative_url = match.group(1)
            absolute_url = _resolve_youtube_relative(relative_url)
            self._logger.debug(
                "Found PLAYER_JS_URL via cfg regex: %s", absolute_url
            )
            return absolute_url

        match = _RE_PLAYER_ASSETS.search(html_body)
        if match:
            relative_url = match.group(1)
            absolute_url = _resolve_youtube_relative(relative_url)
            self._logger.debug(
                "Found PLAYER_JS_URL via assets regex: %s", absolute_url
            )
            return absolute_url

        self._logger.warning(
            "PLAYER_JS_URL not found in HTML for video_id=%s.", video_id
        )
        return None

    # ------------------------------------------------------------------
    # Player JS fetching
    # ------------------------------------------------------------------

    def _fetch_player_js(self, player_js_url: str) -> str:
        """Download and return the raw player JS body.

        Results are cached in :attr:`_player_js_cache` so the same URL
        is only fetched once per resolver instance.

        Args:
            player_js_url: Absolute URL of the player JS bundle.

        Returns:
            The raw JavaScript source text, or an empty string if the
            request fails.
        """
        if player_js_url in self._player_js_cache:
            return self._player_js_cache[player_js_url]

        self._logger.debug("Fetching player JS: %s", player_js_url)
        try:
            response = self._http_client.get(
                player_js_url,
                headers={"User-Agent": _PLAYER_JS_USER_AGENT},
                timeout=30,
            )
            js_body = response.text
        except Exception as exc:
            self._logger.warning(
                "Failed to fetch player JS at %s: %s", player_js_url, exc
            )
            self._player_js_cache[player_js_url] = ""
            return ""

        if not js_body:
            self._logger.warning(
                "Player JS response body is empty for %s.", player_js_url
            )
            self._player_js_cache[player_js_url] = ""
            return ""

        self._player_js_cache[player_js_url] = js_body
        self._logger.debug(
            "Cached %d bytes of player JS from %s.",
            len(js_body),
            player_js_url,
        )
        return js_body

    # ------------------------------------------------------------------
    # Function extraction
    # ------------------------------------------------------------------

    def _extract_function(
        self, js_code: str, func_name: str
    ) -> Optional[_JSFunction]:
        """Locate and parse the named JavaScript function from *js_code*.

        Searches for the function declaration
        ``function <func_name>(...) { ... }`` first, then falls back to
        an assignment pattern such as ``a.<func_name> = function(...)``.

        Args:
            js_code: Full raw player JS source text.
            func_name: The JavaScript function name to locate.

        Returns:
            A :class:`_JSFunction` instance with parsed operations, or
            ``None`` if the function cannot be located.
        """
        decl_pattern = re.compile(
            r"function\s+" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{",
            re.IGNORECASE,
        )
        match = decl_pattern.search(js_code)
        if match:
            body_start = match.start()
            body_text = _extract_balanced_braces(js_code, body_start)
            if body_text:
                self._logger.debug(
                    "Extracted function '%s' via declaration (len=%d).",
                    func_name,
                    len(body_text),
                )
                return _JSFunction(func_name, body_text)

        assign_pattern = re.compile(
            r"(?:^|\.|;|\s)" + re.escape(func_name) + r"\s*=\s*function\s*\([^)]*\)\s*\{",
            re.IGNORECASE,
        )
        match = assign_pattern.search(js_code)
        if match:
            body_start = match.start()
            brace_pos = js_code.find("{", match.end() - 1)
            if brace_pos >= 0:
                body_text = _extract_balanced_braces(js_code, brace_pos)
                if body_text:
                    self._logger.debug(
                        "Extracted function '%s' via assignment (len=%d).",
                        func_name,
                        len(body_text),
                    )
                    return _JSFunction(func_name, body_text)

        self._logger.warning(
            "Function '%s' not found in player JS source.", func_name
        )
        return None

    # ------------------------------------------------------------------
    # n-value computation
    # ------------------------------------------------------------------

    def _compute_n(
        self, func: _JSFunction, input_str: str
    ) -> Optional[str]:
        """Apply the parsed JS function operations to *input_str*.

        Each operation in :attr:`_JSFunction.operations` is executed in
        sequence on the working string.

        Args:
            func: Parsed JavaScript function.
            input_str: The URL path to transform (e.g.
                ``/videoplayback?id=...``).

        Returns:
            The transformed string, or ``None`` if computation fails.
        """
        s = input_str
        try:
            for op in func.operations:
                op_name = op[0]
                if op_name == "reverse":
                    sep = op[1] if len(op) > 1 else ""
                    parts = s.split(sep)
                    parts.reverse()
                    s = sep.join(parts)
                elif op_name == "swap":
                    expr_a = op[1]
                    expr_b = op[2]
                    tmp_var = op[3]
                    self._logger.debug(
                        "Skipping swap operation (expr_a=%r, expr_b=%r) "
                        "– variable swap not applicable to string n-value.",
                        expr_a,
                        expr_b,
                    )
                elif op_name == "slice":
                    start = op[1]
                    end = op[2]
                    s = s[start:end]
                elif op_name == "splice":
                    idx = op[1]
                    count = op[2]
                    items_str = op[3] if len(op) > 3 else ""
                    char_list = list(s)
                    char_list[idx: idx + count] = _parse_splice_items(
                        items_str, s
                    )
                    s = "".join(char_list)
                elif op_name == "set_char":
                    var_name = op[1]
                    src_name = op[2]
                    idx_name = op[3]
                    self._logger.debug(
                        "Skipping set_char operation (var=%r) – "
                        "dynamic character assignment.",
                        var_name,
                    )
                elif op_name == "unknown":
                    raw = op[1] if len(op) > 1 else ""
                    self._logger.debug(
                        "Unknown JS operation encountered: %s", raw[:120]
                    )
                else:
                    self._logger.debug(
                        "Unhandled operation %r - skipped.", op_name
                    )
            return s
        except Exception as exc:
            self._logger.warning(
                "Error during _compute_n for function '%s': %s",
                func.name,
                exc,
            )
            return None

    def _evict_cache(self) -> None:
        """Evict roughly half of the cache entries to free memory."""
        keys = list(self._cache.keys())
        half = len(keys) // 2
        for key in keys[:half]:
            del self._cache[key]
        self._logger.debug("Evicted %d entries from n-resolver cache.", half)

    def __repr__(self) -> str:
        """Return a developer-friendly representation of the resolver."""
        return (
            f"NResolver("
            f"cache_size={self.cache_size}, "
            f"player_js_cached={len(self._player_js_cache)}, "
            f"fallback={self.fallback_behavior!r})"
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _resolve_youtube_relative(relative_url: str) -> str:
    """Resolve a relative YouTube asset URL to an absolute HTTPS URL.

    Args:
        relative_url: Path such as ``/s/player/abc123/player_ias.vflset/...``
            or ``//yt3.ggpht.com/...``.

    Returns:
        An absolute ``https://www.youtube.com`` or ``https://yt3.ggpht.com``
        URL.
    """
    if relative_url.startswith("//"):
        return "https:" + relative_url
    if relative_url.startswith("http://") or relative_url.startswith("https://"):
        return relative_url
    return "https://www.youtube.com" + relative_url


def _extract_balanced_braces(source: str, start: int) -> str:
    """Extract the balanced ``{ ... }`` block starting at *start*.

    The character at *start* must be the ``{`` that opens the block.
    Braces inside string literals are ignored.

    Args:
        source: Full source text.
        start: Index of the opening ``{``.

    Returns:
        The matched block including the outer braces, or an empty
        string if no matching closing brace is found.
    """
    if start < 0 or start >= len(source) or source[start] != "{":
        return ""
    depth = 0
    in_string = False
    string_char = ""
    i = start
    while i < len(source):
        ch = source[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == string_char:
                in_string = False
        else:
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[start: i + 1]
        i += 1
    return ""


def _parse_splice_items(items_str: str, original: str) -> list[str]:
    """Parse the items string from a ``splice`` call into a list of chars.

    The ``items_str`` is a raw JS expression fragment (e.g.
    ``"a", "b", c[0]``).  We extract quoted literals and ignore
    complex expressions, falling back to a single space character for
    anything we cannot parse.

    Args:
        items_str: Raw items string from the splice regex capture.
        original: The original string being modified (used only for
            context; not currently needed).

    Returns:
        List of single-character strings to splice into the target.
    """
    if not items_str or not items_str.strip():
        return []

    chars: list[str] = []
    for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'', items_str):
        val = m.group(1) if m.group(1) is not None else m.group(2)
        if val:
            chars.extend(val)

    if not chars:
        chars = [" "]

    return chars


def _interpret_n_function(
    js_source: str,
    func_name: str,
    input_str: str,
) -> Optional[str]:
    """Standalone helper to interpret an n-function without a resolver.

    Primarily intended for unit tests and one-off lookups.  Fetches no
    player JS; the full source must be provided directly.

    Args:
        js_source: Raw player JS source text.
        func_name: Name of the function to locate.
        input_str: The URL path string to transform.

    Returns:
        The transformed string, or ``None`` if the function cannot be
        located or interpreted.
    """
    func = _extract_function_static(js_source, func_name)
    if func is None:
        return None

    resolver = NResolver.__new__(NResolver)
    resolver._cache = {}
    resolver._player_js_cache = {}
    resolver._logger = _logger
    resolver._max_cache_size = 256
    resolver.fallback_behavior = "raise"
    resolver._http_client = None  # type: ignore[assignment]
    return resolver._compute_n(func, input_str)


def _extract_function_static(
    js_code: str, func_name: str
) -> Optional[_JSFunction]:
    """Locate and parse *func_name* from *js_code* without HTTP access.

    Args:
        js_code: Full raw player JS source text.
        func_name: JavaScript function name to locate.

    Returns:
        A :class:`_JSFunction` or ``None``.
    """
    decl_pattern = re.compile(
        r"function\s+" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{",
        re.IGNORECASE,
    )
    match = decl_pattern.search(js_code)
    if match:
        body_text = _extract_balanced_braces(js_code, match.start())
        if body_text:
            return _JSFunction(func_name, body_text)

    assign_pattern = re.compile(
        r"(?:^|\.|;|\s)" + re.escape(func_name) + r"\s*=\s*function\s*\([^)]*\)\s*\{",
        re.IGNORECASE,
    )
    match = assign_pattern.search(js_code)
    if match:
        brace_pos = js_code.find("{", match.end() - 1)
        if brace_pos >= 0:
            body_text = _extract_balanced_braces(js_code, brace_pos)
            if body_text:
                return _JSFunction(func_name, body_text)

    return None


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "NResolver",
    "NResolverError",
    "_JSFunction",
    "_interpret_n_function",
    "_extract_function_static",
    "_MAX_JS_BODY_SIZE",
    "_MAX_FUNC_BODY_SIZE",
]
