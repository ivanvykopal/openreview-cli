"""``openreview_cli deadlines`` subcommand.

Thin presentation layer: fetches a venue's stage deadlines via
:func:`openreview_cli.lib.deadlines.fetch_deadlines`, classifies each as
open / closed / upcoming with ``deadline_state``, colors it with
``deadline_color``, and prints a Name / Window / State table (or JSON).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import click

from . import get_facade, handle_errors
from ..exceptions import OpenReviewCLIError
from ..lib.deadlines import deadline_state, fetch_deadlines
from ..models import Deadline
from .formatting import deadline_color, emit, print_table, truncate

__all__ = ["command"]

_TABLE_HEADERS = ("Name", "Window", "State")


@click.command("deadlines")
@click.option(
    "--venue",
    default=None,
    help="Venue id (default: group-level --venue or config venues)",
)
@click.pass_context
@handle_errors
def deadlines(ctx: click.Context, venue: str | None) -> None:
    """Show a venue's stage deadlines (submission, review, ...)."""
    facade = get_facade(ctx)

    if not venue:
        venues = (ctx.obj or {}).get("venues") or []
        venue = venues[0] if venues else None

    if not venue:
        raise OpenReviewCLIError("no venue specified")

    records = fetch_deadlines(facade, venue)

    # Group by invitation name, then sort by window start (undated last).
    grouped = sorted(
        (name, sorted(items, key=lambda r: r.start or ""))
        for name, items in _group_by_name(records).items()
    )

    states = [
        deadline_state(record) for _, items in grouped for record in items
    ]
    flat_records = [record for _, items in grouped for record in items]
    rows = [
        {**dataclasses.asdict(record), "state": state}
        for record, state in zip(flat_records, states)
    ]

    def human() -> None:
        print_table(
            _TABLE_HEADERS,
            [
                (
                    truncate(record.name, 30),
                    _window(record),
                    state,
                )
                for record, state in zip(flat_records, states)
            ],
        )

        # The plain table cannot color cells, so follow it with a colored
        # per-stage state legend using deadline_color.
        for record, state in zip(flat_records, states):
            click.secho(f"{record.name}: {state}", fg=deadline_color(state))

    emit(ctx, rows, human)


def _group_by_name(records: list[Deadline]) -> dict[str, list[Deadline]]:
    """Group deadline records by their (friendly) name."""
    grouped: dict[str, list[Deadline]] = {}

    for record in records:
        grouped.setdefault(record.name, []).append(record)

    return grouped


def _window(record: Deadline) -> str:
    """Render a deadline's window as ``start → end`` with ``-`` gaps."""
    return f"{record.start or '-'} → {record.end or '-'}"


command = deadlines
