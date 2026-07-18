from __future__ import annotations


class YTDLException(Exception):
    """Base exception for all ytdownloader errors."""

    def __init__(self, message: str = "", cause: Exception | None = None) -> None:
        self.cause = cause
        super().__init__(message)

    def __str__(self) -> str:
        msg = super().__str__()
        if self.cause is not None:
            msg = f"{msg} (caused by: {self.cause})"
        return msg


class InvalidURLError(YTDLException):
    """Raised when the provided URL is not a valid or supported YouTube URL."""


class VideoUnavailableError(YTDLException):
    """Raised when the video is unavailable (removed, private, or deleted)."""


class AgeRestrictedError(YTDLException):
    """Raised when access to the video is restricted by age-gate verification."""


class GeoRestrictedError(YTDLException):
    """Raised when the video is not available in the current geographic region."""


class NetworkError(YTDLException):
    """Raised when a network-related error occurs during communication."""


class DownloadError(YTDLException):
    """Raised when a stream download fails or is interrupted."""


class FormatSelectionError(YTDLException):
    """Raised when no suitable stream format can be selected for download."""


class SignatureCipherError(YTDLException):
    """Raised when parsing or applying the signatureCipher parameter fails."""


class NResolverError(YTDLException):
    """Raised when the JavaScript n-parameter resolver encounters an error."""


class MetadataExtractionError(YTDLException):
    """Raised when extracting video metadata from the player response fails."""


class StreamResolutionError(YTDLException):
    """Raised when resolving stream format details encounters an error."""


class SubtitleError(YTDLException):
    """Raised when downloading or converting subtitle/caption tracks fails."""


class MergeError(YTDLException):
    """Raised when merging separate audio and video streams fails."""


class CacheError(YTDLException):
    """Raised when a cache operation (read/write/clear) fails."""


class ConfigError(YTDLException):
    """Raised when configuration loading, validation, or application fails."""


class HtmlExtractionError(YTDLException):
    """Raised when extracting data from YouTube HTML fails."""
