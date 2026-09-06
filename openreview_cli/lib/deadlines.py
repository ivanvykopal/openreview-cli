"""Venue stage deadlines derived from OpenReview invitations.

Library layer for the "deadlines" command: fetches a venue's
invitations, maps them to friendly stage names (submission, review,
rebuttal, ...), and converts their ``cdate``/``duedate``/``expdate``
millisecond timestamps into ISO-8601 :class:`~openreview_cli.models.Deadline`
records. Nothing here prints.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from ..client import OpenReviewFacade
from ..exceptions import APIError, VenueNotFoundError
from ..helpers import timestamp_to_iso
from ..models import Deadline

#: Friendly stage name -> invitation suffixes (case-sensitive substrings
#: to look for in the invitation id).
INVITATION_NAMES: dict[str, list[str]] = {
    "submission": ["Submission"],
    "review": ["Official_Review", "Review"],
    "rebuttal": ["Rebuttal", "Author_Revision"],
    "meta_review": ["Meta_Review", "Meta-Review"],
    "decision": ["Decision"],
    "camera_ready": ["Camera_Ready", "CameraReady"],
}

#: Maximum number of invitations requested per API call.
INVITATION_BATCH_LIMIT = 1000


def now_ms() -> int:
    """Return the current time in milliseconds since the Unix epoch."""
    return int(time.time() * 1000)


def _friendly_name(invitation_id: str) -> str:
    """Map an invitation id to a friendly stage name.

    Args:
        invitation_id: Full invitation id, e.g.
            ``"aclweb.org/ACL/ARR/2026/August/-/Official_Review"``.

    Returns:
        The friendly name whose matching suffix is the longest (so
        ``Meta_Review`` maps to ``"meta_review"`` and not the shorter
        ``"Review"`` match), else the invitation's last segment.
    """
    best_name: Optional[str] = None
    best_len = 0

    for name, suffixes in INVITATION_NAMES.items():
        for suffix in suffixes:
            if suffix in invitation_id and len(suffix) > best_len:
                best_name = name
                best_len = len(suffix)

    if best_name is not None:
        return best_name

    return invitation_id.rstrip("/").rsplit("/", 1)[-1]


def _invitation_ms(invitation: Any, attr: str) -> Optional[int]:
    """Read a millisecond timestamp attribute from an invitation."""
    value = getattr(invitation, attr, None)

    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _invitation_id(invitation: Any) -> Optional[str]:
    """Read an invitation's id, tolerating missing attributes."""
    value = getattr(invitation, "id", None)
    return str(value) if value else None


def _to_deadline(venue: str, invitation: Any, now: int) -> Deadline:
    """Build a :class:`Deadline` from a single invitation object."""
    invitation_id = _invitation_id(invitation) or ""
    name = _friendly_name(invitation_id)

    start_ms = _invitation_ms(invitation, "cdate")
    end_ms = None

    for attr in ("duedate", "expdate"):
        end_ms = _invitation_ms(invitation, attr)
        if end_ms is not None:
            break

    start = timestamp_to_iso(start_ms)
    end = timestamp_to_iso(end_ms)

    is_open = False

    if start_ms is not None and start_ms <= now:
        is_open = end_ms is None or now <= end_ms
    elif start_ms is None:
        # No cdate: treat a not-yet-due invitation as open.
        is_open = end_ms is None or now <= end_ms

    return Deadline(
        venue=venue,
        invitation_id=invitation_id,
        name=name,
        start=start,
        end=end,
        is_open=is_open,
    )


def _fetch_invitations(
    facade: OpenReviewFacade,
    venue: str,
) -> list[Any]:
    """Fetch a venue's invitations via prefix (or ids) queries.

    Tries ``get_invitations(prefix=venue + "/")`` first; when the facade
    rejects the prefix parameter, falls back to ``get_invitations(ids=...)``
    for well-known stage invitation ids.

    Raises:
        VenueNotFoundError: If the API reports the venue/invitations as
            not found.
        APIError: For any other API failure.
    """
    prefix = venue.rstrip("/") + "/"

    try:
        invitations = facade.get_invitations(prefix=prefix, limit=INVITATION_BATCH_LIMIT)
    except Exception as first_exc:  # noqa: BLE001 - SDK raises varied types
        message = str(first_exc).lower()

        if "not found" in message or "404" in message:
            raise VenueNotFoundError(venue) from first_exc

        # Prefix queries may be unsupported; fall back to id lookups.
        ids = [f"{venue}/-/{suffix}" for suffixes in INVITATION_NAMES.values() for suffix in suffixes]

        try:
            invitations = facade.get_invitations(ids=ids, limit=INVITATION_BATCH_LIMIT)
        except Exception as second_exc:  # noqa: BLE001
            second_message = str(second_exc).lower()

            if "not found" in second_message or "404" in second_message:
                raise VenueNotFoundError(venue) from second_exc

            raise APIError(
                f"Could not read invitations for venue {venue}: {second_exc}"
            ) from second_exc

    if invitations is None:
        return []

    if isinstance(invitations, dict):
        invitations = invitations.get("invitations") or invitations.get("results") or []

    return list(invitations)


def fetch_deadlines(facade: OpenReviewFacade, venue: str) -> list[Deadline]:
    """Fetch the stage deadlines of a venue.

    Args:
        facade: Rate-limited OpenReview facade.
        venue: Venue group id, e.g. ``"aclweb.org/ACL/ARR/2026/August"``.

    Returns:
        :class:`~openreview_cli.models.Deadline` records for every
        invitation that defines at least a ``cdate``, ``duedate``, or
        ``expdate``. Stages without any date are skipped.

    Raises:
        VenueNotFoundError: If the venue's invitations cannot be found.
        APIError: If the invitations cannot be read for another reason.
    """
    now = now_ms()
    deadlines: list[Deadline] = []

    for invitation in _fetch_invitations(facade, venue):
        start_ms = _invitation_ms(invitation, "cdate")
        end_ms = _invitation_ms(invitation, "duedate") or _invitation_ms(
            invitation, "expdate"
        )

        if start_ms is None and end_ms is None:
            continue

        deadlines.append(_to_deadline(venue, invitation, now))

    return deadlines


def deadline_state(deadline: Deadline, now: int | None = None) -> str:
    """Classify a deadline relative to the current time.

    Args:
        deadline: The deadline record (``start``/``end`` are ISO strings).
        now: Optional current time in ms since the epoch (for tests);
            defaults to :func:`now_ms`.

    Returns:
        ``"upcoming"`` when now is before ``start``, ``"closed"`` when
        now is after ``end``, and ``"open"`` otherwise. A deadline with
        neither bound is treated as open; one with only a start as open
        from that point on; one with only an end as open until then.
    """
    if now is None:
        now = now_ms()

    start_ms = _iso_to_ms(deadline.start)
    end_ms = _iso_to_ms(deadline.end)

    if start_ms is not None and now < start_ms:
        return "upcoming"

    if end_ms is not None and now > end_ms:
        return "closed"

    return "open"


def _iso_to_ms(value: Optional[str]) -> Optional[int]:
    """Convert an ISO-8601 string back to ms since the epoch, or None."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return int(parsed.timestamp() * 1000)
