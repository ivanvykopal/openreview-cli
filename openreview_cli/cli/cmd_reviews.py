"""``openreview_cli reviews`` subcommand.

Thin presentation layer: fetches reviews, meta-reviews, and decisions
for the current profile's submissions via
:mod:`openreview_cli.lib.reviews`, prints them per paper, and
optionally saves per-paper JSON files via ``--output``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import click

from . import get_facade, handle_errors
from ..lib.reviews import (
    fetch_my_reviews,
    fetch_reviews,
    save_reviews_json,
)
from ..lib.status import find_my_submissions
from .formatting import emit, truncate

__all__ = ["command"]


def _print_review(review: Any) -> None:
    """Print one review with its rating, confidence, and full content."""
    click.secho(f"  Review {review.id}", fg="yellow")
    click.echo(f"    Rating:     {review.rating or '-'}")
    click.echo(f"    Confidence: {review.confidence or '-'}")
    click.echo(f"    Created:    {review.created or '-'}")
    for key, value in (review.content or {}).items():
        click.echo(f"    {key}: {_one_line(value)}")


def _one_line(value: Any) -> str:
    """Render a content value on a single line, truncating long text."""
    if isinstance(value, str):
        return truncate(" ".join(value.split()), 100)
    return truncate(" ".join(str(value).split()), 100)


def _print_payload(submission: Any, entry: dict[str, Any]) -> None:
    """Print one paper's reviews, meta-reviews, and decisions."""
    click.secho(
        f"{truncate(submission.title, 70)} ({submission.id})",
        fg="cyan",
        bold=True,
    )
    click.echo(
        f"  Venue: {submission.venue or submission.venueid or '-'}"
        f"  Status: {submission.status}"
    )

    for review in entry["reviews"]:
        _print_review(review)

    for meta in entry["meta_reviews"]:
        click.secho(f"  Meta-review {meta.id}", fg="cyan")
        click.echo(f"    Recommendation: {meta.recommendation or '-'}")
        click.echo(f"    Created:        {meta.created or '-'}")
        for key, value in (meta.content or {}).items():
            click.echo(f"    {key}: {_one_line(value)}")

    for decision in entry["decisions"]:
        click.secho(f"  Decision {decision.id}", fg="green")
        click.echo(f"    {decision.decision} ({decision.created or '-'})")

    role_labels = {"authors": "Rebuttal", "reviewers": "Reviewer reply"}
    for comment in entry["comments"]:
        label = role_labels.get(comment.role, "Comment")
        click.secho(f"  {label} {comment.id}", fg="magenta")
        if comment.title:
            click.echo(f"    Title: {comment.title}")
        click.echo(f"    Created: {comment.created or '-'}")
        if comment.comment:
            for line in comment.comment.splitlines():
                click.echo(f"    {truncate(line, 100)}")

    if not (
        entry["reviews"]
        or entry["meta_reviews"]
        or entry["decisions"]
        or entry["comments"]
    ):
        click.echo("  No reviews, meta-reviews, or decisions visible yet.")


@click.command("reviews")
@click.option(
    "--venue",
    default=None,
    help="Venue id prefix filter (default: group-level --venue or config)",
)
@click.option(
    "-s",
    "--submission",
    "submission_ids",
    multiple=True,
    help="Limit to this submission id (repeatable)",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Also save per-paper JSON files into this directory",
)
@click.option(
    "--mine",
    "mine",
    is_flag=True,
    help="Fetch the reviews YOU wrote (on other people's papers)",
)
@click.option(
    "--all",
    "include_old",
    is_flag=True,
    help="Include submissions from older ARR cycles / years "
    "(default: newest cycle plus one back)",
)
@click.option(
    "--cycles-back",
    type=int,
    default=None,
    help="Number of extra (older) ARR cycles to include (default: 1)",
)
@click.pass_context
@handle_errors
def reviews(
    ctx: click.Context,
    venue: str | None,
    submission_ids: tuple[str, ...],
    output_dir: str | None,
    mine: bool,
    include_old: bool,
    cycles_back: int | None,
) -> None:
    """Fetch reviews, meta-reviews, and decisions (yours or your papers')."""
    facade = get_facade(ctx)

    if not venue:
        venues = (ctx.obj or {}).get("venues") or []
        venue = venues[0] if venues else None

    if mine:
        submissions, results, errors = fetch_my_reviews(
            facade, venue=venue
        )
    else:
        submissions = find_my_submissions(
            facade,
            include_old=include_old,
            cycles_back=cycles_back if cycles_back is not None else 1,
            venue=venue,
        )
        results, errors = fetch_reviews(facade, submissions)

    if submission_ids:
        wanted = set(submission_ids)
        submissions = [s for s in submissions if s.id in wanted]
        results = {
            sid: entry for sid, entry in results.items() if sid in wanted
        }

    if output_dir:
        errors = list(errors) + save_reviews_json(
            results, Path(output_dir).expanduser()
        )

    def human() -> None:
        for submission in submissions:
            entry = results.get(submission.id)
            if entry is None:
                continue
            _print_payload(submission, entry)
            click.echo("")
        for error in errors:
            click.secho(
                f"Error ({error['paper'] or 'general'}): {error['error']}",
                fg="red",
                err=True,
            )

    emit(ctx, {"results": _serialize(results), "errors": errors}, human)


def _serialize(
    results: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """JSON-serialize a fetch_reviews results dict.

    Dataclass values are replaced by :func:`dataclasses.asdict` outputs
    (``json.dumps(default=str)`` in :func:`print_json` handles the rest).
    """
    return {
        submission_id: {
            key: (
                dataclasses.asdict(value)
                if dataclasses.is_dataclass(value)
                else [
                    dataclasses.asdict(item)
                    if dataclasses.is_dataclass(item)
                    else item
                    for item in value
                ]
                if isinstance(value, list)
                else value
            )
            for key, value in entry.items()
        }
        for submission_id, entry in results.items()
    }


command = reviews
