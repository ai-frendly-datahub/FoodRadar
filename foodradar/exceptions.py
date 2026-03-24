from __future__ import annotations


class RadarError(Exception):
    """Base exception for all Radar errors."""


class ConfigError(RadarError):
    """Configuration loading or validation error."""


class CollectionError(RadarError):
    """Error during data collection from sources."""


class SourceError(CollectionError):
    """Error specific to a single source."""

    def __init__(self, source_name: str, message: str, original_error: Exception | None = None):
        self.source_name = source_name
        self.original_error = original_error
        super().__init__(f"[{source_name}] {message}")


class NetworkError(CollectionError):
    """Network-related error (timeout, connection failure)."""


class ParseError(CollectionError):
    """Error parsing source data (RSS, JSON, HTML)."""


class StorageError(RadarError):
    """Database storage or query error."""


class ReportError(RadarError):
    """Error generating reports."""


class SearchError(RadarError):
    """Search index error."""


class NotificationError(RadarError):
    """Error sending notifications."""
