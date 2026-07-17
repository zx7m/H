"""
Real integration tests for ytdownloader.exceptions.

Tests all 16 custom exceptions can be instantiated, are subclasses of
YTDLException, and propagate messages correctly.
"""

from __future__ import annotations

import pytest

from ytdownloader.exceptions import (
    YTDLException,
    InvalidURLError,
    VideoUnavailableError,
    AgeRestrictedError,
    GeoRestrictedError,
    NetworkError,
    DownloadError,
    FormatSelectionError,
    SignatureCipherError,
    NResolverError,
    MetadataExtractionError,
    StreamResolutionError,
    SubtitleError,
    MergeError,
    CacheError,
    ConfigError,
)

ALL_EXCEPTIONS = [
    InvalidURLError,
    VideoUnavailableError,
    AgeRestrictedError,
    GeoRestrictedError,
    NetworkError,
    DownloadError,
    FormatSelectionError,
    SignatureCipherError,
    NResolverError,
    MetadataExtractionError,
    StreamResolutionError,
    SubtitleError,
    MergeError,
    CacheError,
    ConfigError,
]


class TestAllExceptionsInstantiable:
    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_exception_can_be_instantiated_with_message(self, exc_cls):
        exc = exc_cls("test message")
        assert exc is not None

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_exception_default_message(self, exc_cls):
        exc = exc_cls()
        assert str(exc) == ""

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_exception_empty_message(self, exc_cls):
        exc = exc_cls("")
        assert str(exc) == ""


class TestExceptionsAreSubclasses:
    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_is_subclass_of_ytdl_exception(self, exc_cls):
        assert issubclass(exc_cls, YTDLException)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_is_subclass_of_exception(self, exc_cls):
        assert issubclass(exc_cls, Exception)

    def test_base_ytdl_exception_is_exception(self):
        assert issubclass(YTDLException, Exception)

    def test_count_exceptions(self):
        assert len(ALL_EXCEPTIONS) == 15

    def test_ytdl_exception_not_in_all_exceptions(self):
        assert YTDLException not in ALL_EXCEPTIONS


class TestExceptionMessagePropagation:
    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_message_propagates_to_str(self, exc_cls):
        msg = f"something went wrong in {exc_cls.__name__}"
        exc = exc_cls(msg)
        assert str(exc) == msg

    def test_ytdl_base_message_propagation(self):
        exc = YTDLException("base error")
        assert str(exc) == "base error"

    def test_raise_and_catch_specific(self):
        with pytest.raises(InvalidURLError, match="bad url"):
            raise InvalidURLError("bad url")

    def test_raise_and_catch_base(self):
        with pytest.raises(YTDLException):
            raise VideoUnavailableError("removed")

    def test_raise_and_catch_exception(self):
        with pytest.raises(Exception):
            raise DownloadError("network failed")

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_raise_and_catch_specific_parametrized(self, exc_cls):
        with pytest.raises(exc_cls):
            raise exc_cls("error occurred")

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_raise_and_catch_base_parametrized(self, exc_cls):
        with pytest.raises(YTDLException):
            raise exc_cls("error occurred")


class TestYTDLExceptionCause:
    def test_cause_none_by_default(self):
        exc = YTDLException("msg")
        assert exc.cause is None

    def test_cause_propagated(self):
        cause = RuntimeError("underlying failure")
        exc = YTDLException("wrapper", cause=cause)
        assert exc.cause is cause

    def test_str_includes_cause(self):
        cause = ValueError("root cause")
        exc = YTDLException("wrapper", cause=cause)
        result = str(exc)
        assert "wrapper" in result
        assert "root cause" in result
        assert exc.cause is cause

    def test_str_without_cause(self):
        exc = YTDLException("just a message")
        assert str(exc) == "just a message"
