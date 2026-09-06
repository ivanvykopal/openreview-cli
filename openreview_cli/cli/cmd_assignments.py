"""``openreview_cli assignments`` subcommand.

Thin presentation layer: lists the papers assigned to the current
profile for reviewing via
:func:`openreview_cli.lib.assignments.get_assigned_submissions`, prints a
Title / Abstract / Venue table with a count summary, and can download
each assignment as a bundle via ``download_assignment_bundles``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import click

from . import get_facade, handle_errors
from ..config import expand_path
from ..lib.assignments import (
    download_assignment_bundles,
    get_assigned_submissions,
)
from .formatting import emit, print_table, truncate

__all__ = ["command"]

_TABLE_HEADERS = ("Title", "Abstract", "Venue")

_ABSTRACT_WIDTH = 60


@click.command("assignments")
@click.option(
    "--venue",
    default=None,
    help="Venue id (default: group-level --venue or config venues)",
)
@click.option(
    "--download",
    is_flag=True,
    help="Download each assigned paper as a bundle (paper.pdf + metadata.json)",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Base download directory for --download (default: config download_dir)",
)
@click.option(
    "--supplementary",
    is_flag=True,
    help="Also download supplementary material with --download",
)
@click.pass_context
@handle_errors
def assignments(
    ctx: click.Context,
    venue: str | None,
    download: bool,
    output_dir: str | None,
    supplementary: bool,
) -> None:
    """List (and optionally download) your reviewer assignments."""
    facade = get_facade(ctx)
    config = ctx.obj["config"]

    if not venue:
        venues = (ctx.obj or {}).get("venues") or []
        venue = venues[0] if venues else None

    assigned, lookup_errors = get_assigned_submissions(
        facade, venue=venue, config=config
    )

    payload: dict[str, Any] = {
        "assignments": [dataclasses.asdict(a) for a in assigned]
    }
    paper_dirs: list[Path] = []
    errors: list[dict] = []

    if lookup_errors:
        payload["lookup_errors"] = lookup_errors

    if download:
        base = Path(output_dir) if output_dir else expand_path(config.download_dir)
        paper_dirs, errors = download_assignment_bundles(
            facade,
            assigned,
            base,
            supplementary=supplementary,
            venue=venue,
            config=config,
        )
        payload["downloaded"] = paper_dirs
        payload["errors"] = errors

    def human() -> None:
        print_table(
            _TABLE_HEADERS,
            [
                (
                    truncate(a.title, 50),
                    truncate(a.abstract or "", _ABSTRACT_WIDTH),
                    venue or "-",
                )
                for a in assigned
            ],
        )
        click.echo(f"{len(assigned)} assignment(s)")
        if download:
            click.echo(f"Downloaded {len(paper_dirs)} bundle(s)")
        for error in errors:
            click.secho(
                f"Error ({error['title'] or error['submission_id']}): "
                f"{error['error']}",
                fg="red",
                err=True,
            )
        for lookup_error in lookup_errors:
            click.secho(
                f"Error looking up assignments for {lookup_error['venue']}: "
                f"{lookup_error['error']}",
                fg="red",
                err=True,
            )

    emit(ctx, payload, human)


command = assignments
