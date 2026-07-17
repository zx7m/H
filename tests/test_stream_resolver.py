import pytest
from unittest.mock import patch, MagicMock
from ytdownloader.stream_resolver import (
    StreamFormat,
    _parse_mime_type,
    _parse_content_length,
    _estimate_size,
    _quality_ordinal,
    _derive_ext,
    parse_streaming_data,
    filter_formats,
    sort_formats,
    get_format_by_itag,
    get_audio_only_formats,
    get_video_only_formats,
    get_combined_formats,
    get_best_format,
)
from ytdownloader.exceptions import StreamResolutionError, FormatSelectionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_format_dict():
    return {
        "itag": 22,
        "mimeType": "video/mp4; codecs=\"avc1, mp4a.40.2\"",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "tbr": 1500.0,
        "abr": 128.0,
        "vbr": 1372.0,
        "protocol": "http",
        "url": "https://example.com/videoplayback?itag=22",
        "contentLength": "10485760",
        "approxDurationMs": "600000",
    }


@pytest.fixture
def audio_format_dict():
    return {
        "itag": 140,
        "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
        "width": None,
        "height": None,
        "fps": None,
        "tbr": 128.0,
        "abr": 128.0,
        "vbr": 0.0,
        "protocol": "http",
        "url": "https://example.com/videoplayback?itag=140",
        "contentLength": "3145728",
        "approxDurationMs": "600000",
    }


@pytest.fixture
def video_only_format_dict():
    return {
        "itag": 248,
        "mimeType": "video/webm; codecs=\"vp9\"",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "tbr": 2500.0,
        "abr": None,
        "vbr": 2500.0,
        "protocol": "https",
        "url": "https://example.com/videoplayback?itag=248",
        "contentLength": "20971520",
        "approxDurationMs": "600000",
    }


@pytest.fixture
def player_response_with_formats(sample_format_dict, audio_format_dict):
    return {
        "videoDetails": {
            "videoId": "test123",
            "title": "Test Video",
        },
        "streamingData": {
            "formats": [sample_format_dict],
            "adaptiveFormats": [audio_format_dict],
        },
    }


@pytest.fixture
def player_response_with_cipher(audio_format_dict):
    return {
        "videoDetails": {"videoId": "test123", "title": "Test Video"},
        "streamingData": {
            "formats": [],
            "adaptiveFormats": [
                {
                    **audio_format_dict,
                    "signatureCipher": "url=https%3A%2F%2Fexample.com%2Fv&s=enc_sig&sp=signature&n=raw_n",
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# _parse_mime_type
# ---------------------------------------------------------------------------

class TestParseMimeType:
    def test_video_mp4(self):
        container, vcodec, acodec = _parse_mime_type('video/mp4; codecs="avc1, mp4a"')
        assert container == "mp4"
        assert vcodec == "avc1"
        assert acodec == "mp4a"

    def test_video_webm_no_audio(self):
        container, vcodec, acodec = _parse_mime_type("video/webm; codecs=\"vp9\"")
        assert container == "webm"
        assert vcodec == "vp9"
        assert acodec == "none"

    def test_audio_webm(self):
        container, vcodec, acodec = _parse_mime_type("audio/webm; codecs=\"opus\"")
        assert container == "webm"
        assert vcodec == "none"
        assert acodec == "opus"

    def test_audio_mpeg(self):
        container, vcodec, acodec = _parse_mime_type("audio/mpeg")
        assert container == "mpeg"
        assert vcodec == "none"
        assert acodec == "none"

    def test_empty_mime(self):
        container, vcodec, acodec = _parse_mime_type("")
        assert container == "unknown"
        assert vcodec == "none"
        assert acodec == "none"

    def test_none_mime(self):
        container, vcodec, acodec = _parse_mime_type(None)
        assert container == "unknown"
        assert vcodec == "none"
        assert acodec == "none"


# ---------------------------------------------------------------------------
# _parse_content_length
# ---------------------------------------------------------------------------

class TestParseContentLength:
    def test_string_number(self):
        assert _parse_content_length("10485760") == 10485760

    def test_int_direct(self):
        assert _parse_content_length(10485760) == 10485760

    def test_none_returns_none(self):
        assert _parse_content_length(None) is None

    def test_invalid_string_returns_none(self):
        assert _parse_content_length("not_a_number") is None

    def test_empty_string_returns_none(self):
        assert _parse_content_length("") is None


# ---------------------------------------------------------------------------
# _estimate_size
# ---------------------------------------------------------------------------

class TestEstimateSize:
    def test_uses_content_length_when_available(self):
        result = _estimate_size(content_length=10000, duration_ms=None, tbr=None)
        assert result == 10000

    def test_calculates_from_duration_and_tbr(self):
        result = _estimate_size(content_length=None, duration_ms=600000, tbr=1500.0)
        expected = int(600000 / 1000.0 * 1500 * 1000 / 8.0)
        assert result == expected

    def test_none_inputs_return_none(self):
        assert _estimate_size(None, None, None) is None

    def test_zero_tbr_returns_zero(self):
        result = _estimate_size(None, 600000, 0.0)
        assert result == 0


# ---------------------------------------------------------------------------
# _quality_ordinal
# ---------------------------------------------------------------------------

class TestQualityOrdinal:
    def test_height_used_directly(self):
        assert _quality_ordinal(1080, "1080p") == 1080

    def test_none_height_falls_back_to_label_map(self):
        assert _quality_ordinal(None, "720p") == 720

    def test_audio_only_returns_zero(self):
        assert _quality_ordinal(None, "128kbps") == 0

    def test_unknown_label_returns_zero(self):
        assert _quality_ordinal(None, "unknown") == 0


# ---------------------------------------------------------------------------
# _derive_ext
# ---------------------------------------------------------------------------

class TestDeriveExt:
    def test_mp4_mime(self):
        assert _derive_ext("video/mp4", 22) == "mp4"

    def test_webm_mime(self):
        assert _derive_ext("video/webm", 248) == "webm"

    def test_audio_mp4(self):
        assert _derive_ext("audio/mp4", 140) == "m4a"

    def test_audio_mpeg(self):
        assert _derive_ext("audio/mpeg", 139) == "mp3"

    def test_empty_mime_defaults_to_mp4(self):
        assert _derive_ext("", 22) == "mp4"

    def test_fallback_to_itag_details(self):
        assert _derive_ext("unknown/type", 22) == "mp4"


# ---------------------------------------------------------------------------
# parse_streaming_data
# ---------------------------------------------------------------------------

class TestParseStreamingData:
    def test_parses_formats_and_adaptive(self, player_response_with_formats):
        with patch("ytdownloader.stream_resolver._resolve_stream_url", side_effect=lambda fmt, js: fmt.get("url", "")):
            result = parse_streaming_data(player_response_with_formats, js_url=None)
        assert len(result) == 2

    def test_missing_streaming_data_returns_empty(self):
        assert parse_streaming_data({}, js_url=None) == []

    def test_format_missing_itag_skipped(self):
        data = {
            "streamingData": {
                "formats": [{"url": "https://example.com/x", "mimeType": "video/mp4"}],
                "adaptiveFormats": [],
            }
        }
        with patch("ytdownloader.stream_resolver._resolve_stream_url", side_effect=lambda fmt, js: fmt.get("url", "")):
            result = parse_streaming_data(data, js_url=None)
        assert len(result) == 0

    def test_missing_streaming_data_key(self):
        data = {"videoDetails": {"videoId": "abc"}}
        result = parse_streaming_data(data, js_url=None)
        assert result == []

    def test_direct_streaming_data_dict(self):
        data = {
            "formats": [{"itag": 18, "url": "https://example.com/18", "mimeType": "video/mp4"}],
            "adaptiveFormats": [],
        }
        with patch("ytdownloader.stream_resolver._resolve_stream_url", side_effect=lambda fmt, js: fmt.get("url", "")):
            result = parse_streaming_data(data, js_url=None)
        assert len(result) == 1
        assert result[0].itag == 18


# ---------------------------------------------------------------------------
# filter_formats
# ---------------------------------------------------------------------------

class TestFilterFormats:
    @pytest.fixture
    def sample_formats(self):
        return [
            StreamFormat(
                itag=22, ext="mp4", vcodec="avc1", acodec="mp4a",
                width=1280, height=720, fps=30, tbr=1500.0, abr=128.0, vbr=1372.0,
                acontainer="mp4", vcontainer="mp4", mimeType="video/mp4",
                protocol="http", url="https://example.com/22",
                signature_cipher=None, content_length=10000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="720p", quality_ordinal=720,
                has_video=True, has_audio=True,
            ),
            StreamFormat(
                itag=140, ext="m4a", vcodec="none", acodec="mp4a",
                width=None, height=None, fps=None, tbr=128.0, abr=128.0, vbr=0.0,
                acontainer="m4a", vcontainer="m4a", mimeType="audio/mp4",
                protocol="http", url="https://example.com/140",
                signature_cipher=None, content_length=3000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="128kbps", quality_ordinal=0,
                has_video=False, has_audio=True,
            ),
            StreamFormat(
                itag=248, ext="webm", vcodec="vp9", acodec="none",
                width=1920, height=1080, fps=30, tbr=2500.0, abr=None, vbr=2500.0,
                acontainer="webm", vcontainer="webm", mimeType="video/webm",
                protocol="https", url="https://example.com/248",
                signature_cipher=None, content_length=20000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="1080p", quality_ordinal=1080,
                has_video=True, has_audio=False,
            ),
        ]

    def test_min_height_filter(self, sample_formats):
        result = filter_formats(sample_formats, min_height=720)
        assert all(sf.height is None or sf.height >= 720 for sf in result)

    def test_max_height_filter(self, sample_formats):
        result = filter_formats(sample_formats, max_height=720)
        assert all(sf.height is None or sf.height <= 720 for sf in result)

    def test_audio_only_filter(self, sample_formats):
        result = filter_formats(sample_formats, acodecs=["mp4a"])
        assert all(sf.acodec == "mp4a" for sf in result)

    def test_combined_filter(self, sample_formats):
        result = filter_formats(sample_formats, min_height=1080)
        assert all(sf.height is None or sf.height >= 1080 for sf in result)

    def test_empty_result_when_no_match(self, sample_formats):
        result = filter_formats(sample_formats, min_height=2160)
        assert result == []


# ---------------------------------------------------------------------------
# sort_formats
# ---------------------------------------------------------------------------

class TestSortFormats:
    @pytest.fixture
    def sample_formats(self):
        return [
            StreamFormat(
                itag=22, ext="mp4", vcodec="avc1", acodec="mp4a",
                width=1280, height=720, fps=30, tbr=1500.0, abr=128.0, vbr=1372.0,
                acontainer="mp4", vcontainer="mp4", mimeType="video/mp4",
                protocol="http", url="https://example.com/22",
                signature_cipher=None, content_length=10000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="720p", quality_ordinal=720,
                has_video=True, has_audio=True,
            ),
            StreamFormat(
                itag=140, ext="m4a", vcodec="none", acodec="mp4a",
                width=None, height=None, fps=None, tbr=128.0, abr=128.0, vbr=0.0,
                acontainer="m4a", vcontainer="m4a", mimeType="audio/mp4",
                protocol="http", url="https://example.com/140",
                signature_cipher=None, content_length=3000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="128kbps", quality_ordinal=0,
                has_video=False, has_audio=True,
            ),
            StreamFormat(
                itag=248, ext="webm", vcodec="vp9", acodec="none",
                width=1920, height=1080, fps=30, tbr=2500.0, abr=None, vbr=2500.0,
                acontainer="webm", vcontainer="webm", mimeType="video/webm",
                protocol="https", url="https://example.com/248",
                signature_cipher=None, content_length=20000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="1080p", quality_ordinal=1080,
                has_video=True, has_audio=False,
            ),
        ]

    def test_sort_by_quality_descending(self, sample_formats):
        result = sort_formats(sample_formats, key="quality")
        assert result[0].quality_ordinal >= result[1].quality_ordinal
        assert result[1].quality_ordinal >= result[2].quality_ordinal

    def test_sort_by_itag_ascending(self, sample_formats):
        result = sort_formats(sample_formats, key="itag")
        assert result[0].itag <= result[1].itag <= result[2].itag

    def test_sort_by_height_descending(self, sample_formats):
        result = sort_formats(sample_formats, key="height")
        heights = [sf.height or 0 for sf in result]
        assert heights == sorted(heights, reverse=True)


# ---------------------------------------------------------------------------
# get_format_by_itag
# ---------------------------------------------------------------------------

class TestGetFormatByItag:
    @pytest.fixture
    def sample_formats(self):
        return [
            StreamFormat(
                itag=22, ext="mp4", vcodec="avc1", acodec="mp4a",
                width=1280, height=720, fps=30, tbr=1500.0, abr=128.0, vbr=1372.0,
                acontainer="mp4", vcontainer="mp4", mimeType="video/mp4",
                protocol="http", url="https://example.com/22",
                signature_cipher=None, content_length=10000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="720p", quality_ordinal=720,
                has_video=True, has_audio=True,
            ),
        ]

    def test_finds_format_by_itag(self, sample_formats):
        result = get_format_by_itag(sample_formats, 22)
        assert result is not None
        assert result.itag == 22

    def test_returns_none_when_itag_not_found(self, sample_formats):
        assert get_format_by_itag(sample_formats, 999) is None


# ---------------------------------------------------------------------------
# Audio/video/combined filter helpers
# ---------------------------------------------------------------------------

class TestFormatFilterHelpers:
    @pytest.fixture
    def sample_formats(self):
        return [
            StreamFormat(
                itag=22, ext="mp4", vcodec="avc1", acodec="mp4a",
                width=1280, height=720, fps=30, tbr=1500.0, abr=128.0, vbr=1372.0,
                acontainer="mp4", vcontainer="mp4", mimeType="video/mp4",
                protocol="http", url="https://example.com/22",
                signature_cipher=None, content_length=10000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="720p", quality_ordinal=720,
                has_video=True, has_audio=True,
            ),
            StreamFormat(
                itag=140, ext="m4a", vcodec="none", acodec="mp4a",
                width=None, height=None, fps=None, tbr=128.0, abr=128.0, vbr=0.0,
                acontainer="m4a", vcontainer="m4a", mimeType="audio/mp4",
                protocol="http", url="https://example.com/140",
                signature_cipher=None, content_length=3000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="128kbps", quality_ordinal=0,
                has_video=False, has_audio=True,
            ),
            StreamFormat(
                itag=248, ext="webm", vcodec="vp9", acodec="none",
                width=1920, height=1080, fps=30, tbr=2500.0, abr=None, vbr=2500.0,
                acontainer="webm", vcontainer="webm", mimeType="video/webm",
                protocol="https", url="https://example.com/248",
                signature_cipher=None, content_length=20000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="1080p", quality_ordinal=1080,
                has_video=True, has_audio=False,
            ),
        ]

    def test_audio_only_formats(self, sample_formats):
        result = get_audio_only_formats(sample_formats)
        assert len(result) == 1
        assert result[0].itag == 140

    def test_video_only_formats(self, sample_formats):
        result = get_video_only_formats(sample_formats)
        assert len(result) == 1
        assert result[0].itag == 248

    def test_combined_formats(self, sample_formats):
        result = get_combined_formats(sample_formats)
        assert len(result) == 1
        assert result[0].itag == 22


# ---------------------------------------------------------------------------
# get_best_format
# ---------------------------------------------------------------------------

class TestGetBestFormat:
    @pytest.fixture
    def sample_formats(self):
        return [
            StreamFormat(
                itag=22, ext="mp4", vcodec="avc1", acodec="mp4a",
                width=1280, height=720, fps=30, tbr=1500.0, abr=128.0, vbr=1372.0,
                acontainer="mp4", vcontainer="mp4", mimeType="video/mp4",
                protocol="http", url="https://example.com/22",
                signature_cipher=None, content_length=10000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="720p", quality_ordinal=720,
                has_video=True, has_audio=True,
            ),
            StreamFormat(
                itag=248, ext="webm", vcodec="vp9", acodec="none",
                width=1920, height=1080, fps=30, tbr=2500.0, abr=None, vbr=2500.0,
                acontainer="webm", vcontainer="webm", mimeType="video/webm",
                protocol="https", url="https://example.com/248",
                signature_cipher=None, content_length=20000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="1080p", quality_ordinal=1080,
                has_video=True, has_audio=False,
            ),
            StreamFormat(
                itag=140, ext="m4a", vcodec="none", acodec="mp4a",
                width=None, height=None, fps=None, tbr=128.0, abr=128.0, vbr=0.0,
                acontainer="m4a", vcontainer="m4a", mimeType="audio/mp4",
                protocol="http", url="https://example.com/140",
                signature_cipher=None, content_length=3000000,
                approx_duration_ms=600000, is_dash=False, is_hls=False,
                quality_label="128kbps", quality_ordinal=0,
                has_video=False, has_audio=True,
            ),
        ]

    def test_best_quality_prefers_combined(self, sample_formats):
        result = get_best_format(sample_formats, quality="best", prefer_video=True, prefer_audio=True)
        assert result.has_video and result.has_audio

    def test_worst_quality_returns_lowest(self, sample_formats):
        result = get_best_format(sample_formats, quality="worst", prefer_video=True, prefer_audio=True)
        assert result.quality_ordinal == min(sf.quality_ordinal for sf in sample_formats)

    def test_empty_formats_raises(self):
        with pytest.raises(FormatSelectionError, match="No formats available"):
            get_best_format([])

    def test_unknown_quality_falls_back_to_best(self, sample_formats):
        result = get_best_format(sample_formats, quality="unknown_quality")
        assert result.quality_ordinal == max(sf.quality_ordinal for sf in sample_formats)

    def test_preferred_container_filter(self, sample_formats):
        result = get_best_format(sample_formats, preferred_container="mp4")
        assert all(sf.vcontainer == "mp4" or sf.acontainer == "mp4" for sf in [result])

    def test_preferred_vcodec_filter(self, sample_formats):
        result = get_best_format(sample_formats, preferred_vcodec="vp9")
        assert result.vcodec == "vp9"

    def test_audio_only_selection(self, sample_formats):
        result = get_best_format(sample_formats, quality="best", prefer_video=False, prefer_audio=True)
        assert result.has_audio

    def test_video_only_selection(self, sample_formats):
        result = get_best_format(sample_formats, quality="best", prefer_video=True, prefer_audio=False)
        assert result.has_video
        assert not result.has_audio
