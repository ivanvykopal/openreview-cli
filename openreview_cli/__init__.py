"""OpenReview CLI - a command-line tool for review status, deadlines,
downloads and more.

This package provides a Click-based CLI for reviewers and area chairs:
check your review status, browse reviews, download paper files, track
venue deadlines, and list your paper assignments — with human-readable
or JSON output.

Run ``openreview_cli --help`` after installing the dependencies.
"""

from .cli import cli  # noqa: F401

__version__ = "0.1.0"

__all__ = ["cli", "__version__"]
