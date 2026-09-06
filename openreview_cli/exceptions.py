"""Typed exceptions for the OpenReview CLI.

All exceptions raised by the library layer derive from
:class:`OpenReviewCLIError` so the CLI can catch a single base class
and format errors uniformly.
"""

from __future__ import annotations


class OpenReviewCLIError(Exception):
    """Base class for all OpenReview CLI errors."""


class AuthenticationError(OpenReviewCLIError):
    """Raised when login fails or credentials are missing/unusable."""


class VenueNotFoundError(OpenReviewCLIError):
    """Raised when a requested venue (group / invitation prefix) does not exist."""

    def __init__(self, venue: str) -> None:
        self.venue = venue
        super().__init__(f"Venue not found: {venue}")


class PermissionError(OpenReviewCLIError):
    """Raised when the API denies access to a resource."""


class DownloadError(OpenReviewCLIError):
    """Raised when a PDF or attachment download fails."""


class ConfigError(OpenReviewCLIError):
    """Raised when the configuration file is invalid."""


class APIError(OpenReviewCLIError):
    """Raised for unexpected OpenReview API failures not otherwise classified."""
