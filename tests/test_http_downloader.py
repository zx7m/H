import pytest
import os
from unittest.mock import patch, MagicMock, mock_open
from ytdownloader.http_downloader import (
    _sanitize_filename,
    compute_output_path,
    _build_base_headers,
    _make_session,
    _retry_delay,
    _get_content_length,
    _try_resolve_n,
    _http_get_with_retry,
    download_stream,
)
from ytdownloader.exceptions import DownloadError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def video_info_dict():
    return {
        "title": "My Test Video: Special <Chars> | 2024",
        "id": "abc123XYZ",
        "videoDetails": {
            "title": "My Test Video: Special <Chars> | 2024",
            "videoId": "abc123XYZ",
        },
    }


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_safe_string_unchanged(self):
        assert _sanitize_filename("hello_world") == "hello_world"

    def test_unsafe_chars_replaced(self):
        result = _sanitize_filename('file<name>:"test|vid?.mp4')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert "|" not in result
        assert "?" not in result

    def test_strips_leading_trailing_dots_spaces(self):
        assert _sanitize_filename("  .hello.  ") == "hello"

    def test_empty_returns_download(self):
        assert _sanitize_filename("") == "download"

    def test_only_unsafe_chars_returns_download(self):
        result = _sanitize_filename('<>:"/\\|?*')
        assert result == "download"


# ---------------------------------------------------------------------------
# compute_output_path
# ---------------------------------------------------------------------------

class TestComputeOutputPath:
    def test_basic_output_path(self, video_info_dict):
        result = compute_output_path(video_info_dict, output_dir="/tmp", ext="mp4")
        assert result.startswith("/tmp")
        assert result.endswith(".mp4")
        assert "abc123XYZ" in result

    def test_video_id_from_nested_dict(self):
        info = {"videoDetails": {"title": "Test", "videoId": "xyz789"}}
        result = compute_output_path(info, output_dir=".", ext="webm")
        assert "xyz789" in result

    def test_fallback_id(self):
        info = {"title": "Test"}
        result = compute_output_path(info, output_dir=".", ext="mp4")
        assert "unknown" in result

    def test_fallback_title(self):
        info = {"id": "abc123"}
        result = compute_output_path(info, output_dir=".", ext="mp4")
        assert "untitled" in result

    def test_ext_stripped_of_dot(self, video_info_dict):
        result = compute_output_path(video_info_dict, output_dir=".", ext=".mp4")
        assert not result.endswith("..mp4")

    def test_filename_format(self, video_info_dict):
        result = compute_output_path(video_info_dict, output_dir=".", ext="mp4")
        basename = os.path.basename(result)
        assert "abc123XYZ" in basename
        assert basename.endswith(".mp4")


# ---------------------------------------------------------------------------
# _build_base_headers
# ---------------------------------------------------------------------------

class TestBuildBaseHeaders:
    def test_returns_dict_with_standard_headers(self):
        headers = _build_base_headers("https://example.com/videoplayback")
        assert "User-Agent" in headers
        assert "Referer" in headers
        assert "Origin" in headers

    def test_youtube_referer(self):
        headers = _build_base_headers("https://www.youtube.com/videoplayback")
        assert headers["Referer"] == "https://www.youtube.com/"

    def test_custom_cookies_added(self):
        headers = _build_base_headers(
            "https://example.com/videoplayback",
            cookies={"session": "abc123"},
        )
        assert "Cookie" in headers
        assert "session=abc123" in headers["Cookie"]

    def test_custom_user_agent(self):
        custom_ua = "CustomAgent/1.0"
        headers = _build_base_headers(
            "https://example.com/videoplayback",
            user_agent=custom_ua,
        )
        assert headers["User-Agent"] == custom_ua

    def test_extra_headers_merged(self):
        headers = _build_base_headers(
            "https://example.com/videoplayback",
            extra_headers={"X-Custom": "value"},
        )
        assert headers["X-Custom"] == "value"


# ---------------------------------------------------------------------------
# _make_session
# ---------------------------------------------------------------------------

class TestMakeSession:
    def test_creates_session_with_headers(self):
        import requests
        headers = {"User-Agent": "TestAgent"}
        session = _make_session(headers)
        assert isinstance(session, requests.Session)
        assert session.headers["User-Agent"] == "TestAgent"


# ---------------------------------------------------------------------------
# _retry_delay
# ---------------------------------------------------------------------------

class TestRetryDelay:
    def test_attempt_zero(self):
        assert _retry_delay(0) == 1.0

    def test_attempt_one(self):
        assert _retry_delay(1) == 2.0

    def test_attempt_two(self):
        assert _retry_delay(2) == 4.0

    def test_custom_base(self):
        assert _retry_delay(1, base=2.0) == 4.0


# ---------------------------------------------------------------------------
# _get_content_length
# ---------------------------------------------------------------------------

class TestGetContentLength:
    def test_content_length_header(self):
        headers = {"Content-Length": "10485760"}
        assert _get_content_length(headers) == 10485760

    def test_lowercase_header(self):
        headers = {"content-length": "5000"}
        assert _get_content_length(headers) == 5000

    def test_missing_header(self):
        assert _get_content_length({}) is None

    def test_invalid_value(self):
        headers = {"Content-Length": "not_a_number"}
        assert _get_content_length(headers) is None


# ---------------------------------------------------------------------------
# _try_resolve_n
# ---------------------------------------------------------------------------

class TestTryResolveN:
    def test_no_n_param_returns_original(self):
        result = _try_resolve_n("https://example.com/video", "https://www.youtube.com/s/player/abc/base.js")
        assert result == "https://example.com/video"

    def test_n_param_present_calls_resolver(self):
        url = "https://example.com/video?n=Ed7FM_"
        with patch("ytdownloader.http_downloader.resolve_n_param", return_value="resolved_n") as mock_resolve:
            result = _try_resolve_n(url, "https://www.youtube.com/s/player/abc/base.js")
        assert "n=resolved_n" in result
        mock_resolve.assert_called_once()

    def test_no_js_url_returns_original(self):
        url = "https://example.com/video?n=Ed7FM_"
        result = _try_resolve_n(url, None)
        assert result == url

    def test_resolver_failure_returns_original(self):
        url = "https://example.com/video?n=Ed7FM_"
        with patch("ytdownloader.http_downloader.resolve_n_param", side_effect=Exception("fail")):
            result = _try_resolve_n(url, "https://www.youtube.com/s/player/abc/base.js")
        assert result == url


# ---------------------------------------------------------------------------
# _http_get_with_retry
# ---------------------------------------------------------------------------

class TestHttpGetWithRetry:
    def test_success_first_attempt(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        result = _http_get_with_retry(
            "https://example.com/file",
            mock_session,
            max_retries=3,
        )
        assert result == mock_response
        assert mock_session.get.call_count == 1

    def test_403_then_success(self):
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.headers = {}
        mock_403.raise_for_status.side_effect = None

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.headers = {}

        mock_session = MagicMock()
        mock_session.get.side_effect = [mock_403, mock_200]

        with patch("ytdownloader.http_downloader.time.sleep"):
            result = _http_get_with_retry(
                "https://example.com/file",
                mock_session,
                max_retries=5,
            )
        assert result == mock_200

    def test_raises_download_error_after_max_retries(self):
        mock_fail = MagicMock()
        mock_fail.status_code = 503
        mock_fail.headers = {}
        mock_fail.raise_for_status.side_effect = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_fail

        with patch("ytdownloader.http_downloader.time.sleep"):
            with pytest.raises(DownloadError, match="HTTP 503"):
                _http_get_with_retry(
                    "https://example.com/file",
                    mock_session,
                    max_retries=2,
                )

    def test_rotates_user_agent_on_retry(self):
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.headers = {}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_403

        with patch("ytdownloader.http_downloader.time.sleep"):
            with pytest.raises(DownloadError):
                _http_get_with_retry(
                    "https://example.com/file",
                    mock_session,
                    max_retries=1,
                )

        call_kwargs = mock_session.get.call_args_list[-1][1]
        assert "headers" in call_kwargs


# ---------------------------------------------------------------------------
# download_stream
# ---------------------------------------------------------------------------

class TestDownloadStream:
    def test_download_success(self, tmp_path):
        output_file = str(tmp_path / "output.mp4")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.iter_content.return_value = [b"x" * 100]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        with patch("ytdownloader.http_downloader._http_get_with_retry", return_value=mock_response):
            with patch("ytdownloader.http_downloader._make_session", return_value=mock_session):
                with patch("ytdownloader.http_downloader._build_base_headers", return_value={}):
                    with patch("os.makedirs"):
                        result = download_stream(
                            "https://example.com/video.mp4",
                            output_file,
                            max_retries=1,
                        )

        assert result == output_file
        assert os.path.exists(output_file)

    def test_download_with_progress_callback(self, tmp_path):
        output_file = str(tmp_path / "output.mp4")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.iter_content.return_value = [b"x" * 100]

        progress_calls = []
        def progress_callback(downloaded, total, speed):
            progress_calls.append((downloaded, total, speed))

        with patch("ytdownloader.http_downloader._http_get_with_retry", return_value=mock_response):
            with patch("ytdownloader.http_downloader._make_session", return_value=MagicMock()):
                with patch("ytdownloader.http_downloader._build_base_headers", return_value={}):
                    with patch("os.makedirs"):
                        download_stream(
                            "https://example.com/video.mp4",
                            output_file,
                            progress_callback=progress_callback,
                            max_retries=1,
                        )

        assert len(progress_calls) >= 1

    def test_download_handles_request_exception(self, tmp_path):
        output_file = str(tmp_path / "output.mp4")
        with patch("ytdownloader.http_downloader._http_get_with_retry", side_effect=DownloadError("fail")):
            with pytest.raises(DownloadError):
                download_stream(
                    "https://example.com/video.mp4",
                    output_file,
                    max_retries=1,
                )
