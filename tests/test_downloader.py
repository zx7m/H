import pytest
from unittest.mock import patch, MagicMock
from ytdownloader.downloader import (
    download_video,
    download_audio,
    get_video_info_wrapper,
    print_video_info,
    _print_metadata,
    _format_sort_key,
    _format_size,
)
from ytdownloader.exceptions import StreamResolutionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_VIDEO_ID = "abcdefghijk"  # 11 chars

@pytest.fixture
def sample_video_info():
    return {
        "title": "My Test Video: Special <Chars> | 2024",
        "id": VALID_VIDEO_ID,
        "author": "Test Channel",
        "channel_id": "UC_test_channel",
        "duration": 300,
        "view_count": 10000,
        "keywords": ["test", "video", "demo"],
        "thumbnail": [{"url": "https://example.com/thumb.jpg"}],
        "streaming_data": {
            "formats": [
                {
                    "itag": 18,
                    "ext": "mp4",
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "width": 640,
                    "height": 360,
                    "fps": 30,
                    "tbr": 500.0,
                    "abr": 128.0,
                    "vbr": 372.0,
                    "protocol": "http",
                    "url": "https://example.com/video18",
                    "contentLength": "5242880",
                    "approxDurationMs": "300000",
                    "mimeType": "video/mp4; codecs=\"avc1, mp4a.40.2\"",
                }
            ],
            "adaptiveFormats": [
                {
                    "itag": 140,
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a",
                    "width": None,
                    "height": None,
                    "fps": None,
                    "tbr": 128.0,
                    "abr": 128.0,
                    "vbr": 0.0,
                    "protocol": "http",
                    "url": "https://example.com/audio140",
                    "contentLength": "1572864",
                    "approxDurationMs": "300000",
                    "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
                }
            ],
        },
        "assets": {"js": "https://www.youtube.com/s/player/abc123/base.js"},
    }


def _make_valid_url(video_id=VALID_VIDEO_ID):
    return f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# _format_sort_key
# ---------------------------------------------------------------------------

class TestFormatSortKey:
    def test_combined_format_rank_zero(self):
        fmt = {"vcodec": "avc1", "acodec": "mp4a", "height": 720, "tbr": 1500}
        key = _format_sort_key(fmt)
        assert key[0] == 0

    def test_video_only_rank_one(self):
        fmt = {"vcodec": "vp9", "acodec": "none", "height": 1080, "tbr": 2500}
        key = _format_sort_key(fmt)
        assert key[0] == 1

    def test_audio_only_rank_two(self):
        fmt = {"vcodec": "none", "acodec": "opus", "height": 0, "tbr": 128}
        key = _format_sort_key(fmt)
        assert key[0] == 2

    def test_height_none_coerced_to_zero(self):
        fmt = {"vcodec": "none", "acodec": "none", "height": None, "tbr": 0}
        key = _format_sort_key(fmt)
        assert key[1] == 0


# ---------------------------------------------------------------------------
# _format_size
# ---------------------------------------------------------------------------

class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500 B"

    def test_kilobytes(self):
        assert _format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _format_size(5242880) == "5.0 MB"

    def test_gigabytes(self):
        assert _format_size(1073741824 + 1) == "1.0 GB"

    def test_none_returns_unknown(self):
        assert _format_size(None) == "unknown"

    def test_zero_returns_unknown(self):
        assert _format_size(0) == "unknown"

    def test_invalid_string_returns_unknown(self):
        assert _format_size("abc") == "unknown"


# ---------------------------------------------------------------------------
# _print_metadata
# ---------------------------------------------------------------------------

class TestPrintMetadata:
    def test_prints_title_and_id(self, capsys, sample_video_info):
        _print_metadata(sample_video_info)
        captured = capsys.readouterr()
        assert "My Test Video" in captured.out
        assert VALID_VIDEO_ID in captured.out

    def test_prints_author(self, capsys, sample_video_info):
        _print_metadata(sample_video_info)
        captured = capsys.readouterr()
        assert "Test Channel" in captured.out

    def test_prints_view_count(self, capsys, sample_video_info):
        _print_metadata(sample_video_info)
        captured = capsys.readouterr()
        assert "10000" in captured.out

    def test_prints_keywords(self, capsys, sample_video_info):
        _print_metadata(sample_video_info)
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_prints_formats_count(self, capsys, sample_video_info):
        _print_metadata(sample_video_info)
        captured = capsys.readouterr()
        assert "Available Formats" in captured.out
        assert "2" in captured.out


# ---------------------------------------------------------------------------
# get_video_info_wrapper
# ---------------------------------------------------------------------------

class TestGetVideoInfoWrapper:
    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid YouTube URL"):
            get_video_info_wrapper("not-a-youtube-url")

    def test_valid_url_calls_get_video_info(self):
        url = _make_valid_url()
        with patch("ytdownloader.downloader.is_valid_youtube_url", return_value=True):
            with patch("ytdownloader.downloader.get_video_info", return_value={"title": "Test"}) as mock_info:
                with patch("ytdownloader.downloader.normalize_youtube_url", return_value=url):
                    result = get_video_info_wrapper(url)
        assert result == {"title": "Test"}
        mock_info.assert_called_once()


# ---------------------------------------------------------------------------
# download_video (integration with mocked network)
# ---------------------------------------------------------------------------

class TestDownloadVideo:
    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid YouTube URL"):
            download_video("not-a-url", output_path=".")

    def test_download_video_success(self, tmp_path, sample_video_info):
        url = _make_valid_url()
        mock_info = dict(sample_video_info)
        mock_info["title"] = "Test Video"

        with patch("ytdownloader.downloader.is_valid_youtube_url", return_value=True):
            with patch("ytdownloader.downloader.normalize_youtube_url", return_value=url):
                with patch("ytdownloader.downloader.get_video_info", return_value=mock_info):
                    with patch("ytdownloader.downloader.download_video_from_info") as mock_dl:
                        mock_dl.return_value = str(tmp_path / f"Test Video [{VALID_VIDEO_ID}].mp4")
                        result = download_video(url, output_path=str(tmp_path), quiet=True)

        assert result.endswith(".mp4")
        assert VALID_VIDEO_ID in result
        mock_dl.assert_called_once()

    def test_download_video_prints_title(self, capsys):
        url = _make_valid_url("vid001")
        mock_info = {
            "title": "My Awesome Video",
            "id": "vid001",
            "streaming_data": {
                "formats": [{
                    "itag": 18, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
                    "width": 640, "height": 360, "fps": 30,
                    "tbr": 500.0, "abr": 128.0, "vbr": 372.0,
                    "protocol": "http", "url": "https://example.com/18",
                    "contentLength": "5242880", "approxDurationMs": "300000",
                    "mimeType": "video/mp4; codecs=\"avc1, mp4a\"",
                }],
                "adaptiveFormats": [],
            },
            "assets": {"js": None},
        }

        with patch("ytdownloader.downloader.is_valid_youtube_url", return_value=True):
            with patch("ytdownloader.downloader.normalize_youtube_url", return_value=url):
                with patch("ytdownloader.downloader.get_video_info", return_value=mock_info):
                    with patch("ytdownloader.downloader.download_video_from_info") as mock_dl:
                        mock_dl.return_value = "/tmp/test.mp4"
                        download_video(url, quiet=False)

        captured = capsys.readouterr()
        assert "My Awesome Video" in captured.out


# ---------------------------------------------------------------------------
# download_audio (integration with mocked network)
# ---------------------------------------------------------------------------

class TestDownloadAudio:
    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Invalid YouTube URL"):
            download_audio("not-a-url", output_path=".")

    def test_download_audio_success(self, tmp_path, sample_video_info):
        url = _make_valid_url()
        mock_info = dict(sample_video_info)
        mock_info["title"] = "Audio Test"

        with patch("ytdownloader.downloader.is_valid_youtube_url", return_value=True):
            with patch("ytdownloader.downloader.normalize_youtube_url", return_value=url):
                with patch("ytdownloader.downloader.get_video_info", return_value=mock_info):
                    with patch("ytdownloader.downloader.download_audio_from_info") as mock_dl:
                        mock_dl.return_value = str(tmp_path / f"Audio Test [{VALID_VIDEO_ID}].m4a")
                        result = download_audio(url, output_path=str(tmp_path), quiet=True)

        assert VALID_VIDEO_ID in result
        mock_dl.assert_called_once()

    def test_download_audio_prints_title(self, capsys):
        url = _make_valid_url("aud001")
        mock_info = {
            "title": "Audio Only Track",
            "id": "aud001",
            "streaming_data": {
                "formats": [],
                "adaptiveFormats": [{
                    "itag": 140, "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
                    "width": None, "height": None, "fps": None,
                    "tbr": 128.0, "abr": 128.0, "vbr": 0.0,
                    "protocol": "http", "url": "https://example.com/140",
                    "contentLength": "1572864", "approxDurationMs": "300000",
                    "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
                }],
            },
            "assets": {"js": None},
        }

        with patch("ytdownloader.downloader.is_valid_youtube_url", return_value=True):
            with patch("ytdownloader.downloader.normalize_youtube_url", return_value=url):
                with patch("ytdownloader.downloader.get_video_info", return_value=mock_info):
                    with patch("ytdownloader.downloader.download_audio_from_info") as mock_dl:
                        mock_dl.return_value = "/tmp/audio.m4a"
                        download_audio(url, quiet=False)

        captured = capsys.readouterr()
        assert "Audio Only Track" in captured.out


# ---------------------------------------------------------------------------
# print_video_info
# ---------------------------------------------------------------------------

class TestPrintVideoInfo:
    def test_calls_get_video_info_and_prints(self, capsys):
        mock_info = {"title": "Info Video", "id": "inf001"}
        with patch("ytdownloader.downloader.get_video_info_wrapper", return_value=mock_info) as mock_wrapper:
            with patch("ytdownloader.downloader._print_metadata") as mock_print:
                print_video_info("https://www.youtube.com/watch?v=inf001")

        mock_wrapper.assert_called_once_with("https://www.youtube.com/watch?v=inf001")
        mock_print.assert_called_once_with(mock_info)
