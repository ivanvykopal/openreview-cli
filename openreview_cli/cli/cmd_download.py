"""``openreview_cli download`` subcommand.

Thin presentation layer: selects the papers to download (specific
``--submission`` ids, a whole ``--venue``, or by default the current
profile's own submissions), delegates to
:func:`openreview_cli.lib.download.bulk_download`, and prints per-paper
progress (``✓ title -> path`` / ``✗ title: error``) plus a summary line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from . import get_facade, handle_errors
from ..config import expand_path
from ..lib.download import bulk_download, find_venue_submissions
from ..lib.status import find_my_submissions
from ..models import Submission
from .formatting import emit

__all__ = ["command"]


@click.command("download")
@click.option(
    "--venue",
    default=None,
    help="Venue id whose submissions to download (default: your own submissions)",
)
@click.option(
    "-s",
    "--submission",
    "submission_ids",
    multiple=True,
    help="Download only this submission id (repeatable)",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Base download directory (default: config download_dir)",
)
@click.option(
    "--supplementary",
    is_flag=True,
    help="Also download supplementary material, when available",
)
@click.pass_context
@handle_errors
def download(
    ctx: click.Context,
    venue: str | None,
    submission_ids: tuple[str, ...],
    output_dir: str | None,
    supplementary: bool,
) -> None:
    """Download paper PDFs (and optional supplementary material)."""
    facade = get_facade(ctx)
    config = ctx.obj["config"]

    base = Path(output_dir) if output_dir else expand_path(config.download_dir)

    if submission_ids:
        submissions = [
            Submission(
                id=str(sid),
                forum=str(sid),
                title=str(sid),
                venue=None,
                venueid=venue or "unknown",
                authors=[],
                abstract=None,
                status="SUBMITTED",
                status_detail="",
            )
            for sid in submission_ids
        ]
    elif venue:
        submissions = find_venue_submissions(facade, venue)
    else:
        submissions = find_my_submissions(facade, venue=venue)

    paths, errors = bulk_download(
        facade, submissions, base, supplementary=supplementary
    )

    def human() -> None:
        by_id = {s.id: s for s in submissions}
        for paper_path in paths:
            paper_id = paper_path.parent.name
            submission = by_id.get(paper_id)
            title = submission.title if submission else paper_id
            click.secho(f"✓ {title} -> {paper_path}", fg="green")
        for error in errors:
            click.secho(
                f"✗ {error['title']}: {error['error']}", fg="red", err=True
            )
        click.echo(
            f"Downloaded {len(paths)} of {len(submissions)} paper(s) to {base}"
        )

    emit(ctx, {"downloaded": paths, "errors": errors}, human)


command = download
