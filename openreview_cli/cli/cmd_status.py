"""``openreview_cli status`` subcommand.

Thin presentation layer: finds the current profile's submissions via
:mod:`openreview_cli.lib.status`, analyzes their forums, records them
in the SQLite change-tracking database, and prints a status table with
"NEW" activity lines for previously unseen notes. In ``--watch`` mode
the check repeats every ``--interval`` seconds until interrupted.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import click

from . import get_facade, handle_errors
from ..lib.status import StatusDB, run_status_check
from .formatting import emit, print_table, truncate

__all__ = ["command"]

_TABLE_HEADERS = ("Title", "Venue", "Status", "Detail")


def _venue_filter(ctx: click.Context, venue: str | None) -> str | None:
    """Resolve the effective venue filter for this invocation.

    The command-level ``--venue`` option takes precedence; otherwise the
    shared ``--venue`` group option(s) apply; with neither, all venues
    are considered (``None``).
    """
    if venue:
        return venue
    venues = (ctx.obj or {}).get("venues") or []
    return venues[0] if venues else None


def _run_once(ctx: click.Context, venue: str | None, include_all: bool) -> None:
    """Run a single status check and emit the output.

    Args:
        ctx: The command's click context.
        venue: Optional venue id prefix filter.
        include_all: When ``True``, include old-cycle submissions.
    """
    facade = get_facade(ctx)
    config = ctx.obj["config"]

    with StatusDB(str(Path(config.db_path).expanduser())) as db:
        submissions, errors, new_events = run_status_check(
            facade, db, include_all=include_all, venue=venue
        )

    rows = [
        {**dataclasses.asdict(s), "new_events": new_events.get(s.id, [])}
        for s in submissions
    ]

    def human() -> None:
        print_table(
            _TABLE_HEADERS,
            [
                (
                    truncate(submission.title, 50),
                    submission.venue or "",
                    submission.status,
                    submission.status_detail,
                )
                for submission in submissions
            ],
        )
        for entry in rows:
            for event in entry["new_events"]:
                click.secho(
                    f"NEW {event['type']}: {event['paper']} ({event['note_id']})",
                    fg="green",
                )
        for error in errors:
            click.secho(
                f"Error ({error['paper'] or 'general'}): {error['error']}",
                fg="red",
                err=True,
            )

    emit(ctx, {"submissions": rows, "errors": errors}, human)


@click.command("status")
@click.option("--venue", default=None, help="Venue id prefix filter (default: all)")
@click.option(
    "--all",
    "include_all",
    is_flag=True,
    help="Include submissions from older ARR cycles / years",
)
@click.option(
    "--watch",
    is_flag=True,
    help="Poll continuously until interrupted (ignored with --json)",
)
@click.option(
    "--interval",
    type=click.IntRange(min=1),
    default=900,
    show_default=True,
    help="Seconds between polls in --watch mode",
)
@click.pass_context
@handle_errors
def status(
    ctx: click.Context,
    venue: str | None,
    include_all: bool,
    watch: bool,
    interval: int,
) -> None:
    """Show review status of your paper submissions."""
    venue_filter = _venue_filter(ctx, venue)
    as_json = bool((ctx.obj or {}).get("as_json"))

    if watch and as_json:
        click.echo(
            "Note: --watch is ignored in JSON mode; running a single check.",
            err=True,
        )

    _run_once(ctx, venue_filter, include_all)

    if watch and not as_json:
        click.echo(f"Watching for changes (every {interval}s, Ctrl+C to exit).")
        try:
            while True:
                time.sleep(interval)
                click.echo("")
                _run_once(ctx, venue_filter, include_all)
        except KeyboardInterrupt:
            click.echo("Stopped watching.")


command = status
