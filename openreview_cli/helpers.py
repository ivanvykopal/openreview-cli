"""Pure helper functions over OpenReview note objects.

All functions are duck-typed: they accept anything that quacks like an
``openreview.Note`` (attribute access on ``id``, ``invitation``,
``content``, ``forum``, ``tmdate``/``mdate``/``cdate``). No
``openreview`` import is required at runtime — the library is only
referenced under ``TYPE_CHECKING`` for type aliases.

Ported from the original standalone monitor script; logic is identical
but reimplemented here with typing and docstrings.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .models import Submission

#: Group id of the top-level ACL Rolling Review group.
ARR_GROUP_ID = "aclweb.org/ACL/ARR"

#: Matches an ARR cycle group id, e.g. ``aclweb.org/ACL/ARR/2026/August``.
ARR_CYCLE_RE = re.compile(r"aclweb\.org/ACL/ARR/(\d{4})/([A-Za-z]+)")


def timestamp_to_iso(timestamp: int | None) -> str | None:
    """Convert an OpenReview ms-epoch timestamp to an ISO-8601 string.

    Args:
        timestamp: Milliseconds since the Unix epoch (or ``None``/0).

    Returns:
        UTC ISO-8601 string, or ``None`` when the timestamp is missing
        or cannot be converted.
    """
    if not timestamp:
        return None

    try:
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def content_value(note: Any, key: str, default: Any = None) -> Any:
    """Read a content field from a note, handling both API formats.

    OpenReview content fields are commonly represented either as a plain
    value (``{"title": "My paper"}``, API v1) or wrapped in a dict
    (``{"title": {"value": "My paper"}}``, API v2). Both are handled.

    Args:
        note: A note-like object with a ``content`` attribute (mapping).
        key: The content field name.
        default: Value returned when the field is absent.

    Returns:
        The unwrapped field value, or ``default``.
    """
    content = getattr(note, "content", {}) or {}

    value = content.get(key, default)

    if isinstance(value, dict):
        if "value" in value:
            return value["value"]

    return value


def note_id(note: Any) -> str | None:
    """Return the note's id, or ``None`` if it has none."""
    return getattr(note, "id", None)


def note_invitation(note: Any) -> str:
    """Return the note's invitation string (``""`` when missing)."""
    return str(getattr(note, "invitation", "") or "")


def note_forum(note: Any) -> str | None:
    """Return the note's forum id, falling back to its own id."""
    return getattr(note, "forum", None) or getattr(note, "id", None)


def note_time(note: Any) -> str | None:
    """Return the most useful note timestamp as an ISO-8601 string.

    Checks ``tmdate`` (true modification date), then ``mdate``, then
    ``cdate``; returns ``None`` when none are set or convertible.
    """
    for attr in ("tmdate", "mdate", "cdate"):
        value = getattr(note, attr, None)

        if value:
            result = timestamp_to_iso(value)

            if result:
                return result

    return None


def classify_note(note: Any) -> str:
    """Classify a note into one of the tool's note kinds.

    Uses the invitation string plus the content field names (case
    insensitive). Venue invitation naming varies, so matching is
    deliberately broad.

    Args:
        note: A note-like object with ``invitation`` and ``content``.

    Returns:
        One of ``"review"``, ``"meta_review"``, ``"decision"``,
        ``"comment"``, or ``"other"``.
    """
    # API v2 notes carry a plural ``invitations`` list; v1 a single
    # ``invitation`` string. Consider all of them when classifying.
    invitations = getattr(note, "invitations", None) or []
    invitation = " ".join(
        [note_invitation(note).lower()]
        + [str(inv).lower() for inv in invitations]
    )

    content = getattr(note, "content", {}) or {}

    keys = {str(key).lower() for key in content.keys()}

    # Issue reports (e.g. Meta-Review_Issue_Report, Review_Issue_Report)
    # are side-channel notes, not the reviews / meta-reviews themselves.
    if "issue_report" in invitation or "issue report" in invitation:
        return "other"

    # Decision
    if "decision" in invitation or "decision" in keys:
        return "decision"

    # Meta-review / area chair
    if (
        "meta-review" in invitation
        or "meta_review" in invitation
        or "metareview" in invitation
        or "meta review" in invitation
        or "area_chair" in invitation
        or "area chair" in invitation
        or "metareview" in keys
    ):
        return "meta_review"

    # Review
    if "review" in invitation:
        return "review"

    # Comment / rebuttal
    if (
        "comment" in invitation
        or "rebuttal" in invitation
        or "response" in invitation
    ):
        return "comment"

    return "other"


def is_probable_submission(note: Any) -> bool:
    """Heuristically decide whether a note is a submission.

    Excludes reviews, comments, meta-reviews, rebuttals, decisions, and
    area-chair notes based on their invitation string. Intentionally
    conservative because different venues use different invitation
    names.

    Args:
        note: A note-like object with an ``invitation`` attribute.

    Returns:
        ``True`` when the note looks like a paper submission.
    """
    invitation = note_invitation(note).lower()

    excluded = (
        "review",
        "meta-review",
        "metareview",
        "meta_review",
        "comment",
        "rebuttal",
        "response",
        "decision",
        "area_chair",
    )

    return not any(word in invitation for word in excluded)


def arr_cycle_id_of_submission(
    submission: "Submission | dict[str, Any]",
    note: Any = None,
) -> str | None:
    """Extract the ARR cycle group id from a submission.

    E.g. ``"aclweb.org/ACL/ARR/2026/August/Submission"`` ->
    ``"aclweb.org/ACL/ARR/2026/August"``. Released papers carry the
    cycle in their ``venueid``; papers still under review only expose
    it via their note invitation(s), so both are checked.

    Args:
        submission: A :class:`~openreview_cli.models.Submission`
            (``.venueid`` is used) or a plain dict
            (``.get("venueid")`` is used).
        note: Optional note object whose invitation / ``invitations``
            are searched when the venueid does not match.

    Returns:
        The ARR cycle group id, or ``None`` for non-ARR venues
        (committed *ACL papers, other conferences).
    """
    if isinstance(submission, dict):
        venue_id = str(submission.get("venueid") or "")
    else:
        venue_id = str(getattr(submission, "venueid", "") or "")

    match = ARR_CYCLE_RE.search(venue_id)

    if match:
        return venue_id[: match.end()]

    # Under review: try the note's invitation(s).
    invitations: list[str] = []

    if note is not None:
        invitation = note_invitation(note)

        if invitation:
            invitations.append(invitation)

        invitations.extend(getattr(note, "invitations", None) or [])

    for candidate in invitations:
        match = ARR_CYCLE_RE.search(candidate)

        if match:
            return candidate[: match.end()]

    return None


def extract_decision(note: Any) -> str | None:
    """Extract a human-readable decision string from a note.

    Checks the common content field names (``decision``,
    ``recommendation``, ``final_decision``), unwrapping the API v2
    ``{"value": ...}`` representation. Falls back to the invitation
    string, then to ``"Decision announced"``.

    Args:
        note: A decision note-like object, or ``None``.

    Returns:
        The decision text, or ``None`` when the note is falsy.
    """
    if not note:
        return None

    content = getattr(note, "content", {}) or {}

    for key in ("decision", "recommendation", "final_decision"):
        value = content.get(key)

        if isinstance(value, dict):
            value = value.get("value")

        if value:
            return str(value)

    invitation = note_invitation(note)

    if invitation:
        return invitation

    return "Decision announced"


def is_dead_submission(venue: str, venueid: str) -> bool:
    """Decide whether a submission is effectively dead.

    Dead submissions are withdrawn, desk-rejected, submitted to CORR,
    or published to arXiv only — nothing further will happen on them.

    Args:
        venue: The human-readable venue name.
        venueid: The venue id (both checked case-insensitively).

    Returns:
        ``True`` when a dead marker is present.
    """
    venue_lower = str(venue or "").lower()
    venue_id_lower = str(venueid or "").lower()

    return (
        "withdrawn" in venue_id_lower
        or "withdrawn" in venue_lower
        or "desk-rejected" in venue_id_lower
        or "desk_rejected" in venue_id_lower
        or "desk-rejected" in venue_lower
        or "desk_rejected" in venue_lower
        or "corr" in venue_lower
        or "arxiv" in venue_lower
    )


def previous_submission_id(note: Any) -> str | None:
    """Extract the previous cycle's submission id from a submission note.

    ARR resubmissions link to their earlier version via a content URL
    field: ``previous_URL`` on ARR submissions, ``paper_link`` on
    commitment venues. The URL looks like
    ``https://openreview.net/forum?id=<note id>``.

    Args:
        note: A submission note-like object.

    Returns:
        The previous submission's note id, or ``None`` when the note
        carries no usable link.
    """
    for key in ("previous_URL", "paper_link"):
        url = content_value(note, key)
        if not isinstance(url, str) or not url:
            continue
        match = re.search(r"[?&]id=([A-Za-z0-9]+)", url)
        if match:
            return match.group(1)
    return None
