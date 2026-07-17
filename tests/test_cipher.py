import pytest
from unittest.mock import patch, MagicMock
from ytdownloader.cipher import (
    parse_signature_cipher,
    apply_signature,
    _find_balanced,
    _normalize_js_body,
    _extract_operations,
    _apply_operations,
    _find_decipher_function,
    CipherResolver,
)
from ytdownloader.exceptions import SignatureCipherError


class TestParseSignatureCipher:
    def test_valid_cipher_parsed(self):
        raw = "url=https%3A%2F%2Fexample.com%2Fvid&s=abc123&sp=signature&n=Ed7FM_"
        result = parse_signature_cipher(raw)
        assert result["url"] == "https://example.com/vid"
        assert result["s"] == "abc123"
        assert result["sp"] == "signature"
        assert result["n"] == "Ed7FM_"

    def test_cipher_without_n(self):
        raw = "url=https%3A%2F%2Fexample.com%2Fvid&s=abc123&sp=signature"
        result = parse_signature_cipher(raw)
        assert result["n"] is None

    def test_cipher_default_sp(self):
        raw = "url=https%3A%2F%2Fexample.com%2Fvid&s=abc123"
        result = parse_signature_cipher(raw)
        assert result["sp"] == "signature"

    def test_empty_cipher_raises(self):
        with pytest.raises(SignatureCipherError, match="Empty signatureCipher"):
            parse_signature_cipher("")

    def test_missing_url_raises(self):
        with pytest.raises(SignatureCipherError, match="missing required 'url'"):
            parse_signature_cipher("s=abc123&sp=signature")

    def test_missing_s_raises(self):
        with pytest.raises(SignatureCipherError, match="missing required 's'"):
            parse_signature_cipher("url=https%3A%2F%2Fexample.com")


class TestApplySignature:
    def test_basic_signature_appended(self):
        url = "https://example.com/videoplayback"
        result = apply_signature(url, "abc123", "signature")
        assert result == "https://example.com/videoplayback?signature=abc123"

    def test_ampersand_delimiter_when_params_exist(self):
        url = "https://example.com/videoplayback?itag=18"
        result = apply_signature(url, "abc123", "signature")
        assert "&signature=abc123" in result

    def test_custom_sp_param(self):
        url = "https://example.com/videoplayback"
        result = apply_signature(url, "abc123", "s")
        assert "?s=abc123" in result


class TestFindBalanced:
    def test_simple_balanced(self):
        text = "{ hello } world"
        pos = _find_balanced(text, 0)
        assert pos == len("{ hello }")

    def test_nested_balanced(self):
        text = "{ a { b } c } rest"
        pos = _find_balanced(text, 0)
        assert pos == len("{ a { b } c }")

    def test_unbalanced_returns_minus_one(self):
        text = "{ unbalanced"
        assert _find_balanced(text, 0) == -1

    def test_empty_content(self):
        text = "{} rest"
        pos = _find_balanced(text, 0)
        assert pos == 2


class TestNormalizeJsBody:
    def test_removes_single_line_comment(self):
        body = "var x = 1; // this is a comment\nvar y = 2;"
        result = _normalize_js_body(body)
        assert "//" not in result

    def test_removes_multi_line_comment(self):
        body = "var x = 1; /* block comment */ var y = 2;"
        result = _normalize_js_body(body)
        assert "/*" not in result
        assert "*/" not in result

    def test_collapses_whitespace(self):
        body = "var   x   =   1  ;"
        result = _normalize_js_body(body)
        assert "   " not in result

    def test_preserves_code(self):
        body = "a = a.split('').reverse().join('');"
        result = _normalize_js_body(body)
        assert "split" in result
        assert "reverse" in result


class TestExtractOperations:
    def test_reverse_operation(self):
        body = "a = a.reverse();"
        ops = _extract_operations(body)
        assert any(op["op"] == "reverse" for op in ops)

    def test_slice_operation(self):
        body = "a = a.slice(3);"
        ops = _extract_operations(body)
        assert any(op["op"] == "slice" and op["arg"] == 3 for op in ops)

    def test_splice_operation(self):
        body = "a = a.splice(0, 2);"
        ops = _extract_operations(body)
        assert any(op["op"] == "splice" and op["arg"] == 2 for op in ops)

    def test_split_join_envelope(self):
        body = "a = a.split('').join('');"
        ops = _extract_operations(body)
        assert any(op["op"] == "split_join" for op in ops)

    def test_swap_operation_two_indices(self):
        body = "var a = [a[5], a[1]];"
        ops = _extract_operations(body)
        assert all(op["op"] != "swap" for op in ops)

    def test_empty_body_returns_empty_list(self):
        ops = _extract_operations("")
        assert ops == []


class TestApplyOperations:
    def test_reverse_operation(self):
        ops = [{"op": "reverse"}]
        assert _apply_operations("hello", ops) == "olleh"

    def test_slice_operation(self):
        ops = [{"op": "slice", "arg": 3}]
        assert _apply_operations("hello", ops) == "lo"

    def test_splice_operation(self):
        ops = [{"op": "splice", "arg": 2}]
        assert _apply_operations("hello", ops) == "llo"

    def test_swap_operation(self):
        ops = [{"op": "swap", "idx1": 0, "idx2": 4}]
        assert _apply_operations("hello", ops) == "oellh"

    def test_split_join_is_noop(self):
        ops = [{"op": "split_join"}]
        assert _apply_operations("hello", ops) == "hello"

    def test_no_operations_returns_original(self):
        assert _apply_operations("hello", []) == "hello"


class TestFindDecipherFunction:
    def test_finds_known_method(self):
        js = (
            "var decipher = function(a) {"
            " a = a.split('');"
            " a = a.reverse();"
            " a = a.join('');"
            " return a;"
            "};"
        )
        result = _find_decipher_function(js)
        assert result is not None
        name, body = result
        assert name == "decipher"
        assert "reverse" in body

    def test_returns_none_for_no_function(self):
        js = "var x = 1; var y = 2;"
        result = _find_decipher_function(js)
        assert result is None


class TestCipherResolver:
    def test_init_stores_js_url(self):
        resolver = CipherResolver("https://www.youtube.com/s/player/abc/base.js")
        assert resolver.js_url == "https://www.youtube.com/s/player/abc/base.js"

    def test_init_cache_js_default(self):
        resolver = CipherResolver("https://www.youtube.com/s/player/abc/base.js")
        assert resolver._cache_js is True

    def test_decipher_signature_with_mock_js(self):
        js = (
            "var C = function(a) {"
            " a = a.split('');"
            " a = a.reverse();"
            " a = a.join('');"
            " return a;"
            "};"
        )
        resolver = CipherResolver("https://www.youtube.com/s/player/abc/base.js")
        with patch("ytdownloader.cipher._fetch_player_js", return_value=js):
            result = resolver.decipher_signature("hello")
        assert result == "olleh"

    def test_resolve_with_mock_js(self):
        js = (
            "var C = function(a) {"
            " a = a.split('');"
            " a = a.reverse();"
            " a = a.join('');"
            " return a;"
            "};"
        )
        resolver = CipherResolver("https://www.youtube.com/s/player/abc/base.js")
        cipher_str = "url=https%3A%2F%2Fexample.com%2Fv&s=hello&sp=signature"
        with patch("ytdownloader.cipher._fetch_player_js", return_value=js):
            result = resolver.resolve(cipher_str)
        assert "signature=olleh" in result

    def test_ensure_loaded_caches_js(self):
        js = (
            "var C = function(a) {"
            " a = a.split('');"
            " a = a.reverse();"
            " a = a.join('');"
            " return a;"
            "};"
        )
        resolver = CipherResolver("https://www.youtube.com/s/player/abc/base.js")
        with patch("ytdownloader.cipher._fetch_player_js", return_value=js) as mock_dl:
            resolver._ensure_loaded()
            resolver._ensure_loaded()
        assert mock_dl.call_count == 1
