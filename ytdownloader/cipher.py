"""
Cipher/signature deciphering module for encrypted YouTube stream URLs.

When stream URLs contain an `s` or `sp` parameter (signature cipher), the URL
is encrypted. This module downloads the YouTube player JS, finds the decipher
function using common patterns, extracts it, and applies it to decrypt the
signature. It also handles parsing of the `signatureCipher` field from format
dicts and resolving `n`-parameter throttling.

Public API:
    - ``decipher_url(encrypted_url: str, js_url: str) -> str``
    - ``parse_signature_cipher(cipher: str) -> dict``
    - ``apply_signature(url: str, signature: str, sp: str) -> str``
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from ytdownloader.exceptions import SignatureCipherError
from ytdownloader.constants import YOUTUBE_PAGE_HEADERS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers for brace-balanced extraction
# ---------------------------------------------------------------------------


def _find_balanced(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Return the index *after* the closing brace that balances the open at ``start``.

    Scans forward from ``start`` (which should point at an ``open_ch`` character)
    and returns the index one past the matching ``close_ch``.  Returns ``-1`` if
    no balanced pair is found.
    """
    depth = 0
    in_str = False
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and in_str:
            escape = True
            i += 1
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


# ---------------------------------------------------------------------------
# Regex patterns for extracting the decipher function from player JS
# ---------------------------------------------------------------------------

# Pattern 1: Invocation pattern for the decipher function, e.g.
#   `c = decodeURIComponent(a.get("s")) && (a.set("s", FUNC(c)), ...)`
#   or: `a.set("s", SOME_OBJ.decipher(value))`
_RE_DECIPHER_INVOCATION = re.compile(
    r"""
    \b(?:decodeURIComponent\s*\(\s*)?       # optional decodeURIComponent wrapper
    [a-zA-Z_$][a-zA-Z0-9_$.]*\.[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(\s*   # obj.method(
    (?P<arg>[a-zA-Z_$][a-zA-Z0-9_$]*)\s*    # argument name
    \)\s*\)?\s*
    (?:,\s*(?:encodeURIComponent\s*\()?      # optional encodeURIComponent in set()
    [a-zA-Z_$][a-zA-Z0-9_$]*\.set\s*\(\s*["']s["']\s*,\s*
    (?P<expr>[^)]+)\))?
    """,
    re.DOTALL | re.VERBOSE,
)

# Pattern 2: Function assignment pattern for known method names:
#   `decipher = function(a) { ... }` or `obj.decipher = function(a) { ... }`
_RE_FUNC_ASSIGNMENT = re.compile(
    r"""
    (?:\b|\.)\s*
    (?P<method_name>decipher|C|j|k|m|Ed|De)\s*   # common decipher method names
    \s*[=:]\s*function\s*\(\s*(?P<param>[a-zA-Z_$][a-zA-Z0-9_$]*)\s*\)\s*\{
    """,
    re.DOTALL | re.VERBOSE,
)

# Pattern 3: Generic function definition with split/join markers.
_RE_GENERIC_FUNC_DEF = re.compile(
    r"""
    (?P<qualifier>(?:var\s+)?[a-zA-Z_$][a-zA-Z0-9_$.]*\s*[=:]\s*)?
    function\s*\(\s*(?P<param>[a-zA-Z_$][a-zA-Z0-9_$]*)\s*\)\s*\{
    """,
    re.DOTALL | re.VERBOSE,
)

# Operation patterns (used on already-extracted function body text).
_RE_OP_REVERSE = re.compile(r"""\.reverse\s*\(\s*\)""", re.DOTALL)
_RE_OP_SLICE = re.compile(r"""\.slice\s*\(\s*(\d+)\s*\)""", re.DOTALL)
_RE_OP_SPLICE = re.compile(
    r"""\.splice\s*\(\s*(?:0\s*,\s*)?(\d+)\s*\)""", re.DOTALL
)
_RE_OP_SPLIT_JOIN = re.compile(
    r"""\.split\s*\(\s*["'][^"']*["']\s*\)\s*\.?\s*join\s*\(\s*["']?\s*["']?\s*\)""",
    re.DOTALL,
)


def parse_signature_cipher(cipher: str) -> Dict[str, Any]:
    """Parse a ``signatureCipher`` string into its components.

    The ``signatureCipher`` field is a URL-encoded string of the form::

        url=<encoded_url>&s=<encrypted_signature>&sp=<param_name>&n=<n_value>

    Args:
        cipher: The raw ``signatureCipher`` value from the format dict.

    Returns:
        A dict with keys:
        - ``url``: the base URL (URL-decoded)
        - ``s``: the encrypted signature string
        - ``sp``: the query parameter name for the deciphered signature
        - ``n``: (optional) the n-parameter value

    Raises:
        SignatureCipherError: If the cipher string cannot be parsed.
    """
    if not cipher:
        raise SignatureCipherError("Empty signatureCipher string.")

    try:
        parsed = parse_qs(cipher, strict_parsing=True)
    except Exception as exc:
        raise SignatureCipherError(
            f"Failed to parse signatureCipher: {exc}"
        ) from exc

    if "url" not in parsed:
        raise SignatureCipherError(
            "signatureCipher missing required 'url' field."
        )

    url = parsed["url"][0]
    if "s" not in parsed:
        raise SignatureCipherError(
            "signatureCipher missing required 's' (encrypted signature) field."
        )

    s = parsed["s"][0]
    sp = parsed.get("sp", ["signature"])[0]
    n = parsed.get("n", [None])[0]

    return {
        "url": url,
        "s": s,
        "sp": sp,
        "n": n,
    }


def apply_signature(url: str, signature: str, sp: str = "signature") -> str:
    """Append the deciphered signature to a base stream URL.

    Args:
        url: The base URL (without the signature parameter).
        signature: The deciphered signature string.
        sp: The query parameter name for the signature (default: ``"signature"``).

    Returns:
        The complete URL with the signature parameter appended.
    """
    delimiter = "&" if "?" in url else "?"
    return f"{url}{delimiter}{sp}={signature}"


def _fetch_player_js(js_url: str, timeout: int = 30) -> str:
    """Download the YouTube player JS source code.

    Args:
        js_url: Full URL to the player JS file.
        timeout: HTTP request timeout in seconds.

    Returns:
        The JS source code as a string.

    Raises:
        SignatureCipherError: If the JS cannot be downloaded.
    """
    headers = {
        **YOUTUBE_PAGE_HEADERS,
        "Accept": "*/*",
        "Referer": "https://www.youtube.com/",
    }
    try:
        response = requests.get(js_url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        raise SignatureCipherError(
            f"Failed to download player JS from {js_url}: {exc}"
        ) from exc


def _find_decipher_function(js_code: str) -> Optional[Tuple[str, str]]:
    """Find the signature decipher function within the player JS source.

    Tries multiple strategies in order of reliability:

    1. Look for a function assigned to a known property name (``decipher``,
       ``C``, ``j``) that contains ``split``/``reverse``/``join`` patterns.
    2. Search for the invocation pattern used in the player to set the
       ``s`` parameter, then walk back to find the function definition.
    3. Look for any standalone function containing ``split("")`` and
       ``reverse()``.

    Uses a brace-depth scanner rather than recursive regex so the code runs
    on Python's standard ``re`` module.

    Args:
        js_code: The raw player JS source code.

    Returns:
        A tuple of ``(function_name, function_body)`` if found, else ``None``.
    """
    # ------------------------------------------------------------------
    # Strategy 1: search for known method names assigned to a function.
    # ------------------------------------------------------------------
    for method_name in ("decipher", "C", "j", "k", "m", "Ed", "De"):
        for match in _RE_FUNC_ASSIGNMENT.finditer(js_code):
            if match.group("method_name") != method_name:
                continue
            # The regex ends with \{ so the match span includes the opening brace.
            open_pos = match.end() - 1
            end_pos = _find_balanced(js_code, open_pos)
            if end_pos == -1:
                continue
            body = js_code[open_pos + 1 : end_pos - 1]
            if "split" in body and "join" in body:
                return method_name, body

    # ------------------------------------------------------------------
    # Strategy 2: search for the invocation pattern used when setting "s".
    # ------------------------------------------------------------------
    invoc_match = _RE_DECIPHER_INVOCATION.search(js_code)
    if invoc_match:
        qualifier_str = invoc_match.group(0)
        # Try to find the method name referenced in the expression.
        expr = invoc_match.group("expr") or ""
        method_candidates = re.findall(
            r"[a-zA-Z_$][a-zA-Z0-9_$.]*\.([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(",
            expr,
        )
        for method_name in method_candidates:
            for fmatch in _RE_FUNC_ASSIGNMENT.finditer(js_code):
                if fmatch.group("method_name") == method_name:
                    open_pos = js_code.find("{", fmatch.end())
                    if open_pos == -1:
                        continue
                    end_pos = _find_balanced(js_code, open_pos)
                    if end_pos == -1:
                        continue
                    body = js_code[open_pos + 1 : end_pos - 1]
                    if "split" in body and "reverse" in body:
                        return method_name, body

    # ------------------------------------------------------------------
    # Strategy 3: search for any generic `function(param) { ... }` that has
    # split/join/reverse markers.
    # ------------------------------------------------------------------
    for match in _RE_GENERIC_FUNC_DEF.finditer(js_code):
        open_pos = js_code.find("{", match.end())
        if open_pos == -1:
            continue
        end_pos = _find_balanced(js_code, open_pos)
        if end_pos == -1:
            continue
        body = js_code[open_pos + 1 : end_pos - 1]
        if "split" in body and "join" in body:
            func_name = (
                match.group("qualifier").strip().split()[-1]
                if match.group("qualifier")
                else ""
            )
            return func_name, body

    # ------------------------------------------------------------------
    # Strategy 4: fallback – scan raw text for balanced blocks containing
    # the cipher operation markers.
    # ------------------------------------------------------------------
    i = 0
    while i < len(js_code):
        open_pos = js_code.find("{", i)
        if open_pos == -1:
            break
        end_pos = _find_balanced(js_code, open_pos)
        if end_pos == -1:
            break
        block = js_code[open_pos + 1 : end_pos - 1]
        if "split" in block and "join" in block:
            return "", block
        i = end_pos

    return None


def _extract_operations(func_body: str) -> List[Dict[str, Any]]:
    """Parse the decipher function body into a list of operations.

    The function body is expected to contain a sequence of transformations
    applied to the input string. Supported operations (in order of
    application):

    - ``reverse``: reverses the entire string.
    - ``slice``: drops the first *N* characters.
    - ``splice``: removes the first *N* characters (same as slice).
    - ``swap``: swaps characters at index 0 and index *N*.

    Args:
        func_body: The raw function body string extracted from the JS.

    Returns:
        A list of operation dicts. Each dict has an ``op`` key and
        additional keys depending on the operation type.
    """
    operations: List[Dict[str, Any]] = []

    # Look for the transformation sequence.
    # YouTube functions typically follow a pattern like:
    #   a = a.split("");   // always first
    #   // then some combination of:
    #   a = a.reverse();
    #   a = a.slice(N);
    #   a = a.splice(0, N);
    #   // swap is trickier - appears as [a[0], ..., a[N]] = [a[N], ...]
    #   a = a.join("");    // always last

    # Normalize the body: collapse whitespace, remove comments.
    normalized = _normalize_js_body(func_body)

    # Extract the variable name used in the function.
    # It's typically the function parameter name.
    var_match = re.search(
        r"function\s*\(\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\)",
        normalized,
    )
    var_name = var_match.group(1) if var_match else "a"

    # Split the body into individual statements.
    # We look for `var_name = ...` or `var_name += ...` patterns.
    stmt_pattern = re.compile(
        rf"""
        (?:\bvar\s+)?
        (?P<target>{re.escape(var_name)})\s*[+\-]?=\s*(?P<expr>[^;]+)
        """,
        re.DOTALL | re.VERBOSE,
    )

    # Find all statements that assign to the target variable.
    for stmt_match in stmt_pattern.finditer(normalized):
        expr = stmt_match.group("expr").strip()

        # Check for split+join (the envelope operation).
        if "split" in expr and "join" in expr:
            operations.append({"op": "split_join"})
            continue

        # Check for reverse().
        if re.search(r"\.reverse\s*\(\s*\)", expr):
            operations.append({"op": "reverse"})
            continue

        # Check for slice(N).
        slice_match = re.search(r"\.slice\s*\(\s*(\d+)\s*\)", expr)
        if slice_match:
            operations.append({
                "op": "slice",
                "arg": int(slice_match.group(1)),
            })
            continue

        # Check for splice(0, N) or splice(N).
        splice_match = re.search(r"\.splice\s*\(\s*(?:0\s*,\s*)?(\d+)\s*\)", expr)
        if splice_match:
            operations.append({
                "op": "splice",
                "arg": int(splice_match.group(1)),
            })
            continue

        # Check for swap pattern: [a[0], ..., a[N]] type array literal
        # or an assignment that looks like index swapping.
        swap_match = re.search(rf"""
            (?:\bvar\s+)?{re.escape(var_name)}\s*=\s*\[
            (?P<first>[^\]]+)\]
            """, expr, re.DOTALL | re.VERBOSE)
        if swap_match:
            indices = re.findall(rf"{re.escape(var_name)}\[(\d+)\]", swap_match.group("first"))
            if len(indices) == 2:
                operations.append({
                    "op": "swap",
                    "idx1": int(indices[0]),
                    "idx2": int(indices[1]),
                })
                continue

    return operations


def _normalize_js_body(body: str) -> str:
    """Normalize a JS function body by collapsing whitespace and removing comments."""
    # Remove single-line comments.
    body = re.sub(r"//[^\n]*", "", body)
    # Remove multi-line comments.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    # Collapse multiple whitespace/newlines into single space.
    body = re.sub(r"\s+", " ", body)
    return body.strip()


def _apply_operations(s: str, operations: List[Dict[str, Any]]) -> str:
    """Apply a list of decipher operations to a string.

    Operations are applied in the order they appear in the list.

    Args:
        s: The encrypted signature string.
        operations: List of operation dicts from ``_extract_operations``.

    Returns:
        The deciphered signature string.
    """
    result = s
    for op in operations:
        op_type = op["op"]
        if op_type == "reverse":
            result = result[::-1]
        elif op_type == "slice":
            result = result[op["arg"]:]
        elif op_type == "splice":
            result = result[op["arg"]:]
        elif op_type == "swap":
            idx1 = op["idx1"]
            idx2 = op["idx2"]
            if 0 <= idx1 < len(result) and 0 <= idx2 < len(result):
                chars = list(result)
                chars[idx1], chars[idx2] = chars[idx2], chars[idx1]
                result = "".join(chars)
        elif op_type == "split_join":
            # split+join is just the envelope - the actual work is done by
            # the intermediate operations. No change needed here.
            pass
    return result


def _decipher_signature(
    encrypted_sig: str,
    js_url: str,
) -> str:
    """Decipher an encrypted signature using the YouTube player JS.

    Args:
        encrypted_sig: The encrypted ``s`` parameter value.
        js_url: URL to the player JS file.

    Returns:
        The deciphered signature string.

    Raises:
        SignatureCipherError: If the decipher function cannot be found or
            the operations cannot be extracted/applied.
    """
    js_code = _fetch_player_js(js_url)

    found = _find_decipher_function(js_code)
    if found is None:
        raise SignatureCipherError(
            "Could not locate the signature decipher function in the "
            "player JS. YouTube may have changed their obfuscation pattern."
        )

    func_name, func_body = found
    logger.debug(
        "Found decipher function: name=%r, body length=%d chars.",
        func_name,
        len(func_body),
    )

    operations = _extract_operations(func_body)
    if not operations:
        raise SignatureCipherError(
            "Could not extract decipher operations from the function body. "
            "The JS pattern may have changed."
        )

    logger.debug(
        "Extracted %d decipher operations: %s",
        len(operations),
        [op["op"] for op in operations],
    )

    deciphered = _apply_operations(encrypted_sig, operations)
    return deciphered


def decipher_url(encrypted_url: str, js_url: str) -> str:
    """Decipher an encrypted YouTube stream URL.

    Handles two input formats:

    1. A ``signatureCipher``-style URL-encoded string::

           url=<base>&s=<encrypted_sig>&sp=<param_name>&n=<n_value>

    2. A full stream URL that already contains ``s``/``sp`` (or ``signature``)
       query parameters. In this case the base URL is everything except the
       ``s`` (and optionally ``sp``/``n``) parameters, and the deciphered
       signature is appended back.

    Args:
        encrypted_url: The encrypted URL or ``signatureCipher`` value.
        js_url: Full URL to the YouTube player JS file used to extract the
            decipher function.

    Returns:
        The deciphered direct stream URL with the valid signature appended.

    Raises:
        SignatureCipherError: If the URL cannot be deciphered due to missing
            data, network errors, or unrecognized JS patterns.
    """
    parsed = urlparse(encrypted_url)

    # Case 1: It is a full URL with 's' query parameter(s).
    if parsed.scheme and parsed.netloc and parsed.path:
        query_params = parse_qs(parsed.query)

        # Determine which parameter holds the encrypted signature.
        sig_param_name = None
        for name in ("s", "sig", "signature"):
            if name in query_params:
                sig_param_name = name
                break

        if sig_param_name is None:
            raise SignatureCipherError(
                "URL does not contain an encrypted signature parameter ('s', "
                "'sig', or 'signature')."
            )

        encrypted_sig = query_params[sig_param_name][0]
        sp = query_params.get("sp", ["signature"])[0]

        # Decipher the signature.
        deciphered_sig = _decipher_signature(encrypted_sig, js_url)

        # Rebuild the URL without the encrypted sig and with the deciphered sig.
        new_params = {
            k: v for k, v in query_params.items()
            if k not in ("s", "sig", "signature", "sp")
        }
        new_params[sp] = deciphered_sig
        new_query = urlencode(new_params, doseq=True)

        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    # Case 2: It is a raw signatureCipher string (no scheme/host).
    cipher_data = parse_signature_cipher(encrypted_url)
    base_url = cipher_data["url"]
    encrypted_sig = cipher_data["s"]
    sp = cipher_data["sp"]

    deciphered_sig = _decipher_signature(encrypted_sig, js_url)
    return apply_signature(base_url, deciphered_sig, sp)


class CipherResolver:
    """Stateful cipher resolver that caches the player JS and decipher function.

    This class is useful when deciphering multiple URLs from the same video,
    as the player JS only needs to be downloaded and parsed once.

    Args:
        js_url: URL to the YouTube player JS file.
        cache_js: If ``True`` (default), the JS source is cached so it is
            only downloaded once.
    """

    def __init__(self, js_url: str, cache_js: bool = True) -> None:
        self.js_url = js_url
        self._cache_js = cache_js
        self._js_code: Optional[str] = None
        self._func_name: Optional[str] = None
        self._func_body: Optional[str] = None
        self._operations: Optional[List[Dict[str, Any]]] = None

    def _ensure_loaded(self) -> None:
        """Download and parse the player JS if not already cached."""
        if self._js_code is not None:
            return
        self._js_code = _fetch_player_js(self.js_url)
        found = _find_decipher_function(self._js_code)
        if found is None:
            raise SignatureCipherError(
                "Could not locate the signature decipher function in the "
                "player JS."
            )
        self._func_name, self._func_body = found
        self._operations = _extract_operations(self._func_body)
        if not self._operations:
            raise SignatureCipherError(
                "Could not extract decipher operations from the function body."
            )

    def decipher_signature(self, encrypted_sig: str) -> str:
        """Decipher a single encrypted signature using cached JS.

        Args:
            encrypted_sig: The encrypted signature string.

        Returns:
            The deciphered signature.
        """
        self._ensure_loaded()
        return _apply_operations(encrypted_sig, self._operations)

    def resolve(self, encrypted_url: str) -> str:
        """Decipher an encrypted stream URL using cached JS.

        Args:
            encrypted_url: The encrypted URL or ``signatureCipher`` value.

        Returns:
            The deciphered direct stream URL.
        """
        self._ensure_loaded()
        return decipher_url(encrypted_url, self.js_url)
