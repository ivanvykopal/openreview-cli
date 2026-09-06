"""PDF and supplementary-material downloads.

Library layer for fetching paper PDFs (and optional supplementary
material) to a structured on-disk layout::

    <output_dir>/<venue_slug>/<paper_id>/paper.pdf
    <output_dir>/<venue_slug>/<paper_id>/supplementary/<filename>

Nothing here prints; per-paper failures in :func:`bulk_download` are
collected and returned alongside the successful paths.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ..client import OpenReviewFacade
from ..exceptions import APIError, DownloadError
from ..helpers import content_value, is_dead_submission
from ..models import Submission

#: Filename of the downloaded paper PDF inside a paper directory.
PAPER_FILENAME = "paper.pdf"

#: Content field names that may hold supplementary material, in priority
#: order.
SUPPLEMENTARY_FIELDS = (
    "supplementary_material",
    "supplementary",
    "pdf_supplementary",
)

#: Characters that are unsafe in a single filesystem path component.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def venue_slug(venueid: str) -> str:
    """Convert a venue id into a filesystem-safe directory name.

    Args:
        venueid: A venue id such as ``"aclweb.org/ACL/ARR/2026/August"``.

    Returns:
        A string usable as a single path component, with ``/`` and other
        unsafe characters (``\\``, ``:``, ``*``, ``?``, ``"``, ``<``,
        ``>``, ``|``, control characters) replaced by ``_`` and
        surrounding whitespace stripped.
    """
    slug = _UNSAFE_CHARS.sub("_", str(venueid))
    slug = slug.strip().strip(".")

    # A slug with no alphanumeric characters (e.g. "///" -> "___") is
    # useless as a directory name.
    if not any(char.isalnum() for char in slug):
        return "unknown"

    return slug


def _pdf_bytes(facade: OpenReviewFacade, submission_id: str) -> bytes:
    """Fetch a submission's PDF bytes via the facade.

    Raises:
        DownloadError: If the API call fails or returns unusable data
            (``None``, non-bytes, or empty).
    """
    try:
        payload = facade.get_pdf(submission_id)
    except Exception as exc:  # noqa: BLE001 - SDK raises varied types
        raise DownloadError(f"PDF download failed for {submission_id}: {exc}") from exc

    if payload is None or not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
        raise DownloadError(f"No usable PDF returned for {submission_id}")

    return bytes(payload)


def download_pdf(
    facade: OpenReviewFacade,
    submission_id: str,
    output_dir: Path,
    venueid: str = "unknown",
) -> Path:
    """Download a submission's PDF to the standard layout.

    Writes ``output_dir/<venue_slug(venueid)>/<submission_id>/paper.pdf``,
    creating parent directories as needed.

    Args:
        facade: Rate-limited OpenReview facade.
        submission_id: The submission (note) id.
        output_dir: Base download directory.
        venueid: Venue id used to derive the sub-directory name.

    Returns:
        The path of the written ``paper.pdf``.

    Raises:
        DownloadError: If the PDF cannot be fetched or written.
    """
    paper_dir = Path(output_dir) / venue_slug(venueid) / str(submission_id)
    paper_dir.mkdir(parents=True, exist_ok=True)

    target = paper_dir / PAPER_FILENAME

    try:
        target.write_bytes(_pdf_bytes(facade, submission_id))
    except DownloadError:
        raise
    except OSError as exc:
        raise DownloadError(f"Could not write PDF for {submission_id}: {exc}") from exc

    return target


def _venue_submission_from_note(note: Any) -> Submission:
    """Build a :class:`Submission` from a venue-submission note.

    ``status``/``status_detail`` are left at their initial values — the
    download commands do not need forum analysis.
    """
    authors = content_value(note, "authors", [])
    if not isinstance(authors, list):
        authors = []

    return Submission(
        id=str(getattr(note, "id", "") or ""),
        forum=str(getattr(note, "forum", None) or getattr(note, "id", "") or ""),
        title=str(content_value(note, "title", "(untitled)")),
        venue=content_value(note, "venue", None),
        venueid=str(content_value(note, "venueid", "") or ""),
        authors=[str(a) for a in authors],
        abstract=content_value(note, "abstract", None),
        status="SUBMITTED",
        status_detail="",
    )


def find_venue_submissions(
    facade: OpenReviewFacade,
    venue: str,
) -> list[Submission]:
    """List the live submissions of a venue.

    Queries the venue's submissions via ``get_all_notes`` with the
    ``venueid`` content filter and skips dead submissions (withdrawn,
    desk-rejected, arXiv-only, and the like).

    Args:
        facade: Rate-limited OpenReview facade.
        venue: Venue id, e.g. ``"aclweb.org/ACL/ARR/2026/August"``.

    Returns:
        Deduplicated :class:`~openreview_cli.models.Submission` records
        (ids of notes that have one), in API order.

    Raises:
        APIError: If the venue's notes cannot be fetched.
    """
    try:
        notes = facade.get_all_notes(content={"venueid": venue})
    except Exception as exc:  # noqa: BLE001 - SDK raises varied types
        raise APIError(f"Could not list submissions for venue {venue}: {exc}") from exc

    unique: dict[str, Submission] = {}

    for note in notes or []:
        submission = _venue_submission_from_note(note)

        if is_dead_submission(str(submission.venue or ""), submission.venueid):
            continue

        if submission.id:
            unique[submission.id] = submission

    return list(unique.values())


def _supplementary_field(note: Any) -> Optional[tuple[str, Any]]:
    """Return ``(field_name, value)`` for a note's first supplementary field.

    Reads the note's content via
    :func:`~openreview_cli.helpers.content_value` over the candidate
    fields (:data:`SUPPLEMENTARY_FIELDS`). The
    :class:`~openreview_cli.models.Submission` model deliberately does
    not carry raw note content, so this only works when the caller has
    the original note.
    """
    for field in SUPPLEMENTARY_FIELDS:
        value = content_value(note, field, None)

        if value not in (None, ""):
            return field, value

    return None


def _attachment_filename(field_name: str, value: Any) -> str:
    """Derive a filename for a supplementary value.

    The value may be a relative path string like ``"/attachment/f.pdf"``
    or a dict with a ``"value"`` key; anything else falls back to the
    field name itself.
    """
    if isinstance(value, dict):
        value = value.get("value")

    if isinstance(value, str) and value.strip():
        candidate = value.strip().rstrip("/").split("/")[-1]

        if candidate:
            return candidate

    return field_name


def download_supplementary(
    facade: OpenReviewFacade,
    note: Any,
    submission_id: str,
    paper_dir: Path,
) -> Optional[Path]:
    """Download a submission's supplementary material, if any.

    Takes the raw submission *note* (duck-typed): the
    :class:`~openreview_cli.models.Submission` model does not carry raw
    note content, so the candidate supplementary fields
    (:data:`SUPPLEMENTARY_FIELDS`) can only be read from the note
    itself.

    Note: :func:`bulk_download` only has :class:`Submission` objects
    (no notes) and therefore cannot detect supplementary material —
    supplementary downloads currently happen only via the assignments
    path (:func:`openreview_cli.lib.assignments.download_assignment_bundles`),
    whose records carry a ``supplementary_url``. TODO: thread the raw
    notes through ``bulk_download`` if venue downloads ever need
    supplementary material.

    Args:
        facade: Rate-limited OpenReview facade.
        note: The submission note to inspect for supplementary fields.
        submission_id: The note's id, passed to
            ``facade.get_attachment``.
        paper_dir: The paper's directory (``.../<paper_id>``); files are
            written into ``paper_dir/supplementary/``.

    Returns:
        The path of the downloaded file, or ``None`` when there is no
        supplementary field, the download fails, or the payload is
        unusable — supplementary material is optional, so failures are
        swallowed rather than raised.
    """
    found = _supplementary_field(note)

    if found is None:
        return None

    field_name, value = found

    return download_attachment(facade, submission_id, field_name, value, paper_dir)


def download_attachment(
    facade: OpenReviewFacade,
    submission_id: str,
    field_name: str,
    value: Any,
    paper_dir: Path,
) -> Optional[Path]:
    """Download one supplementary attachment into ``paper_dir/supplementary/``.

    Shared by the submission downloader and the assignment-bundle
    downloader (assignments already carry the supplementary URL, so
    they call this directly instead of re-deriving the field).

    Args:
        facade: Rate-limited OpenReview facade.
        submission_id: The submission (note) id, for
            ``facade.get_attachment(submission_id, field_name)``.
        field_name: The content field holding the attachment.
        value: The field value (a relative path string like
            ``"/attachment/..."``, a ``{"value": ...}`` dict, or
            anything else — used only to derive the filename).
        paper_dir: The paper's directory.

    Returns:
        The path of the downloaded file, or ``None`` on any failure —
        supplementary material is optional, so failures are swallowed
        rather than raised.
    """
    filename = _attachment_filename(field_name, value)

    try:
        # The SDK signature is get_attachment(field_name, id=None, ...);
        # pass keywords so the two values cannot be transposed.
        payload = facade.get_attachment(field_name=field_name, id=submission_id)
    except Exception:  # noqa: BLE001 - optional material, never fatal
        return None

    if payload is None or not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
        return None

    supp_dir = Path(paper_dir) / "supplementary"
    supp_dir.mkdir(parents=True, exist_ok=True)

    target = supp_dir / filename

    try:
        target.write_bytes(bytes(payload))
    except OSError:
        return None

    return target


def bulk_download(
    facade: OpenReviewFacade,
    submissions: list[Submission],
    output_dir: Path,
    supplementary: bool = False,
) -> tuple[list[Path], list[dict]]:
    """Download PDFs (and optionally supplementary material) for papers.

    Each paper is handled independently: a failure on one paper does not
    stop the others.

    Args:
        facade: Rate-limited OpenReview facade.
        submissions: The submissions to download.
        output_dir: Base download directory.
        supplementary: Also try to download supplementary material.
            NOTE: currently a no-op here — supplementary fields are only
            readable from raw notes, which this function does not have
            (see the TODO in the source).

    Returns:
        A ``(paths, errors)`` tuple. ``paths`` lists the written
        ``paper.pdf`` locations; ``errors`` holds one dict per failed
        paper with keys ``"submission_id"``, ``"title"``, and ``"error"``
        (the failure message).
    """
    paths: list[Path] = []
    errors: list[dict] = []

    for submission in submissions:
        try:
            paper_path = download_pdf(
                facade,
                submission.id,
                output_dir,
                venueid=submission.venueid or "unknown",
            )
        except DownloadError as exc:
            errors.append(
                {
                    "submission_id": submission.id,
                    "title": submission.title,
                    "error": str(exc),
                }
            )
            continue

        paths.append(paper_path)

        # Supplementary material cannot be detected here: the
        # Submission model does not carry raw note content, so the
        # candidate supplementary fields are unreadable. The
        # assignments path (whose records carry a supplementary_url)
        # handles supplementary downloads; see
        # download_supplementary's docstring. TODO: thread the raw
        # notes through if venue downloads ever need this.
        _ = supplementary

    return paths, errors
