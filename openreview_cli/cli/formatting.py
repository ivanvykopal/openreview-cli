"""Output formatting helpers for the OpenReview CLI.

Presentation-only utilities: JSON dumping, plain-text tables, unified
JSON/human output dispatch, status coloring, and string truncation.
No business logic and no external dependencies beyond click.
"""

from __future__ import annotations

import json
from typing import Callable, Sequence

import click

__all__ = [
    "print_json",
    "print_table",
    "emit",
    "status_color",
    "deadline_color",
    "truncate",
]


def print_json(payload: "dict | list") -> None:
    """Pretty-print a payload as JSON to stdout.

    Args:
        payload: A dict or list (already JSON-compatible or containing
            objects serializable via ``default=str``).
    """
    click.echo(json.dumps(payload, indent=2, default=str))


def print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """Print an aligned plain-text table to stdout.

    Column widths are computed from the widest cell in each column
    (header included). Rows may be shorter than ``headers``; missing
    cells render as empty strings.

    Args:
        headers: Column titles.
        rows: Table body, one sequence of strings per row.
    """
    def cell(value: object) -> str:
        return "" if value is None else str(value)

    width = len(headers)
    normalized = [[cell(c) for c in row[:width]] + [""] * (width - len(row)) for row in rows]

    columns = [list(col) for col in zip(*([list(headers)] + normalized))] if width else []
    widths = [max((len(c) for c in col), default=0) for col in columns]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    click.echo(line([cell(h) for h in headers]))
    if widths:
        click.echo("  ".join("-" * w for w in widths))
    for row in normalized:
        click.echo(line(row))


def emit(ctx: click.Context, payload: "dict | list", human: Callable[[], None]) -> None:
    """Output ``payload`` in the mode selected by the shared context.

    Args:
        ctx: The command's click context (``ctx.obj["as_json"]``
            selects JSON mode).
        payload: The JSON-serializable payload.
        human: Zero-argument callable that prints the human-readable
            representation; only invoked in human mode.
    """
    obj = ctx.obj or {}
    if obj.get("as_json"):
        print_json(payload)
    else:
        human()


def status_color(status: str) -> str:
    """Map a review/deadline status label to a click color.

    Args:
        status: One of ``DECISION``, ``META_REVIEW``, ``REVIEWS``,
            ``SUBMITTED`` (case-insensitive).

    Returns:
        A click color name; unknown statuses render uncolored (empty
        string).
    """
    return {
        "DECISION": "green",
        "META_REVIEW": "cyan",
        "REVIEWS": "yellow",
        "SUBMITTED": "white",
    }.get(status.upper(), "")


def deadline_color(state: str) -> str:
    """Map a deadline state to a click color.

    Args:
        state: One of ``open``, ``upcoming``, ``closed``
            (case-insensitive).

    Returns:
        A click color name; unknown states render uncolored (empty
        string).
    """
    return {
        "open": "green",
        "upcoming": "yellow",
        "closed": "red",
    }.get(state.lower(), "")


def truncate(s: str, width: int) -> str:
    """Shorten ``s`` to at most ``width`` characters.

    Longer strings are cut to ``width - 3`` characters plus an ellipsis
    ``"..."`` (total exactly ``width``) when ``width >= 4``; narrower
    widths use a hard cut.

    Args:
        s: The string to truncate.
        width: Maximum result length.

    Returns:
        The (possibly) truncated string.
    """
    if width < 0:
        return ""
    if len(s) <= width:
        return s
    if width >= 4:
        return s[: width - 3] + "..."
    return s[:width]
