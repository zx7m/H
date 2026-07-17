import pytest
from unittest.mock import patch, MagicMock
from ytdownloader.n_resolver import (
    _resolve_js_url,
    _translate_n_function,
    _compute_n,
    NResolver,
    resolve_n_param,
)
from ytdownloader.exceptions import NResolverError


class TestResolveJsUrl:
    def test_absolute_https_url_passthrough(self):
        assert _resolve_js_url("https://www.youtube.com/s/player/abc/base.js") == \
            "https://www.youtube.com/s/player/abc/base.js"

    def test_absolute_http_url_passthrough(self):
        assert _resolve_js_url("http://www.youtube.com/s/player/abc/base.js") == \
            "http://www.youtube.com/s/player/abc/base.js"

    def test_relative_url_resolved(self):
        result = _resolve_js_url("/s/player/abc123/base.js")
        assert result == "https://www.youtube.com/s/player/abc123/base.js"

    def test_relative_url_no_leading_slash(self):
        result = _resolve_js_url("s/player/abc123/base.js")
        assert result == "https://www.youtube.com/s/player/abc123/base.js"


class TestTranslateNFunction:
    def test_reverse_pattern(self):
        js = 'a = a.split("").reverse().join("");'
        result = _translate_n_function(js)
        assert result == "s[::-1]"

    def test_splice_pattern(self):
        js = "a = a.splice(0, 3);"
        result = _translate_n_function(js)
        assert result == "s[3:]"

    def test_slice_single_arg(self):
        js = "a = a.slice(2);"
        result = _translate_n_function(js)
        assert result == "s[2:]"

    def test_slice_two_args(self):
        js = "a = a.slice(1, 4);"
        result = _translate_n_function(js)
        assert result == "s[1:4]"

    def test_swap_pattern(self):
        js = "a = [a[5], a[1], a[0], a[2], a[3], a[4]];"
        result = _translate_n_function(js)
        assert result == "s[5] + s[1:5] + s[0] + s[6:]"

    def test_swap_pattern_small_index(self):
        js = "a = [a[0], a[1], a[2]];"
        result = _translate_n_function(js)
        assert result == "s[0] + s[1:]"

    def test_unrecognized_returns_none(self):
        js = "a = someUnknownOperation(a);"
        result = _translate_n_function(js)
        assert result is None

    def test_complex_body_fallback(self):
        js = "a[0] = a[1]; a.length = 5;"
        result = _translate_n_function(js)
        assert result == "s"


class TestComputeN:
    def test_reverse_transformer(self):
        assert _compute_n("s[::-1]", "abc") == "cba"

    def test_slice_transformer(self):
        assert _compute_n("s[3:]", "Ed7FM_") == "FM_"

    def test_splice_transformer(self):
        assert _compute_n("s[2:]", "hello") == "llo"

    def test_identity_transformer(self):
        assert _compute_n("s", "hello") == "hello"

    def test_returns_string(self):
        result = _compute_n("s[::-1]", "test")
        assert isinstance(result, str)

    def test_bad_transformer_raises_n_resolver_error(self):
        with pytest.raises(NResolverError):
            _compute_n("s / 0", "test")

    def test_single_statement_transformer(self):
        assert _compute_n("s[2:]", "hello") == "llo"


class TestNResolver:
    def test_cache_initially_empty(self):
        resolver = NResolver()
        assert resolver._cache == {}

    def test_resolve_n_returns_input_on_n_resolver_error(self):
        resolver = NResolver()
        with patch("ytdownloader.n_resolver._download_js", side_effect=NResolverError("fail")):
            result = resolver.resolve_n("Ed7FM_", "/s/player/abc/base.js")
        assert result == "Ed7FM_"

    def test_resolve_n_caches_result(self):
        resolver = NResolver()
        mock_js = (
            '.get("n")) && (a = FRa[0](a));'
            "function FRa(a) { a = a.split('').reverse().join(''); return a; }"
        )
        func_info = {
            "name": "FRa",
            "body": "a = a.split('').reverse().join('');",
            "transformer": "s[::-1]",
        }
        with patch("ytdownloader.n_resolver._download_js", return_value=mock_js):
            with patch("ytdownloader.n_resolver._find_n_function_info", return_value=func_info):
                result1 = resolver.resolve_n("abc", "https://www.youtube.com/s/player/abc/base.js")
                result2 = resolver.resolve_n("abc", "https://www.youtube.com/s/player/abc/base.js")
        assert result1 == "cba"
        assert result2 == "cba"

    def test_resolve_n_with_known_transformer(self):
        resolver = NResolver()
        mock_js = (
            '.get("n")) && (a = FRa[0](a));'
            "function FRa(a) { a = a.split('').reverse().join(''); return a; }"
        )
        func_info = {
            "name": "FRa",
            "body": "a = a.split('').reverse().join('');",
            "transformer": "s[::-1]",
        }
        with patch("ytdownloader.n_resolver._download_js", return_value=mock_js):
            with patch("ytdownloader.n_resolver._find_n_function_info", return_value=func_info):
                result = resolver.resolve_n("Ed7FM_", "https://www.youtube.com/s/player/abc/base.js")
        assert result == "_MF7dE"

    def test_resolve_n_relative_url_resolved(self):
        resolver = NResolver()
        mock_js = '.get("n")) && (a = FRa(a)); function FRa(a) { return a; }'
        func_info = {
            "name": "FRa",
            "body": "return a;",
            "transformer": "s",
        }
        with patch("ytdownloader.n_resolver._download_js", return_value=mock_js) as mock_dl:
            with patch("ytdownloader.n_resolver._find_n_function_info", return_value=func_info):
                resolver.resolve_n("test", "/s/player/abc/base.js")
        assert "https://www.youtube.com" in mock_dl.call_args[0][0]


class TestResolveNParam:
    def test_module_level_function_returns_string(self):
        mock_js = (
            '.get("n")) && (a = FRa[0](a));'
            "function FRa(a) { a = a.split('').reverse().join(''); return a; }"
        )
        func_info = {
            "name": "FRa",
            "body": "a = a.split('').reverse().join('');",
            "transformer": "s[::-1]",
        }
        with patch("ytdownloader.n_resolver._download_js", return_value=mock_js):
            with patch("ytdownloader.n_resolver._find_n_function_info", return_value=func_info):
                result = resolve_n_param("https://www.youtube.com/s/player/abc/base.js", "Ed7FM_")
        assert isinstance(result, str)
        assert result == "_MF7dE"

    def test_module_level_function_falls_back_on_error(self):
        from ytdownloader.n_resolver import _resolver
        _resolver._cache.clear()
        with patch("ytdownloader.n_resolver._download_js", side_effect=NResolverError("network fail")):
            result = resolve_n_param("https://www.youtube.com/s/player/def/base.js", "Ed7FM_")
        assert result == "Ed7FM_"

    def test_module_level_caching(self):
        from ytdownloader.n_resolver import _resolver
        _resolver._cache.clear()
        mock_js = (
            '.get("n")) && (a = FRa[0](a));'
            "function FRa(a) { a = a.split('').reverse().join(''); return a; }"
        )
        func_info = {
            "name": "FRa",
            "body": "a = a.split('').reverse().join('');",
            "transformer": "s[::-1]",
        }
        with patch("ytdownloader.n_resolver._download_js", return_value=mock_js) as mock_dl:
            with patch("ytdownloader.n_resolver._find_n_function_info", return_value=func_info):
                resolve_n_param("https://www.youtube.com/s/player/ghi/base.js", "test1")
                resolve_n_param("https://www.youtube.com/s/player/ghi/base.js", "test2")
        assert mock_dl.call_count == 1
