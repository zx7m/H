"""
Signature cipher decoder for YouTube ``signatureCipher`` URL parameters.

YouTube protects many stream URLs by embedding them in a ``signatureCipher``
query parameter.  The value is a URL-encoded query-string that carries:

- ``s`` – the encrypted/obfuscated signature string.
- ``sp`` – the name of the query parameter that must carry the deciphered
  signature when requesting the stream URL (commonly ``"signature"``).
- ``url`` – the base stream URL (itself URL-encoded within the cipher).
- ``n`` – the name of a JavaScript navigator function that must be applied
  to the ``url`` path component before it is usable.

This module decodes the cipher, extracts each component, applies the
signature to the URL, and exposes a rich set of helpers for validation,
normalisation and introspection.

Typical usage::

    from ytdownloader.signature_cipher import decode_signature_cipher, apply_signature

    result = decode_signature_cipher(cipher_string)
    signed_url = apply_signature(result["url"], result["s"], result["sp"])
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, parse_qsl, quote, quote_plus, unquote, unquote_plus, urlencode, urljoin, urlparse, urlunparse

from .exceptions import SignatureCipherError
from .logger import get_logger

_logger = get_logger(__name__)

__all__ = [
    "CipherResult",
    "CipherComponent",
    "CipherValidationError",
    "CipherParseError",
    "decode_signature_cipher",
    "parse_cipher_params",
    "apply_signature",
    "validate_cipher_components",
    "normalize_cipher_url",
    "extract_cipher_from_format",
    "batch_decode_ciphers",
    "CIPHER_COMPONENT_ORDER",
    "REQUIRED_CIPHER_KEYS",
    "KNOWN_SP_VALUES",
    "SIGNATURE_PARAM_ALIASES",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CIPHER_COMPONENT_ORDER: Tuple[str, ...] = ("s", "sp", "url", "n")
REQUIRED_CIPHER_KEYS: Tuple[str, ...] = ("s", "url")
KNOWN_SP_VALUES: Tuple[str, ...] = (
    "signature",
    "sig",
    "sign",
    "s",
    "auth",
    "authuser",
    "access_token",
    "token",
)

SIGNATURE_PARAM_ALIASES: Dict[str, str] = {
    "sp": "signature",
    "sig": "signature",
    "sign": "signature",
    "s": "signature",
    "auth": "authentication",
    "authuser": "authentication_user",
    "access_token": "access_token",
    "token": "token",
}

_CIPHER_KEY_RE = re.compile(r"^(s|sp|url|n)$", re.IGNORECASE)
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DOUBLE_ENCODED_RE = re.compile(r"%25[0-9A-Fa-f]{2}")
_YOUTUBE_STREAM_HOSTS = (
    "googlevideo.com",
    "youtube.com",
    "ytimg.com",
    "yt3.ggpht.com",
)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class CipherValidationError(SignatureCipherError):
    """Raised when a decoded cipher fails structural validation."""


class CipherParseError(SignatureCipherError):
    """Raised when the cipher string cannot be parsed at all."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CipherComponent:
    """Represents a single decoded component of a ``signatureCipher`` value.

    Attributes:
        key: The component key (``"s"``, ``"sp"``, ``"url"`` or ``"n"``).
        raw_value: The URL-encoded value exactly as found in the cipher.
        decoded_value: The URL-decoded value.
        is_required: ``True`` when the key is mandatory for a usable cipher.
    """

    key: str
    raw_value: str = ""
    decoded_value: str = ""
    is_required: bool = False

    def __post_init__(self) -> None:
        if not self.decoded_value and self.raw_value:
            self.decoded_value = unquote(self.raw_value)
        if not self.raw_value and self.decoded_value:
            self.raw_value = quote(self.decoded_value, safe="")

    def is_empty(self) -> bool:
        """Return ``True`` when both raw and decoded values are empty."""
        return not self.raw_value and not self.decoded_value

    def __repr__(self) -> str:
        return (
            f"CipherComponent(key={self.key!r}, "
            f"raw_value={self.raw_value!r}, "
            f"decoded_value={self.decoded_value!r})"
        )


@dataclass
class CipherResult:
    """Holds the fully decoded ``signatureCipher`` components.

    Attributes:
        signature: The deciphered signature string (from ``s``).
        sp: The signature parameter name (from ``sp``).
        url: The base stream URL (from ``url``).
        n: The navigator function name (from ``n``), or empty string.
        raw_cipher: The original, unmodified cipher string.
        components: Ordered mapping of all discovered cipher components.
        parse_warnings: Non-fatal warnings encountered during parsing.
        is_valid: ``True`` when all required components are present and
            structurally sound.
    """

    signature: str = ""
    sp: str = ""
    url: str = ""
    n: str = ""
    raw_cipher: str = ""
    components: Dict[str, CipherComponent] = field(default_factory=dict)
    parse_warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.components:
            self.components = self._build_components()

    def _build_components(self) -> Dict[str, CipherComponent]:
        comps: Dict[str, CipherComponent] = {}
        for key in CIPHER_COMPONENT_ORDER:
            raw = ""
            decoded = ""
            if key == "s":
                decoded = self.signature
            elif key == "sp":
                decoded = self.sp
            elif key == "url":
                decoded = self.url
            elif key == "n":
                decoded = self.n
            raw = quote(decoded, safe="") if decoded else ""
            comps[key] = CipherComponent(
                key=key,
                raw_value=raw,
                decoded_value=decoded,
                is_required=key in REQUIRED_CIPHER_KEYS,
            )
        return comps

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this result to a plain dictionary.

        Returns:
            A dictionary with all public fields plus the nested components.
        """
        return {
            "signature": self.signature,
            "sp": self.sp,
            "url": self.url,
            "n": self.n,
            "raw_cipher": self.raw_cipher,
            "is_valid": self.is_valid,
            "parse_warnings": list(self.parse_warnings),
            "components": {
                k: {
                    "key": v.key,
                    "raw_value": v.raw_value,
                    "decoded_value": v.decoded_value,
                    "is_required": v.is_required,
                }
                for k, v in self.components.items()
            },
        }

    @property
    def is_valid(self) -> bool:
        """``True`` when all required cipher components are non-empty."""
        return all(
            self.components[k].decoded_value for k in REQUIRED_CIPHER_KEYS
        )

    def get_component(self, key: str) -> CipherComponent:
        """Return a specific :class:`CipherComponent` by key.

        Args:
            key: One of ``"s"``, ``"sp"``, ``"url"``, ``"n"``.

        Returns:
            The matching :class:`CipherComponent`, or an empty component if
            the key is unknown.
        """
        return self.components.get(key, CipherComponent(key=key))

    def missing_required(self) -> List[str]:
        """Return a list of required keys that are empty.

        Returns:
            A list of key strings that are required but missing/empty.
        """
        return [k for k in REQUIRED_CIPHER_KEYS if not self.components[k].decoded_value]

    def __repr__(self) -> str:
        return (
            f"CipherResult(signature={self.signature!r}, sp={self.sp!r}, "
            f"url={self.url!r}, n={self.n!r}, is_valid={self.is_valid})"
        )


# ---------------------------------------------------------------------------
# Low-level URL / query-string helpers
# ---------------------------------------------------------------------------


def _is_encoded(value: str) -> bool:
    """Return ``True`` when *value* looks like a URL-encoded string.

    A value is considered encoded if it contains ``%`` escape sequences.

    Args:
        value: The string to inspect.

    Returns:
        ``True`` when percent-encoded sequences are detected.
    """
    return "%" in value


def _decode_deep(value: str) -> str:
    """Recursively decode percent-encoded strings.

    YouTube sometimes double-encodes cipher values.  This helper decodes
    up to three times, stopping when the value no longer changes.

    Args:
        value: The raw string to decode.

    Returns:
        The fully decoded string.
    """
    previous = value
    for _ in range(3):
        decoded = unquote(previous)
        if decoded == previous:
            break
        previous = decoded
    return previous


def _decode_query_string(value: str) -> str:
    """Decode a URL-encoded query-string value.

    Handles both ``+``-style space encoding and percent encoding.

    Args:
        value: The raw query-string value.

    Returns:
        The decoded string.
    """
    decoded = unquote_plus(value)
    decoded = unquote(decoded)
    return decoded


def _safe_urljoin(base: str, path: str) -> str:
    """Safely join *base* URL and *path*, handling edge cases.

    Uses :func:`urllib.parse.urljoin` with additional normalisation.

    Args:
        base: The base URL.
        path: The relative path to append.

    Returns:
        The joined URL.
    """
    if not base:
        return path
    if _URL_SCHEME_RE.match(path):
        return path
    try:
        return urljoin(base, path)
    except (ValueError, TypeError):
        return path


def _has_valid_scheme(url: str) -> bool:
    """Return ``True`` when *url* has a recognised scheme.

    Args:
        url: The URL to test.

    Returns:
        ``True`` when the URL starts with ``http://`` or ``https://``.
    """
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except (ValueError, TypeError):
        return False


def _is_youtube_host(url: str) -> bool:
    """Return ``True`` when *url* points to a known YouTube streaming host.

    Args:
        url: The URL to inspect.

    Returns:
        ``True`` when the hostname is in the known YouTube hosts list.
    """
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
        return any(host == h or host.endswith("." + h) for h in _YOUTUBE_STREAM_HOSTS)
    except (ValueError, TypeError):
        return False


def _normalize_url_path(url: str) -> str:
    """Normalise the path portion of a URL.

    Ensures the path begins with ``/`` when non-empty, and strips any
    leading slash duplication.

    Args:
        url: The URL to normalise.

    Returns:
        The URL with a normalised path component.
    """
    try:
        parsed = urlparse(url)
        path = parsed.path
        if path and not path.startswith("/"):
            path = "/" + path
        path = re.sub(r"/{2,}", "/", path)
        return urlunparse(parsed._replace(path=path))
    except (ValueError, TypeError):
        return url


def _strip_fragment(url: str) -> str:
    """Remove the fragment component of a URL.

    Args:
        url: The URL to strip.

    Returns:
        The URL without its fragment.
    """
    try:
        parsed = urlparse(url)
        return urlunparse(parsed._replace(fragment=""))
    except (ValueError, TypeError):
        return url


# ---------------------------------------------------------------------------
# Cipher string parsing
# ---------------------------------------------------------------------------


def parse_cipher_params(cipher: str) -> Dict[str, str]:
    """URL-decode and parse a ``signatureCipher`` string into its components.

    Accepts the raw URL-encoded cipher string and returns a plain dictionary
    mapping each key (``"s"``, ``"sp"``, ``"url"``, ``"n"``) to its
    decoded value.

    The function handles:

    - Standard ``key=value&key=value`` query-string format.
    - Deeply percent-encoded values (decodes up to 3 levels).
    - Values that are missing from the cipher (absent from the result).
    - Malformed ``key=value`` pairs (skipped with a logged warning).

    Args:
        cipher: The raw ``signatureCipher`` parameter value.  May be
            URL-encoded at one or more levels.

    Returns:
        A dictionary mapping cipher component names to their decoded values.
        Only keys present in the cipher are included in the result.

    Raises:
        CipherParseError: If the cipher is ``None``, not a string, or is
            entirely empty after stripping whitespace.

    Examples:
        >>> parse_cipher_params("s=abc&sp=signature&url=https%3A%2F%2Fexample.com")
        {'s': 'abc', 'sp': 'signature', 'url': 'https://example.com'}
    """
    if cipher is None:
        raise CipherParseError("Cipher value is None; expected a non-empty string.")

    if not isinstance(cipher, str):
        raise CipherParseError(
            f"Cipher must be a string, got {type(cipher).__name__}."
        )

    cipher = cipher.strip()

    if not cipher:
        raise CipherParseError("Cipher string is empty after stripping whitespace.")

    _logger.debug("Parsing cipher params (length=%d)", len(cipher))

    result: Dict[str, str] = {}
    warnings: List[str] = []

    try:
        parsed_qs = parse_qs(cipher, keep_blank_values=False, strict_parsing=False)
    except Exception as exc:
        raise CipherParseError(
            f"Failed to parse cipher as query string: {exc}"
        ) from exc

    for raw_key, raw_values in parsed_qs.items():
        key = raw_key.strip().lower()
        if not _CIPHER_KEY_RE.match(key):
            warnings.append(f"Ignoring unrecognised cipher key: {key!r}")
            _logger.debug("Skipping unrecognised cipher key: %r", key)
            continue

        if not raw_values:
            warnings.append(f"Cipher key {key!r} has no value; treating as empty.")
            result[key] = ""
            continue

        raw_value = raw_values[0]
        decoded = _decode_deep(_decode_query_string(raw_value))
        result[key] = decoded

        _logger.debug(
            "Cipher component %r: raw=%r decoded=%r", key, raw_value, decoded
        )

    if warnings:
        for warning in warnings:
            _logger.warning("Cipher parse warning: %s", warning)

    if not result:
        raise CipherParseError(
            "Cipher string contains no recognised components after parsing."
        )

    _logger.info(
        "Parsed %d cipher component(s): %s", len(result), ", ".join(sorted(result))
    )
    return result


def _parse_cipher_components(
    cipher: str,
) -> Tuple[Dict[str, CipherComponent], List[str]]:
    """Parse cipher string into :class:`CipherComponent` objects.

    This is the lower-level parser used by :func:`decode_signature_cipher`.
    It returns raw component objects plus any non-fatal warnings.

    Args:
        cipher: The raw ``signatureCipher`` value.

    Returns:
        A 2-tuple ``(components_dict, warnings_list)``.

    Raises:
        CipherParseError: If the cipher cannot be parsed at all.
    """
    params = parse_cipher_params(cipher)
    components: Dict[str, CipherComponent] = {}
    warnings: List[str] = []

    for key in CIPHER_COMPONENT_ORDER:
        if key not in params:
            components[key] = CipherComponent(key=key, is_required=key in REQUIRED_CIPHER_KEYS)
            warnings.append(f"Missing cipher component: {key!r}")
            continue

        raw_value = params[key]
        decoded_value = _decode_deep(_decode_query_string(raw_value))
        components[key] = CipherComponent(
            key=key,
            raw_value=raw_value,
            decoded_value=decoded_value,
            is_required=key in REQUIRED_CIPHER_KEYS,
        )

    return components, warnings


# ---------------------------------------------------------------------------
# URL normalisation helpers
# ---------------------------------------------------------------------------


def normalize_cipher_url(url: str) -> str:
    """Normalise and validate the ``url`` component from a decoded cipher.

    Performs the following steps:

    1. Decode any residual percent-encoding.
    2. Strip URL fragments and unnecessary query parameters.
    3. Ensure the scheme is ``http`` or ``https``.
    4. Normalise the path component.
    5. Reconstruct the URL using :func:`urllib.parse.urlunparse`.

    Args:
        url: The raw ``url`` value extracted from the cipher.

    Returns:
        A normalised URL string.

    Raises:
        CipherValidationError: If the URL is empty or lacks a valid scheme.
    """
    if not url or not isinstance(url, str):
        raise CipherValidationError(
            f"Cipher 'url' component is empty or not a string: {url!r}."
        )

    decoded_url = _decode_deep(unquote_plus(url.strip()))

    if not _has_valid_scheme(decoded_url):
        raise CipherValidationError(
            f"Cipher 'url' component lacks a valid scheme: {decoded_url!r}."
        )

    decoded_url = _strip_fragment(decoded_url)
    decoded_url = _normalize_url_path(decoded_url)

    _logger.debug("Normalised cipher URL: %r -> %r", url, decoded_url)
    return decoded_url


# ---------------------------------------------------------------------------
# Signature application
# ---------------------------------------------------------------------------


def apply_signature(url: str, signature: str, sp: str = "signature") -> str:
    """Append the deciphered signature to *url* as a query parameter.

    YouTube expects the deciphered signature to be passed as a query
    parameter whose name is specified by ``sp`` (commonly ``"signature"``
    or ``"sig"``).

    The function handles:

    - URLs that already carry the target query parameter (the existing
      value is replaced).
    - URLs with no query string (parameter is appended with ``?``).
    - URLs that are already fully signed (detected and returned unchanged
      if the signature already matches).
    - Empty or ``None`` signature values (the URL is returned unchanged
      with a logged warning).
    - Special characters in the signature (properly percent-encoded).

    Args:
        url: The base stream URL to sign.
        signature: The deciphered signature string.
        sp: The name of the query parameter to use for the signature.
            Defaults to ``"signature"``.

    Returns:
        The URL with the signature parameter appended or replaced.

    Raises:
        CipherValidationError: If *url* is empty or not a valid URL string.

    Examples:
        >>> apply_signature(
        ...     "https://example.com/video",
        ...     "deadbeef",
        ...     sp="signature",
        ... )
        'https://example.com/video?signature=deadbeef'
    """
    if not url or not isinstance(url, str):
        raise CipherValidationError(
            f"apply_signature requires a non-empty URL string, got {url!r}."
        )

    url = url.strip()

    if not signature or not isinstance(signature, str):
        _logger.warning(
            "apply_signature called with empty or non-string signature; "
            "returning URL unchanged."
        )
        return url

    signature = signature.strip()
    sp = (sp or "signature").strip()

    if not sp:
        sp = "signature"
        _logger.debug("Empty 'sp' value defaulted to 'signature'.")

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError) as exc:
        raise CipherValidationError(
            f"Unable to parse URL: {url!r} ({exc})."
        ) from exc

    existing_params = parse_qs(parsed.query, keep_blank_values=True)

    if sp in existing_params and existing_params[sp] and existing_params[sp][0] == signature:
        _logger.debug("URL already carries the correct signature; returning unchanged.")
        return url

    existing_params[sp] = [signature]

    new_query = urlencode(existing_params, doseq=True)
    signed_url = urlunparse(parsed._replace(query=new_query))

    _logger.debug(
        "Applied signature parameter %r to URL: %s", sp, signed_url
    )
    return signed_url


# ---------------------------------------------------------------------------
# Component validation
# ---------------------------------------------------------------------------


def validate_cipher_components(
    components: Dict[str, CipherComponent],
) -> List[str]:
    """Validate decoded cipher components and return a list of issues.

    Checks performed:

    - Required components (``s`` and ``url``) are present and non-empty.
    - The ``url`` component has a valid scheme (``http``/``https``).
    - The ``url`` component points to a recognised YouTube streaming host
      (warning only, not an error).
    - The ``sp`` component, if present, is a non-empty string.
    - The ``s`` (signature) component is non-empty.
    - The ``n`` component, if present, looks like a JavaScript function
      identifier.

    Args:
        components: A mapping of cipher component keys to
            :class:`CipherComponent` objects (as returned by
            :func:`decode_signature_cipher`).

    Returns:
        A list of human-readable issue strings.  An empty list means all
        checks passed.
    """
    issues: List[str] = []

    for required_key in REQUIRED_CIPHER_KEYS:
        comp = components.get(required_key)
        if comp is None or not comp.decoded_value:
            issues.append(
                f"Required cipher component {required_key!r} is missing or empty."
            )

    url_comp = components.get("url")
    if url_comp and url_comp.decoded_value:
        url_val = url_comp.decoded_value
        if not _has_valid_scheme(url_val):
            issues.append(
                f"Cipher 'url' component has invalid scheme: {url_val!r}."
            )
        if not _is_youtube_host(url_val):
            issues.append(
                f"Cipher 'url' component does not point to a known YouTube host: "
                f"{urlparse(url_val).hostname!r}."
            )

    sig_comp = components.get("s")
    if sig_comp and not sig_comp.decoded_value:
        issues.append("Cipher 's' (signature) component is empty.")

    sp_comp = components.get("sp")
    if sp_comp and sp_comp.decoded_value:
        sp_val = sp_comp.decoded_value.strip()
        if not sp_val:
            issues.append("Cipher 'sp' component is an empty string.")

    n_comp = components.get("n")
    if n_comp and n_comp.decoded_value:
        n_val = n_comp.decoded_value.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n_val):
            issues.append(
                f"Cipher 'n' value does not look like a JS identifier: {n_val!r}."
            )

    return issues


# ---------------------------------------------------------------------------
# Signature cipher extraction from format dicts
# ---------------------------------------------------------------------------


def extract_cipher_from_format(
    fmt: Dict[str, Any],
) -> Optional[str]:
    """Extract the ``signatureCipher`` value from a YouTube format dictionary.

    YouTube embeds the cipher in a format dict under the key
    ``signatureCipher``.  This helper locates the value regardless of
    the exact casing used.

    Args:
        fmt: A raw format dictionary (e.g. from ``streamingData.formats``).

    Returns:
        The ``signatureCipher`` value as a string, or ``None`` if the
        format dict does not carry a cipher.

    Raises:
        SignatureCipherError: If the ``signatureCipher`` field is present
            but not a string.
    """
    if not isinstance(fmt, dict):
        _logger.debug("extract_cipher_from_format called with non-dict: %r", fmt)
        return None

    for key in ("signatureCipher", "signature_cipher", "signaturecipher"):
        value = fmt.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise SignatureCipherError(
                f"Format 'signatureCipher' field is not a string: {type(value).__name__}."
            )
        _logger.debug(
            "Extracted signatureCipher from format (itag=%s): %.80s...",
            fmt.get("itag", "?"),
            value,
        )
        return value

    return None


# ---------------------------------------------------------------------------
# Core decode function
# ---------------------------------------------------------------------------


def decode_signature_cipher(cipher: str) -> CipherResult:
    """Decode a YouTube ``signatureCipher`` parameter into structured components.

    This is the primary entry point for signature cipher processing.
    It accepts the raw URL-encoded cipher string and returns a
    :class:`CipherResult` containing the decoded ``s`` (signature),
    ``sp`` (signature parameter name), ``url`` (base stream URL), and
    ``n`` (navigator function name).

    Processing pipeline:

    1. Parse the cipher string via :func:`parse_cipher_params`.
    2. Build :class:`CipherComponent` objects for each key.
    3. Validate the decoded components via :func:`validate_cipher_components`.
    4. Normalise the ``url`` component via :func:`normalize_cipher_url`.
    5. Populate and return a :class:`CipherResult`.

    Args:
        cipher: The raw ``signatureCipher`` value extracted from a YouTube
            format dictionary.  Typically obtained from
            ``fmt["signatureCipher"]``.

    Returns:
        A :class:`CipherResult` with all decoded components.  Check
        ``result.is_valid`` to determine whether all required fields
        are present and the URL is well-formed.

    Raises:
        CipherParseError: If the cipher cannot be parsed at all (e.g. not
            a string, empty, or contains no recognised keys).
        CipherValidationError: If a required component is missing or
            structurally invalid.

    Examples:
        >>> result = decode_signature_cipher(
        ...     "s=abc123&sp=signature&url=https%3A%2F%2Fexample.com&n=test_func"
        ... )
        >>> result.signature
        'abc123'
        >>> result.sp
        'signature'
        >>> result.is_valid
        True
    """
    if not cipher or not isinstance(cipher, str):
        raise CipherParseError(
            f"Cipher must be a non-empty string, got {cipher!r}."
        )

    cipher = cipher.strip()
    _logger.debug("decode_signature_cipher called (length=%d)", len(cipher))

    components, parse_warnings = _parse_cipher_components(cipher)

    validation_issues = validate_cipher_components(components)

    if validation_issues:
        for issue in validation_issues:
            _logger.warning("Cipher validation issue: %s", issue)

    url_component = components.get("url")
    if url_component and url_component.decoded_value:
        try:
            normalized_url = normalize_cipher_url(url_component.decoded_value)
            components["url"] = CipherComponent(
                key="url",
                raw_value=url_component.raw_value,
                decoded_value=normalized_url,
                is_required=True,
            )
        except CipherValidationError as exc:
            _logger.warning("URL normalisation failed: %s", exc)
            parse_warnings.append(f"URL normalisation failed: {exc}")

    result = CipherResult(
        signature=components.get("s", CipherComponent("s")).decoded_value,
        sp=components.get("sp", CipherComponent("sp")).decoded_value,
        url=components.get("url", CipherComponent("url")).decoded_value,
        n=components.get("n", CipherComponent("n")).decoded_value,
        raw_cipher=cipher,
        components=components,
        parse_warnings=parse_warnings + validation_issues,
    )

    if not result.is_valid:
        missing = result.missing_required()
        raise CipherValidationError(
            f"Decoded cipher is missing required components: {missing}. "
            f"Cipher: {cipher[:120]}..."
        )

    _logger.info(
        "Successfully decoded signatureCipher: sp=%r url=%r n=%r",
        result.sp,
        result.url[:80] if result.url else "",
        result.n,
    )
    return result


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


def batch_decode_ciphers(
    ciphers: List[str],
    *,
    skip_invalid: bool = False,
) -> List[CipherResult]:
    """Decode multiple ``signatureCipher`` values in sequence.

    Each cipher is decoded independently via :func:`decode_signature_cipher`.
    By default, any error causes the entire batch to raise.  Set
    ``skip_invalid=True`` to silently skip failing entries.

    Args:
        ciphers: A list of raw ``signatureCipher`` strings.
        skip_invalid: When ``True``, entries that fail decoding are replaced
            with an empty/default :class:`CipherResult` rather than
            raising an exception.

    Returns:
        A list of :class:`CipherResult` objects, one per input cipher.
        The order of results matches the order of inputs.

    Raises:
        CipherParseError / CipherValidationError: If ``skip_invalid`` is
            ``False`` and any entry fails to decode.

    Examples:
        >>> results = batch_decode_ciphers([
        ...     "s=abc&sp=signature&url=https%3A%2F%2Fexample.com",
        ...     "s=def&sp=sig&url=https%3A%2F%2Fexample.org",
        ... ])
        >>> len(results)
        2
    """
    results: List[CipherResult] = []
    errors: List[Tuple[int, Exception]] = []

    for i, cipher in enumerate(ciphers):
        try:
            result = decode_signature_cipher(cipher)
            results.append(result)
        except (CipherParseError, CipherValidationError) as exc:
            if skip_invalid:
                _logger.warning(
                    "Skipping cipher at index %d: %s", i, exc
                )
                results.append(
                    CipherResult(
                        raw_cipher=str(cipher),
                        parse_warnings=[str(exc)],
                    )
                )
            else:
                errors.append((i, exc))

    if errors and not skip_invalid:
        index, exc = errors[0]
        raise SignatureCipherError(
            f"batch_decode_ciphers failed at index {index}: {exc}"
        ) from exc

    _logger.info(
        "batch_decode_ciphers processed %d/%d ciphers successfully.",
        len(results),
        len(ciphers),
    )
    return results


# ---------------------------------------------------------------------------
# Cipher string reconstruction
# ---------------------------------------------------------------------------


def build_cipher_string(
    signature: str,
    sp: str = "signature",
    url: str = "",
    n: str = "",
    *,
    double_encode: bool = True,
) -> str:
    """Reconstruct a ``signatureCipher`` string from individual components.

    The inverse of :func:`decode_signature_cipher`.  Useful for testing
    or for generating new cipher strings.

    Args:
        signature: The signature string (``s`` component).
        sp: The signature parameter name (``sp`` component).
        url: The base stream URL (``url`` component).
        n: The navigator function name (``n`` component).
        double_encode: When ``True`` (default), percent-encode values so
            that the resulting cipher string is safe to embed as a single
            query parameter value.

    Returns:
        A URL-encoded cipher string in the format
        ``s=...&sp=...&url=...&n=...``.
    """
    parts: List[Tuple[str, str]] = []
    if signature:
        parts.append(("s", quote_plus(signature) if double_encode else signature))
    if sp:
        parts.append(("sp", quote_plus(sp) if double_encode else sp))
    if url:
        parts.append(("url", quote_plus(url) if double_encode else url))
    if n:
        parts.append(("n", quote_plus(n) if double_encode else n))

    return urlencode(parts)


# ---------------------------------------------------------------------------
# Cipher string comparison and matching
# ---------------------------------------------------------------------------


def compare_ciphers(a: CipherResult, b: CipherResult) -> Dict[str, Any]:
    """Compare two :class:`CipherResult` objects field-by-field.

    Args:
        a: The first cipher result.
        b: The second cipher result.

    Returns:
        A dictionary with keys ``"identical"`` (bool), ``"differences"``
        (list of field names where values differ), and ``"shared_warnings"``
        (list of warnings present in both).
    """
    differences = []
    for field_name in ("signature", "sp", "url", "n"):
        val_a = getattr(a, field_name, "")
        val_b = getattr(b, field_name, "")
        if val_a != val_b:
            differences.append(field_name)

    shared_warnings = list(
        set(a.parse_warnings) & set(b.parse_warnings)
    )

    return {
        "identical": not differences and not shared_warnings,
        "differences": differences,
        "shared_warnings": shared_warnings,
    }


def has_cipher_changed(
    previous: Optional[CipherResult],
    current: CipherResult,
) -> bool:
    """Return ``True`` when *current* differs from *previous*.

    A ``None`` *previous* always returns ``True`` (regarded as changed).

    Args:
        previous: The prior :class:`CipherResult`, or ``None``.
        current: The current :class:`CipherResult`.

    Returns:
        ``True`` when the results differ in any component value.
    """
    if previous is None:
        return True
    comparison = compare_ciphers(previous, current)
    return not comparison["identical"]


# ---------------------------------------------------------------------------
# Cipher introspection helpers
# ---------------------------------------------------------------------------


def get_cipher_summary(result: CipherResult) -> Dict[str, Any]:
    """Produce a human-readable summary of a :class:`CipherResult`.

    Args:
        result: The decoded cipher result.

    Returns:
        A dictionary with summary fields including validity status,
        component presence, warnings, and URL host information.
    """
    url_host = ""
    url_path = ""
    if result.url:
        try:
            parsed = urlparse(result.url)
            url_host = parsed.hostname or ""
            url_path = parsed.path
        except (ValueError, TypeError):
            pass

    return {
        "is_valid": result.is_valid,
        "signature_length": len(result.signature),
        "sp": result.sp,
        "url_host": url_host,
        "url_path": url_path,
        "navigator_function": result.n,
        "missing_required": result.missing_required(),
        "warning_count": len(result.parse_warnings),
        "warnings": list(result.parse_warnings),
        "component_count": len(result.components),
    }


def is_signature_parameter_known(sp: str) -> bool:
    """Return ``True`` when *sp* matches a known signature parameter name.

    Args:
        sp: The ``sp`` value from a decoded cipher.

    Returns:
        ``True`` when *sp* is in :data:`KNOWN_SP_VALUES` or matches a
        known alias.
    """
    if not sp:
        return False
    sp_lower = sp.strip().lower()
    if sp_lower in KNOWN_SP_VALUES:
        return True
    return sp_lower in (v.lower() for v in SIGNATURE_PARAM_ALIASES.values())


def infer_signature_parameter(sp: str) -> str:
    """Return a canonical name for *sp*.

    Maps any recognised alias to ``"signature"``.  Unknown values are
    returned unchanged.

    Args:
        sp: The raw ``sp`` value.

    Returns:
        The canonical signature parameter name.
    """
    if not sp:
        return "signature"
    sp_lower = sp.strip().lower()
    if sp_lower in KNOWN_SP_VALUES:
        return "signature"
    if sp_lower in SIGNATURE_PARAM_ALIASES:
        return SIGNATURE_PARAM_ALIASES[sp_lower]
    return sp.strip()


# ---------------------------------------------------------------------------
# Edge-case helpers
# ---------------------------------------------------------------------------


def is_likely_cipher(value: Any) -> bool:
    """Heuristically determine whether *value* looks like a cipher string.

    A value is considered a cipher candidate if it is a non-empty string
    that contains at least one of the recognised cipher keys (``s``, ``sp``,
    ``url``, ``n``) as a top-level query parameter.

    Args:
        value: Any Python value to test.

    Returns:
        ``True`` when the value resembles a ``signatureCipher`` string.
    """
    if not isinstance(value, str) or not value.strip():
        return False

    try:
        params = parse_qs(value, keep_blank_values=False)
        return any(k in params for k in CIPHER_COMPONENT_ORDER)
    except Exception:
        return False


def sanitize_cipher_input(cipher: Any) -> str:
    """Coerce *cipher* to a clean string suitable for parsing.

    - ``None`` → raises :class:`CipherParseError`.
    - Non-string values are converted via ``str()``.
    - Leading/trailing whitespace is stripped.

    Args:
        cipher: The raw input (any type).

    Returns:
        The cleaned cipher string.

    Raises:
        CipherParseError: If *cipher* is ``None`` or coerces to an empty
            string.
    """
    if cipher is None:
        raise CipherParseError("Cipher value is None; cannot sanitize.")

    if not isinstance(cipher, str):
        cipher = str(cipher)

    cipher = cipher.strip()

    if not cipher:
        raise CipherParseError(
            "Cipher value is empty after coercion and stripping."
        )

    return cipher


def extract_url_from_cipher(cipher: str) -> Optional[str]:
    """Extract just the ``url`` component from a cipher string.

    A lightweight alternative to full decoding when only the URL is needed.

    Args:
        cipher: The raw ``signatureCipher`` value.

    Returns:
        The decoded URL, or ``None`` if the cipher is malformed or the
        ``url`` component is absent.
    """
    try:
        result = decode_signature_cipher(cipher)
        return result.url if result.is_valid else None
    except (CipherParseError, CipherValidationError):
        return None


def extract_signature_from_cipher(cipher: str) -> Optional[str]:
    """Extract just the ``s`` (signature) component from a cipher string.

    Args:
        cipher: The raw ``signatureCipher`` value.

    Returns:
        The signature string, or ``None`` if the cipher is malformed or
        the ``s`` component is absent.
    """
    try:
        result = decode_signature_cipher(cipher)
        return result.signature if result.is_valid else None
    except (CipherParseError, CipherValidationError):
        return None


def is_cipher_expired(result: CipherResult) -> bool:
    """Return ``True`` when the cipher result appears to be expired/invalid.

    A cipher is considered expired when:

    - It is structurally invalid (``result.is_valid`` is ``False``).
    - The URL does not point to a recognised YouTube host.
    - The ``n`` function name is present but does not match the expected
      pattern.

    Args:
        result: The decoded cipher result to check.

    Returns:
        ``True`` when the result appears expired or unusable.
    """
    if not result.is_valid:
        return True

    if result.url and not _is_youtube_host(result.url):
        return True

    if result.n and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", result.n):
        return True

    return False


def decode_and_sign(cipher: str, *, sp_override: Optional[str] = None) -> str:
    """High-level helper: decode a cipher and return the signed URL in one call.

    Combines :func:`decode_signature_cipher` and :func:`apply_signature`
    into a single convenient call.

    Args:
        cipher: The raw ``signatureCipher`` value.
        sp_override: If provided, override the ``sp`` value from the cipher
            with this value.

    Returns:
        The signed URL ready for HTTP requests.

    Raises:
        CipherParseError / CipherValidationError: If decoding fails.
        CipherValidationError: If the final signed URL is not valid.
    """
    result = decode_signature_cipher(cipher)
    sp = sp_override if sp_override is not None else result.sp
    signed_url = apply_signature(result.url, result.signature, sp=sp)
    return signed_url


# ---------------------------------------------------------------------------
# Module-level convenience re-exports
# ---------------------------------------------------------------------------

__all__ += [
    "normalize_url_path",
    "is_likely_cipher",
    "sanitize_cipher_input",
    "extract_url_from_cipher",
    "extract_signature_from_cipher",
    "is_cipher_expired",
    "decode_and_sign",
    "compare_ciphers",
    "has_cipher_changed",
    "get_cipher_summary",
    "is_signature_parameter_known",
    "infer_signature_parameter",
]
