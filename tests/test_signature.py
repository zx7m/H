"""
Real integration tests for signature/cipher handling in ytdownloader.

Tests verify that signature cipher URLs and n-parameter URLs are properly
handled by yt-dlp, and that SignatureCipherError is correctly defined.

All tests use real network calls - no mocking.
"""

from __future__ import annotations

import pytest

from ytdownloader import downloader
from ytdownloader.exceptions import SignatureCipherError, YTDLException
from ytdownloader.metadata import get_video_info
from ytdownloader.utils import is_valid_youtube_url


REAL_PUBLIC_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestSignatureParamDetection:
    def test_signature_cipher_url_validated(self, network_check):
        url_with_sig = REAL_PUBLIC_URL + "&signature=abc123"
        assert is_valid_youtube_url(url_with_sig) is True

    def test_signature_cipher_url_metadata(self, network_check):
        url_with_sig = REAL_PUBLIC_URL + "&signature=abc123"
        info = get_video_info(url_with_sig)
        assert isinstance(info, dict)
        assert "title" in info
        assert "id" in info


class TestNParamHandling:
    def test_n_param_url_validated(self, network_check):
        url_with_n = REAL_PUBLIC_URL + "&n=abc123"
        assert is_valid_youtube_url(url_with_n) is True

    def test_n_param_url_metadata(self, network_check):
        url_with_n = REAL_PUBLIC_URL + "&n=abc123"
        info = get_video_info(url_with_n)
        assert isinstance(info, dict)
        assert "title" in info

    def test_n_param_download_video(self, tmp_download_dir, yt_dlp_check, network_check):
        url_with_n = REAL_PUBLIC_URL + "&n=abc123"
        filename = downloader.download_video(url_with_n, output_path=str(tmp_download_dir), quiet=True)
        assert filename.endswith(".mp4")


class TestSignatureErrorRaised:
    def test_signature_cipher_error_is_exception(self):
        assert issubclass(SignatureCipherError, YTDLException)
        assert issubclass(SignatureCipherError, Exception)

    def test_signature_cipher_error_message(self):
        err = SignatureCipherError("Cannot decrypt signature")
        assert str(err) == "Cannot decrypt signature"
        assert err.cause is None

    def test_signature_cipher_error_with_cause(self):
        cause = RuntimeError("yt-dlp failed to decrypt")
        err = SignatureCipherError("Decryption failed", cause=cause)
        assert "Decryption failed" in str(err)
        assert "yt-dlp failed to decrypt" in str(err)
        assert err.cause is cause

    def test_signature_cipher_error_can_be_raised_and_caught(self):
        with pytest.raises(SignatureCipherError):
            raise SignatureCipherError("signature cipher failure")

    def test_signature_cipher_error_caught_as_ytdl_exception(self):
        with pytest.raises(YTDLException):
            raise SignatureCipherError("caught as base")

    def test_signature_cipher_error_caught_as_exception(self):
        with pytest.raises(Exception):
            raise SignatureCipherError("caught as Exception")
