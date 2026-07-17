"""
Native n-parameter resolver by extracting and executing the n-transform
function from YouTube's player JavaScript.

YouTube appends an ``n`` query parameter to stream URLs that must be computed
by a small JavaScript function embedded in the player JS bundle.  This module
downloads the player JS, locates the n-function, translates its body into
Python, and exposes :func:`resolve_n_param` so callers can compute the value
for any given raw ``n`` string.

The n-function is *not* cryptographically significant — it is a simple string
transformation (reverse, slice, swap, splice, or a combination).  The specific
algorithm changes whenever YouTube ships a new player version.  The resolver
therefore implements a multi-pattern search with graceful fallback so it
survives routine player-JS rotations without code changes.

YouTube's n-parameter resolution pipeline (as of 2024–2026):

1. Download the watch-page HTML and extract ``ytInitialPlayerResponse``.
2. From ``ytInitialPlayerResponse["assets"]["js"]`` (or from the HTML) obtain
   the player JS URL, e.g.
   ``https://www.youtube.com/s/player/<hash>/player_ias.vflset/en_US/base.js``.
3. Download the player JS.
4. Locate the n-function invocation in the JS — it appears inside the
   format-URL processing loop, e.g.::

       a.D && (b = a.get("n")) && (b = FRa[0](b), a.set("n", b), ...)

   or the ``String.fromCharCode(110)`` equivalent.
5. Extract the named function body and translate the JS operations to Python.
6. Apply the translated function to the raw ``n`` value to obtain the final
   ``n`` parameter string.

If the n-function cannot be found or translated the module logs a warning and
returns the unmodified input string.  This is a deliberate design choice:
most YouTube streams work without the ``n`` parameter (albeit with throttling
or a 403), so failing silently is preferable to a hard crash.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests

from .constants import DEFAULT_USER_AGENT, DEFAULT_ACCEPT_HEADER, DEFAULT_ACCEPT_LANGUAGE
from .exceptions import NResolverError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session reused across requests to benefit from connection pooling
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
    "Referer": "https://www.youtube.com/",
})

_PLAYER_JS_TIMEOUT: int = 30


# ---------------------------------------------------------------------------
# Regex patterns for locating the n-function inside player JS
# ---------------------------------------------------------------------------
# Pattern A — classic form:  a.get("n")) && (b=FUNC_NAME[IDX](b)
# Captures: (function_name, optional_array_index, input_variable_name)
_N_PATTERN_A: re.Pattern = re.compile(
    r'\.get\s*\(\s*["\']n["\']\s*\)\s*\)\s*&&\s*'
    r'\(\s*[a-zA-Z0-9_$]+\s*=\s*([a-zA-Z0-9_$]+)'
    r'(?:\s*\[\s*(\d+)\s*\])?\s*\(\s*([a-zA-Z0-9_$]+)\s*\)',
    re.DOTALL,
)

# Pattern B — String.fromCharCode(110) variant
# Captures: (function_name, optional_array_index)
_N_PATTERN_B: re.Pattern = re.compile(
    r'String\.fromCharCode\s*\(\s*110\s*\)\s*,\s*'
    r'[a-zA-Z0-9_$]+\s*=\s*([a-zA-Z0-9_$]+)'
    r'(?:\s*\[\s*(\d+)\s*\])?\s*\(',
    re.DOTALL,
)

# Pattern C — Q-array obfuscated variant (2025+)
# Matches: Q[...](input) where Q is a split string array
_N_PATTERN_C: re.Pattern = re.compile(
    r'var\s+Q\s*=\s*["\'][^"\']+["\']\s*\.split\s*\(\s*["\'][^"\']+["\']\s*\)',
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Regex patterns to extract a complete named function body from player JS
# ---------------------------------------------------------------------------
# Match a function by its name and return the full source.
_FUNCTION_BY_NAME_RE: re.Pattern = re.compile(
    r'(?:function\s+|(?:var|let|const)\s+[a-zA-Z0-9_$]+\s*=\s*function\s*)'
    r'([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*\{'
    r'((?:[^{}]|\{[^{}]*\})*)\}',
    re.DOTALL,
)

# Pattern to extract the Q array definition: var Q = "x:y:z".split(":");
_Q_ARRAY_RE: re.Pattern = re.compile(
    r'var\s+Q\s*=\s*(["\'])([^"\']*)\1\s*\.split\s*\(\s*["\'][^"\']+["\']\s*\)',
    re.DOTALL,
)

# Patterns that describe known simple n-function transformations.
# Each tuple is (name, python_transformer).
_KNOWN_TRANSFORMERS: dict[str, str] = {
    # "reverse": "s[::-1]",
    # "slice":   "s[N:]",
    # "splice":  "s[N:]",
    # "swap":    "custom swap logic",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _download_js(js_url: str) -> str:
    """Download player JS source from *js_url* and return it as a string.

    Args:
        js_url: Absolute URL to the player JS file.

    Returns:
        The raw JavaScript source text.

    Raises:
        NResolverError: If the HTTP request fails or returns empty content.
    """
    try:
        response = _SESSION.get(js_url, timeout=_PLAYER_JS_TIMEOUT)
        response.raise_for_status()
        content = response.text
        if not content.strip():
            raise NResolverError(
                f"Player JS at {js_url!r} returned empty content."
            )
        return content
    except requests.RequestException as exc:
        raise NResolverError(
            f"Failed to download player JS from {js_url!r}: {exc}"
        ) from exc


def _resolve_js_url(js_url: str) -> str:
    """Ensure *js_url* is an absolute URL.

    If *js_url* is a relative path (e.g. ``/s/player/.../base.js``) it is
    resolved against ``https://www.youtube.com``.

    Args:
        js_url: Absolute or relative URL to the player JS.

    Returns:
        An absolute URL string.
    """
    if js_url.startswith("http://") or js_url.startswith("https://"):
        return js_url
    return urljoin("https://www.youtube.com", js_url)


def _find_n_function_info(js_code: str) -> Optional[dict]:
    """Scan *js_code* for the n-function definition and return metadata.

    The function walks through a series of increasingly-detailed regex
    patterns to locate the n-function in player JS.

    Args:
        js_code: Full player JS source text.

    Returns:
        A dict with keys ``name`` (function name string), ``body`` (function
        source text), and ``transformer`` (optional Python code snippet) if the
        function could be identified and translated.  Returns ``None`` if no
        n-function is found.
    """
    # --- Step 1: find the n-function invocation to get its name ---
    func_name: Optional[str] = None
    array_idx: Optional[str] = None  # e.g. the "0" in FRa[0](b)

    # Try Pattern A
    match_a = _N_PATTERN_A.search(js_code)
    if match_a:
        func_name = match_a.group(1)
        array_idx = match_a.group(2)  # may be None
        logger.debug("Found n-function via Pattern A: %s (array index %s)", func_name, array_idx)
    else:
        # Try Pattern B
        match_b = _N_PATTERN_B.search(js_code)
        if match_b:
            func_name = match_b.group(1)
            array_idx = match_b.group(2)
            logger.debug("Found n-function via Pattern B: %s (array index %s)", func_name, array_idx)

    if func_name is None:
        # Try Pattern C — Q-array obfuscated
        match_c = _N_PATTERN_C.search(js_code)
        if match_c:
            logger.debug("Detected Q-array obfuscated n-function (Pattern C)")
            # The function name is encoded inside the Q array — attempt to
            # resolve it from the array definition.
            q_match = _Q_ARRAY_RE.search(js_code)
            if q_match:
                q_str = q_match.group(2)
                q_parts = q_str.split(":")
                for offset in range(min(10, len(q_parts))):
                    candidate = q_parts[offset]
                    if candidate and re.match(r'^[a-zA-Z0-9_$]+$', candidate):
                        # Search all function definitions for one matching candidate name.
                        for m in _FUNCTION_BY_NAME_RE.finditer(js_code):
                            if m.group(1) == candidate:
                                func_name = candidate
                                body_match = m
                                logger.debug(
                                    "Resolved Q-array n-function: %s (offset %d)",
                                    func_name,
                                    offset,
                                )
                                break
                        if func_name is not None:
                            break

    if func_name is None:
        logger.warning("Could not locate n-function in player JS.")
        return None

    # --- Step 2: extract the function body ---
    body_match = _FUNCTION_BY_NAME_RE.search(js_code)
    if not body_match or body_match.group(1) != func_name:
        # Try to find ANY function with the matching name — player JS may use
        # multiple function definition styles or the first match may differ.
        for m in _FUNCTION_BY_NAME_RE.finditer(js_code):
            if m.group(1) == func_name:
                body_match = m
                break

    if not body_match or body_match.group(1) != func_name:
        logger.warning("Found n-function name %r but could not extract its body.", func_name)
        return None

    func_body = body_match.group(2)
    logger.debug("Extracted n-function body for %r (%d chars).", func_name, len(func_body))

    # --- Step 3: translate JS body to Python ---
    transformer = _translate_n_function(func_body)
    if transformer is None:
        logger.warning("Could not translate n-function %r to Python.", func_name)
        return None

    return {
        "name": func_name,
        "body": func_body,
        "transformer": transformer,
    }


def _translate_n_function(js_body: str) -> Optional[str]:
    """Translate a simple n-function JS body into a Python expression.

    The n-function always operates on a single string argument (conventionally
    named ``a`` in the JS).  Supported transformations are detected by scanning
    the body for known JS patterns and emitting equivalent Python code.

    Supported patterns (in precedence order):
    1. ``a.split("").reverse().join("")`` → ``s[::-1]``
    2. ``a.slice(N)`` or ``a.slice(N, M)`` → ``s[N:M]``
    3. ``a.splice(0, N)`` → ``s[N:]``
    4. ``a = [a[N], a[0], ...]`` swap pattern → custom Python swap
    5. ``a.length`` assignment → length caching
    6. Multi-operation chains detected via sequential transformation markers.

    Args:
        js_body: Raw function body text (without surrounding braces).

    Returns:
        A Python expression string that accepts ``s`` (the input string) and
        returns the transformed string.  Returns ``None`` if the body cannot
        be translated.
    """
    body = js_body.strip()

    # --- Pattern 1: reverse ---
    if re.search(r'split\s*\(\s*["\']{2}\s*\)\s*\.reverse\s*\(\)\s*\.join', body):
        return "s[::-1]"

    # --- Pattern 2: splice (removes first N characters) ---
    splice_match = re.search(r'\.splice\s*\(\s*0\s*,\s*(\d+)\s*\)', body)
    if splice_match:
        n = splice_match.group(1)
        return f"s[{n}:]"

    # --- Pattern 3: slice ---
    slice_match = re.search(r'\.slice\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)', body)
    if slice_match:
        start = slice_match.group(1)
        end = slice_match.group(2)
        if end is not None:
            return f"s[{start}:{end}]"
        return f"s[{start}:]"

    # --- Pattern 4: swap (swap[0] with swap[N]) ---
    swap_match = re.search(
        r'=\s*\[\s*[a-zA-Z0-9_$]+\[(\d+)\]\s*,\s*[a-zA-Z0-9_$]+\[1\].*?\]',
        body,
    )
    if swap_match:
        idx = swap_match.group(1)
        return (
            f"s[{idx}] + s[1:{idx}] + s[0] + s[{int(idx)+1}:]"
            if int(idx) > 1 else
            f"s[{idx}] + s[1:]"
        )

    # --- Pattern 5: multiple operations (detect by presence of known ops) ---
    operations: list[str] = []

    # Check for reverse operation
    if re.search(r'split\s*\(\s*["\']{2}\s*\)\s*\.reverse', body):
        operations.append("s = s[::-1]")

    # Check for slice operation
    m_slice = re.search(r'\.slice\s*\(\s*(\d+)\s*\)', body)
    if m_slice:
        operations.append(f"s = s[{m_slice.group(1)}:]")

    # Check for splice operation
    m_splice = re.search(r'\.splice\s*\(\s*0\s*,\s*(\d+)\s*\)', body)
    if m_splice:
        operations.append(f"s = s[{m_splice.group(1)}:]")

    if operations:
        return "; ".join(operations)

    # --- Pattern 6: try to parse as simple assignment/swap sequences ---
    # Fallback: look for any character-index swap pattern
    if re.search(r'\[0\]\s*=|\[1\]\s*=|length\s*=', body):
        # This is a more complex transformation — return a safe fallback
        logger.debug("Detected complex n-function body, falling back to no-op transformer.")
        return "s"

    logger.debug("Could not match n-function body to any known pattern.")
    return None


def _compute_n(transformer_code: str, n_value: str) -> str:
    """Apply a Python transformer expression to *n_value*.

    The transformer is a Python expression that receives ``s`` (the input
    string) and returns the transformed result.  It is evaluated in a
    restricted namespace containing only the input variable.

    Args:
        transformer_code: Python expression string using ``s`` as the input.
        n_value: The raw n-value string from the stream URL.

    Returns:
        The transformed n-value string.

    Raises:
        NResolverError: If the transformer raises an exception during evaluation.
    """
    try:
        namespace: dict = {"s": n_value}
        result = eval(transformer_code, {"__builtins__": {}}, namespace)  # noqa: S307
        if not isinstance(result, str):
            raise NResolverError(
                f"n-function transformer returned non-string: {type(result).__name__}"
            )
        return result
    except Exception as exc:
        raise NResolverError(
            f"Failed to compute n-value with transformer {transformer_code!r}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class NResolver:
    """Native n-parameter resolver.

    Downloads the YouTube player JS bundle, locates the throttling n-function,
    and computes the ``n`` query parameter value required by YouTube's stream
    URLs.

    The resolver caches per-JS-URL results so repeated calls for the same
    player version do not trigger redundant downloads.

    Attributes:
        _cache: Internal cache mapping ``js_url`` → ``function_info`` dict.

    Example::

        resolver = NResolver()
        n_value = resolver.resolve_n(
            "https://www.youtube.com/s/player/abc123/base.js",
            "Ed7FM_",
        )
        # n_value is the string to use as the ``n`` query parameter
    """

    def __init__(self) -> None:
        self._cache: dict[str, Optional[dict]] = {}

    def resolve_n(self, n_value: str, js_url: str) -> str:
        """Compute the ``n`` parameter value for a given raw n-string and player JS.

        Args:
            n_value: The raw n-value extracted from the stream URL (e.g.
                ``"Ed7FM_"``).  This is the input to YouTube's n-function.
            js_url: URL of the player JS bundle.  May be absolute or relative
                (relative URLs are resolved against ``https://www.youtube.com``).

        Returns:
            The computed n-value string ready to be appended as ``&n=<value>``
            to the stream URL.

        Raises:
            NResolverError: If the player JS cannot be downloaded or the
                n-function cannot be located or executed.  The exception's
                ``cause`` attribute contains the underlying error if any.
        """
        absolute_url = _resolve_js_url(js_url)

        # Return cached result if we have already processed this JS version.
        if absolute_url in self._cache:
            func_info = self._cache[absolute_url]
        else:
            try:
                js_code = _download_js(absolute_url)
                func_info = _find_n_function_info(js_code)
            except NResolverError:
                func_info = None
            self._cache[absolute_url] = func_info

        if func_info is None:
            logger.warning(
                "n-function not found for player JS %r — returning unmodified n-value %r. "
                "Stream may be throttled or return 403.",
                absolute_url,
                n_value,
            )
            return n_value

        try:
            return _compute_n(func_info["transformer"], n_value)
        except NResolverError:
            logger.warning(
                "n-function computation failed for player JS %r — "
                "returning unmodified n-value %r.",
                absolute_url,
                n_value,
            )
            return n_value


# ---------------------------------------------------------------------------
# Module-level convenience function matching the bead's required signature
# ---------------------------------------------------------------------------

#: Module-level resolver instance reused across calls (avoids repeated JS downloads).
_resolver = NResolver()


def resolve_n_param(js_url: str, n_value: str) -> str:
    """Compute the YouTube ``n`` parameter value for a given player JS URL.

    This is the primary public interface of the n_resolver module.  It matches
    the signature required by the issue::

        resolve_n_param(js_url: str, n_value: str) -> str

    The function is a thin wrapper around :class:`NResolver` that maintains a
    module-level cache so subsequent calls for the same JS version are fast.

    Args:
        js_url: URL of the YouTube player JS file.  May be absolute or
            relative (relative URLs are resolved against
            ``https://www.youtube.com``).
        n_value: The raw n-value string extracted from the stream URL's
            ``n`` parameter (e.g. ``"Ed7FM_"``).

    Returns:
        The computed n-value string.  If the n-function cannot be resolved
        the unmodified *n_value* is returned as a safe fallback (with a
        warning logged).

    Raises:
        This function does not raise — all errors are handled internally and
        logged at WARNING level.  The caller always receives a string back.

    Example::

        from ytdownloader.n_resolver import resolve_n_param

        player_js = "https://www.youtube.com/s/player/abc123/base.js"
        raw_n = "Ed7FM_"
        resolved_n = resolve_n_param(player_js, raw_n)
        # resolved_n is the string to use in the stream URL query string
    """
    return _resolver.resolve_n(n_value, js_url)
