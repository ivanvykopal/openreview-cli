"""Submission discovery, analysis, and change tracking.

Library layer for the "status" commands of the OpenReview CLI:

- ARR cycle discovery (:func:`get_arr_cycles`) and open-cycle detection
  (:func:`arr_cycle_open`),
- finding the current user's submissions (:func:`find_my_submissions`),
- analysing a submission's forum to derive its status
  (:func:`analyze_submission`),
- SQLite-backed change tracking (:class:`StatusDB`), and
- the combined :func:`run_status_check` entry point.

Ported from the original standalone monitor script; behaviour is
identical but nothing here prints — all diagnostics are returned as data
so the CLI layer decides how to surface them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..client import OpenReviewFacade
from ..helpers import (
    ARR_CYCLE_RE,
    ARR_GROUP_ID,
    arr_cycle_id_of_submission,
    classify_note,
    content_value,
    extract_decision,
    is_dead_submission,
    is_probable_submission,
    note_forum,
    note_id,
    note_invitation,
    note_time,
    previous_submission_id,
)
from ..models import Submission


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _note_latest_ms(note: Any) -> int:
    """Return a note's best-effort most recent activity timestamp (ms)."""
    for attr in ("mdate", "cdate", "tmdate"):
        value = getattr(note, attr, None)
        if value:
            return int(value)
    return 0


# ---------------------------------------------------------------------------
# ARR cycles
# ---------------------------------------------------------------------------

def get_arr_cycles(facade: OpenReviewFacade) -> list[tuple[str, int]] | None:
    """Fetch all ARR cycle groups.

    Args:
        facade: Rate-limited OpenReview facade.

    Returns:
        A list of ``(cycle_id, cdate_ms)`` tuples sorted by cdate, oldest
        first — e.g. ``("aclweb.org/ACL/ARR/2026/August", 1782736166928)`` —
        or ``None`` if the parent group cannot be read (offline, API
        change). Individual unreadable cycle groups are skipped silently.
    """
    try:
        parent = facade.get_group(ARR_GROUP_ID)
    except Exception:  # noqa: BLE001 - any API failure means "unknown"
        return None

    cycles: list[tuple[str, int]] = []

    for member in getattr(parent, "members", None) or []:
        if not ARR_CYCLE_RE.fullmatch(str(member)):
            continue

        try:
            group = facade.get_group(member)
        except Exception:  # noqa: BLE001
            continue

        cdate = getattr(group, "cdate", None)

        if cdate:
            cycles.append((str(member), int(cdate)))

    cycles.sort(key=lambda item: item[1])
    return cycles


def arr_cycle_open(facade: OpenReviewFacade, cycle_id: str) -> bool:
    """Check whether an ARR cycle has started accepting submissions.

    Cycle groups are created days before the cycle opens, with a
    backdated cdate, so group creation alone is not enough. The reliable
    signal is the cycle's ``Submission`` invitation: it exists (possibly
    already expired = submissions closed, cycle under review) once the
    cycle has opened, and is not found before.

    Args:
        facade: Rate-limited OpenReview facade.
        cycle_id: ARR cycle group id, e.g. ``aclweb.org/ACL/ARR/2026/August``.

    Returns:
        ``True`` if the cycle has opened (including "expired" — submissions
        closed but the cycle is under review); ``False`` if the invitation
        was never created or on any unknown error. This function is
        silent by design; the caller logs.
    """
    try:
        facade.get_invitation(f"{cycle_id}/-/Submission")
        return True
    except Exception as exc:  # noqa: BLE001 - SDK raises varied types
        # OpenReviewException str() is the dict repr, so check both the
        # message and the error name.
        message = str(exc).lower()

        name = ""
        args = getattr(exc, "args", None)
        if args and isinstance(args[0], dict):
            name = str(args[0].get("name", "")).lower()

        if "expired" in message or "expired" in name:
            # Invitation existed but expired: the cycle opened.
            return True

        if "notfound" in name or "not found" in message:
            # Invitation never created: cycle not open yet.
            return False

        # Unknown error: assume not open, silently.
        return False


def _active_arr_cycles(
    facade: OpenReviewFacade,
    cycles_back: int,
) -> set[str]:
    """Determine the set of "active" ARR cycle ids.

    The active window is the newest opened cycle plus ``cycles_back``
    earlier ones. If cycle detection fails entirely the function fails
    open: every known cycle (or, if none are known, the empty set) is
    treated as active, so an API hiccup never hides all ARR papers.
    """
    all_cycles = get_arr_cycles(facade)

    active_cycles: set[str] = set()

    if all_cycles:
        opened: list[str] = []
        # Newest first; pre-created groups can appear in any order, so
        # keep checking older cycles even after a closed one.
        for cid, _cdate in reversed(all_cycles):
            if arr_cycle_open(facade, cid):
                opened.append(cid)

        # 'opened' is newest-first; keep current + cycles_back.
        active_cycles = set(opened[: cycles_back + 1])

    if not active_cycles:
        # Fail open: treat every ARR cycle as active.
        active_cycles = {cid for cid, _ in (all_cycles or [])}

    return active_cycles


# ---------------------------------------------------------------------------
# Submission discovery
# ---------------------------------------------------------------------------

def _submission_from_note(note: Any) -> Submission:
    """Build a :class:`Submission` from an authored note.

    ``status``/``status_detail`` are left at their initial values;
    :func:`analyze_submission` fills them in.
    """
    authors = content_value(note, "authors", [])
    if not isinstance(authors, list):
        authors = []

    return Submission(
        id=note_id(note) or "",
        forum=note_forum(note) or "",
        title=str(content_value(note, "title", "(untitled)")),
        venue=content_value(note, "venue", None),
        venueid=str(content_value(note, "venueid", "") or ""),
        authors=[str(a) for a in authors],
        abstract=content_value(note, "abstract", None),
        status="SUBMITTED",
        status_detail="",
        previous_submission_id=previous_submission_id(note),
    )


def find_my_submissions(
    facade: OpenReviewFacade,
    include_old: bool = False,
    cycles_back: int = 1,
    venue: str | None = None,
) -> list[Submission]:
    """Find paper submissions authored by the current profile.

    OpenReview workflows vary between venues, so authored notes are
    inspected and obvious review/comment/etc. notes filtered out. ARR
    submissions are filtered to the active cycle window (newest opened
    cycle plus ``cycles_back`` earlier ones); non-ARR venues fall back to
    a "submitted this year" filter. Dead submissions (withdrawn,
    desk-rejected, CORR, arXiv-only) are always skipped.

    Args:
        facade: Rate-limited OpenReview facade.
        include_old: When ``True``, skip the cycle / year filters.
        cycles_back: Number of extra (older) cycles to include in the
            active window.
        venue: Optional venue id prefix filter — when given, only
            submissions whose ``venueid`` starts with this string are
            kept.

    Returns:
        Deduplicated :class:`~openreview_cli.models.Submission` list with
        ``status`` left as ``"SUBMITTED"`` (filled by
        :func:`analyze_submission`).

    Raises:
        APIError: If the profile id cannot be determined or the notes
            query fails.
    """
    profile_id = facade.profile_id()

    profile_fullname: str | None = None
    try:
        profile = facade.get_profile(profile_id)
        profile_fullname = getattr(profile, "fullname", None)
    except Exception:  # noqa: BLE001 - fullname fallback is best effort
        profile_fullname = None

    active_cycles = (
        set() if include_old else _active_arr_cycles(facade, cycles_back)
    )

    notes = facade.get_all_notes(content={"authorids": profile_id})

    current_year = datetime.now(timezone.utc).year

    unique: dict[str, Submission] = {}

    for note in notes or []:
        if not is_probable_submission(note):
            continue

        author_ids = content_value(note, "authorids", [])
        if not isinstance(author_ids, list):
            author_ids = []

        authors = content_value(note, "authors", [])
        if not isinstance(authors, list):
            authors = []

        matched = profile_id in author_ids

        # Some older/venue-specific records may only expose authors.
        if not matched and profile_fullname and profile_fullname in authors:
            matched = True

        if not matched:
            continue

        submission = _submission_from_note(note)

        # Skip dead submissions: withdrawn, desk-rejected, or arXiv-only.
        if is_dead_submission(str(submission.venue or ""), submission.venueid):
            continue

        # Venue prefix filter.
        if venue and not submission.venueid.startswith(venue):
            continue

        if not include_old:
            cycle_id = arr_cycle_id_of_submission(submission, note)

            if cycle_id:
                if cycle_id not in active_cycles:
                    continue
            else:
                # Non-ARR venue: fall back to a submission-year filter.
                submitted = getattr(note, "cdate", None) or getattr(
                    note, "tcdate", None
                )
                if submitted:
                    try:
                        year = datetime.fromtimestamp(
                            submitted / 1000, tz=timezone.utc
                        ).year
                    except (ValueError, OverflowError, OSError, TypeError):
                        year = current_year
                    if year < current_year:
                        continue

        if submission.id:
            unique[submission.id] = submission

    return list(unique.values())


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_submission(
    facade: OpenReviewFacade,
    submission: Submission,
) -> Submission:
    """Analyze a submission's forum and set its status.

    Fetches all forum notes, classifies each, and fills
    ``submission.status`` / ``submission.status_detail`` using the ladder
    ``DECISION > META-REVIEW > REVIEWS > SUBMITTED``.

    Args:
        facade: Rate-limited OpenReview facade.
        submission: The submission to analyze.

    Returns:
        The same :class:`Submission` object, mutated in place with the
        computed status.

    Raises:
        APIError: If the forum notes cannot be fetched.
    """
    notes = facade.get_all_notes(forum=submission.forum) or []
    return analyze_notes(submission, notes)


def analyze_notes(submission: Submission, notes: Iterable[Any]) -> Submission:
    """Analyze already-fetched forum notes and set a submission's status.

    Same ladder and mutation semantics as :func:`analyze_submission`,
    but the notes are supplied by the caller (so a caller that already
    fetched the forum does not pay for a second API call).

    Args:
        submission: The submission to analyze (mutated in place).
        notes: The submission's forum notes.

    Returns:
        The same :class:`Submission` object, mutated in place with the
        computed status.
    """

    reviews: list[Any] = []
    meta_reviews: list[Any] = []
    decisions: list[Any] = []
    comments: list[Any] = []

    for note in notes:
        kind = classify_note(note)

        if kind == "other":
            continue
        if kind == "review":
            reviews.append(note)
        elif kind == "meta_review":
            meta_reviews.append(note)
        elif kind == "decision":
            decisions.append(note)
        elif kind == "comment":
            comments.append(note)

    if decisions:
        latest = max(decisions, key=_note_latest_ms)
        submission.status = "DECISION"
        decision = extract_decision(latest)
        submission.status_detail = decision if decision else "Decision announced"
    elif meta_reviews:
        submission.status = "META_REVIEW"
        submission.status_detail = "Meta-review visible"
    elif reviews:
        submission.status = "REVIEWS"
        submission.status_detail = f"{len(reviews)} review(s) visible"
    else:
        submission.status = "SUBMITTED"
        submission.status_detail = "No reviews visible yet"

    return submission


# ---------------------------------------------------------------------------
# Change tracking (SQLite)
# ---------------------------------------------------------------------------

class StatusDB:
    """SQLite-backed store tracking seen submissions and notes.

    Wraps a ``sqlite3`` connection with the two-table schema used for
    change detection: ``submissions`` and ``notes``. The classic
    interface mirrors the original monitor script; the keyword interface
    (``save_submission`` / ``save_note``) accepts
    :class:`~openreview_cli.models.Submission` objects and note-like
    objects respectively.
    """

    def __init__(self, path: str) -> None:
        """Open (creating if needed) the SQLite database at ``path``.

        Args:
            path: Filesystem path of the SQLite database, or ``":memory:"``.
        """
        if path != ":memory:":
            # sqlite3.connect raises a raw OperationalError (bypassing
            # the CLI's error handling) when the parent dir is missing.
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                forum TEXT,
                title TEXT,
                venue TEXT,
                first_seen TEXT,
                last_seen TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                submission_id TEXT,
                kind TEXT,
                invitation TEXT,
                created TEXT,
                last_seen TEXT
            )
            """
        )
        self._connection.commit()

    def _submission_exists(self, submission_id: str) -> bool:
        row = self._connection.execute(
            "SELECT id FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        return row is not None

    def _note_exists(self, note_id_value: str) -> bool:
        row = self._connection.execute(
            "SELECT id FROM notes WHERE id = ?", (note_id_value,)
        ).fetchone()
        return row is not None

    def seen_note_ids(self, submission_id: str | None = None) -> set[str]:
        """Return the ids of notes already recorded in the database.

        Args:
            submission_id: Restrict to notes of one submission; when
                ``None``, all recorded note ids are returned.

        Returns:
            The set of seen note ids (empty when nothing is recorded).
        """
        if submission_id is None:
            rows = self._connection.execute("SELECT id FROM notes")
        else:
            rows = self._connection.execute(
                "SELECT id FROM notes WHERE submission_id = ?",
                (submission_id,),
            )
        return {str(row[0]) for row in rows.fetchall()}

    def save_submission(self, submission: Submission) -> None:
        """Insert or update a submission row (updating ``last_seen``).

        Args:
            submission: The submission to record.
        """
        now = _utc_now()

        if self._submission_exists(submission.id):
            self._connection.execute(
                """
                UPDATE submissions
                SET forum = ?, title = ?, venue = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    submission.forum,
                    submission.title,
                    submission.venue,
                    now,
                    submission.id,
                ),
            )
        else:
            self._connection.execute(
                """
                INSERT INTO submissions
                    (id, forum, title, venue, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.id,
                    submission.forum,
                    submission.title,
                    submission.venue,
                    now,
                    now,
                ),
            )

    def save_note(self, submission_id: str, note: Any, kind: str) -> bool:
        """Insert a note row; report whether it is new.

        Existing notes only get their ``last_seen`` refreshed.

        Args:
            submission_id: Id of the submission the note belongs to.
            note: A note-like object with ``id``, ``invitation``, and
                timestamps.
            kind: The note kind from :func:`~.helpers.classify_note`.

        Returns:
            ``True`` if the note had not been seen before, ``False``
            otherwise (or when the note has no id).
        """
        current_id = note_id(note)

        if not current_id:
            return False

        if self._note_exists(current_id):
            self._connection.execute(
                "UPDATE notes SET last_seen = ? WHERE id = ?",
                (_utc_now(), current_id),
            )
            return False

        self._connection.execute(
            """
            INSERT INTO notes
                (id, submission_id, kind, invitation, created, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                current_id,
                submission_id,
                kind,
                note_invitation(note),
                note_time(note),
                _utc_now(),
            ),
        )
        return True

    def record_and_report(
        self,
        submission: Submission,
        notes: Iterable[Any],
    ) -> list[dict[str, Any]]:
        """Record a submission and its classified notes, reporting new ones.

        Saves the submission row, then each note (classified via
        :func:`~.helpers.classify_note`; ``"other"`` notes are skipped),
        and returns an event dict for every note that is new.

        Args:
            submission: The submission the notes belong to.
            notes: The submission's forum notes.

        Returns:
            A list of ``{"type": kind, "note_id": ..., "paper": title}``
            dicts, one per newly seen note.
        """
        new_events: list[dict[str, Any]] = []

        for note in notes or []:
            kind = classify_note(note)

            if kind == "other":
                continue

            if self.save_note(submission.id, note, kind):
                new_events.append(
                    {
                        "type": kind,
                        "note_id": note_id(note),
                        "paper": submission.title,
                    }
                )

        self.save_submission(submission)
        self.commit()

        return new_events

    def commit(self) -> None:
        """Commit the current transaction."""
        self._connection.commit()

    def close(self) -> None:
        """Commit and close the connection."""
        self._connection.commit()
        self._connection.close()

    def __enter__(self) -> "StatusDB":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Combined status check
# ---------------------------------------------------------------------------

def run_status_check(
    facade: OpenReviewFacade,
    db: StatusDB,
    include_all: bool = False,
    venue: str | None = None,
) -> tuple[list[Submission], list[dict[str, str]], dict[str, list[dict[str, Any]]]]:
    """Find, analyze, and record the user's submissions.

    Per-submission failures (forum fetch errors and the like) are
    collected and do not abort the run.

    Args:
        facade: Rate-limited OpenReview facade.
        db: Change-tracking database.
        include_all: When ``True``, include old-cycle submissions.
        venue: Optional venue id prefix filter.

    Returns:
        A ``(submissions, errors, new_events)`` tuple.
        ``submissions`` are the analyzed
        :class:`~openreview_cli.models.Submission` objects with
        ``status`` filled; ``errors`` is a list of
        ``{"paper": title, "error": message}`` dicts; ``new_events``
        maps each submission id to the list of its new-note event dicts
        (as returned by :meth:`StatusDB.record_and_report`).
    """
    errors: list[dict[str, str]] = []
    new_events: dict[str, list[dict[str, Any]]] = {}

    try:
        submissions = find_my_submissions(facade, include_old=include_all, venue=venue)
    except Exception as exc:  # noqa: BLE001
        return [], [
            {"paper": "", "error": f"Unable to find submissions: {exc}"}
        ], {}

    analyzed: list[Submission] = []

    for submission in submissions:
        try:
            notes = facade.get_all_notes(forum=submission.forum) or []
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "paper": submission.title,
                    "error": f"Unable to read forum {submission.forum}: {exc}",
                }
            )
            continue

        try:
            new_events[submission.id] = db.record_and_report(submission, notes)
            # Reuse the already-fetched notes instead of re-fetching the
            # forum via analyze_submission (rate limiting would double the
            # API calls and wall-clock time of every run).
            analyzed.append(analyze_notes(submission, notes))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {"paper": submission.title, "error": str(exc)}
            )

    return analyzed, errors, new_events
