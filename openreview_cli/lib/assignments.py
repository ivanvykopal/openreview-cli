"""Reviewer assignment discovery and bundle downloads.

Library layer for the "assignments" command: finds the papers assigned
to the current user via ``<venue>/-/Assignment`` edges, builds
:class:`~openreview_cli.models.Assignment` records, and can download
each assigned paper as a self-contained bundle (``paper.pdf`` +
``metadata.json`` + optional ``supplementary/``). Nothing here prints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..client import OpenReviewFacade
from ..config import Config
from ..exceptions import APIError
from ..helpers import content_value, note_forum
from ..models import Assignment
from .download import (
    PAPER_FILENAME,
    _pdf_bytes,
    download_attachment,
    venue_slug,
)

#: Suffix of the reviewer-assignment edge invitation.
ASSIGNMENT_INVITATION_SUFFIX = "/-/Assignment"

#: Filename of the per-paper metadata JSON inside a bundle.
METADATA_FILENAME = "metadata.json"


def _edge_id(edge: Any) -> Optional[str]:
    """Read an edge's ``head`` (the submission note id)."""
    value = getattr(edge, "head", None)
    return str(value) if value else None


def _assignment_from_note(note: Any) -> Assignment:
    """Build an :class:`Assignment` from a submission note."""
    checklist = content_value(note, "checklist")

    if not isinstance(checklist, dict):
        checklist = None

    authors = content_value(note, "authors") or []

    if not isinstance(authors, list):
        authors = [str(authors)]

    return Assignment(
        submission_id=str(getattr(note, "id", "") or ""),
        forum=str(note_forum(note) or ""),
        title=str(content_value(note, "title") or ""),
        abstract=content_value(note, "abstract"),
        authors=[str(a) for a in authors],
        checklist=checklist,
        pdf_url=content_value(note, "pdf"),
        supplementary_url=content_value(note, "supplementary_material"),
    )


def _is_not_found_error(exc: Exception) -> bool:
    """Whether an exception looks like OpenReview's "not found" error.

    OpenReview's ``OpenReviewException`` carries a dict payload with a
    ``name`` of ``"NotFoundError"`` (or the message contains "not
    found"). This means the venue simply has no Assignment invitation —
    a legitimate empty result, not a failure.
    """
    name = ""

    args = getattr(exc, "args", None)
    if args and isinstance(args[0], dict):
        name = str(args[0].get("name", "")).lower()
    if not name:
        name = type(exc).__name__.lower()

    message = str(exc).lower()

    return "notfound" in name or "not found" in message


def _edges_for_venue(
    facade: OpenReviewFacade, venue: str, profile_id: str
) -> tuple[list[Any], Optional[dict]]:
    """Fetch the reviewer-assignment edges for one venue.

    Returns ``(edges, error)``. A "not found"-signature failure (the
    venue has no Assignment invitation at all) is a legitimate empty
    result and yields ``([], None)``; any other failure is returned as
    an ``error`` dict so the CLI can surface it instead of silently
    reporting "0 assignments".
    """
    try:
        edges = facade.get_edges(
            invitation=venue.rstrip("/") + ASSIGNMENT_INVITATION_SUFFIX,
            tail=profile_id,
        )
    except Exception as exc:  # noqa: BLE001 - classify below
        if _is_not_found_error(exc):
            return [], None
        return [], {"venue": venue, "error": str(exc)}

    if edges is None:
        return [], None

    if isinstance(edges, dict):
        edges = edges.get("edges") or edges.get("results") or []

    return list(edges), None


def get_assigned_submissions(
    facade: OpenReviewFacade,
    venue: str | None = None,
    config: Optional[Config] = None,
) -> tuple[list[Assignment], list[dict[str, str]]]:
    """List the papers assigned to the current user for reviewing.

    Reviewer assignments are read from ``<venue>/-/Assignment`` edges
    whose ``tail`` is the user's profile id; each edge's ``head`` is the
    submission note id, which is then fetched to build the record. Notes
    that cannot be fetched are skipped silently.

    Args:
        facade: Rate-limited OpenReview facade.
        venue: Venue group id. When ``None``, every venue in
            ``config.venues`` is iterated.
        config: Configuration supplying the venue list; required when
            ``venue`` is ``None``.

    Returns:
        An ``(assignments, errors)`` tuple: de-duplicated (by
        submission id) :class:`Assignment` records, and one
        ``{"venue", "error"}`` dict per venue whose assignment edges
        could not be fetched (venues with no Assignment invitation are
        legitimately empty and produce no error).

    Raises:
        APIError: If the profile id cannot be determined or no venue is
            available to query.
    """
    profile_id = facade.profile_id()

    if venue is not None:
        venues = [venue]
    else:
        venues = list((config.venues if config is not None else None) or [])

    if not venues:
        raise APIError("No venue given and no venues configured for assignment lookup")

    heads: list[str] = []
    errors: list[dict[str, str]] = []

    for candidate in venues:
        edges, error = _edges_for_venue(facade, candidate, profile_id)

        if error is not None:
            errors.append(error)

        for edge in edges:
            head = _edge_id(edge)

            if head and head not in heads:
                heads.append(head)

    assignments: list[Assignment] = []

    for head in heads:
        try:
            note = facade.get_note(head)
        except Exception:  # noqa: BLE001 - unreadable note, skip it
            continue

        if note is None:
            continue

        assignments.append(_assignment_from_note(note))

    return assignments, errors


def _venue_ids(
    facade: OpenReviewFacade,
    assignments: list[Assignment],
    venue: str | None,
    config: Optional[Config],
) -> dict[str, str]:
    """Map submission id -> venueid for the given assignments.

    The venueid for each paper is discovered by fetching its note and
    reading the ``venueid`` content field (OpenReview notes expose the
    authoritative venue id there). Failures leave the paper on the
    fallback venue id ``"unknown"``. When an explicit ``venue`` is given
    it is used for every paper without any extra API calls.
    """
    venue_ids: dict[str, str] = {}

    if venue is not None:
        return {a.submission_id: venue for a in assignments}

    for assignment in assignments:
        venue_id: Optional[str] = None

        try:
            note = facade.get_note(assignment.submission_id)
            venue_id = content_value(note, "venueid")
        except Exception:  # noqa: BLE001 - fall back to "unknown"
            venue_id = None

        venue_ids[assignment.submission_id] = str(venue_id) if venue_id else "unknown"

    return venue_ids


def _metadata(assignment: Assignment, venueid: str) -> dict:
    """Build the bundle's ``metadata.json`` payload."""
    return {
        "submission_id": assignment.submission_id,
        "forum": assignment.forum,
        "venue": venueid,
        "title": assignment.title,
        "abstract": assignment.abstract,
        "authors": assignment.authors,
        "checklist": assignment.checklist,
        "pdf_url": assignment.pdf_url,
        "supplementary_url": assignment.supplementary_url,
    }


def download_assignment_bundles(
    facade: OpenReviewFacade,
    assignments: list[Assignment],
    output_dir: Path,
    supplementary: bool = False,
    venue: str | None = None,
    config: Optional[Config] = None,
) -> tuple[list[Path], list[dict]]:
    """Download one bundle per assignment into ``output_dir``.

    Each bundle lives at ``output_dir/<venue_slug(venueid)>/<paper_id>/``
    and contains ``paper.pdf`` and ``metadata.json`` (the full
    assignment data); a ``supplementary/`` sub-directory is added when
    ``supplementary`` is requested and the paper has a supplementary
    URL. Per-paper failures are collected, not raised.

    Venue ids are resolved per paper: the explicit ``venue`` argument
    when given, otherwise the note's ``venueid`` content field (via one
    extra :meth:`~OpenReviewFacade.get_note` call per paper), falling
    back to ``"unknown"``.

    Args:
        facade: Rate-limited OpenReview facade.
        assignments: Assignments to download.
        output_dir: Base download directory.
        supplementary: Also download supplementary material.
        venue: Optional venue id to use for all papers (skips the
            per-paper venueid lookup).
        config: Configuration, used only for context.

    Returns:
        A ``(paper_dirs, errors)`` tuple: the created bundle
        directories (one per successfully downloaded paper) and one
        error dict per failed paper with keys ``"submission_id"``,
        ``"title"``, and ``"error"``.
    """
    venue_ids = _venue_ids(facade, assignments, venue, config)

    paper_dirs: list[Path] = []
    errors: list[dict] = []

    for assignment in assignments:
        venue_id = venue_ids.get(assignment.submission_id, "unknown")
        paper_dir = Path(output_dir) / venue_slug(venue_id) / assignment.submission_id

        try:
            paper_dir.mkdir(parents=True, exist_ok=True)
            (paper_dir / PAPER_FILENAME).write_bytes(
                _pdf_bytes(facade, assignment.submission_id)
            )
        except Exception as exc:  # noqa: BLE001 - per-paper, keep going
            errors.append(
                {
                    "submission_id": assignment.submission_id,
                    "title": assignment.title,
                    "error": str(exc),
                }
            )
            continue

        try:
            with open(paper_dir / METADATA_FILENAME, "w", encoding="utf-8") as fh:
                json.dump(
                    _metadata(assignment, venue_id), fh, indent=2, ensure_ascii=False
                )
        except OSError as exc:
            errors.append(
                {
                    "submission_id": assignment.submission_id,
                    "title": assignment.title,
                    "error": f"Could not write metadata.json: {exc}",
                }
            )
            continue

        paper_dirs.append(paper_dir)

        if supplementary and assignment.supplementary_url:
            download_attachment(
                facade,
                assignment.submission_id,
                "supplementary_material",
                assignment.supplementary_url,
                paper_dir,
            )

    return paper_dirs, errors
