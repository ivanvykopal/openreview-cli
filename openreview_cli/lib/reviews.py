"""Fetching and persisting reviews, meta-reviews, and decisions.

Library layer for the "reviews" commands of the OpenReview CLI: fetch a
submission's forum notes, classify them, build the corresponding model
objects (:class:`~openreview_cli.models.Review`,
``MetaReview``, ``Decision``), and optionally dump them to per-paper
JSON files. Nothing here prints; failures are returned as error lists.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from typing import Any, Iterable

from ..client import OpenReviewFacade
from ..helpers import (
    classify_note,
    content_value,
    extract_decision,
    note_forum,
    note_id,
    note_invitation,
    note_time,
)
from ..models import Decision, MetaReview, Review, Submission
from .status import _submission_from_note


def _invitations_of(note: Any) -> list[str]:
    """Return a note's invitation strings (API v2 uses ``invitations``)."""
    invitations = getattr(note, "invitations", None)
    if invitations:
        return [str(inv) for inv in invitations]
    single = getattr(note, "invitation", None)
    return [str(single)] if single else []


def _kind_of_invitations(invitations: list[str]) -> str | None:
    """Classify a note by its exact invitation suffixes.

    Unlike :func:`~openreview_cli.helpers.classify_note` (a broad
    substring heuristic), this only matches the official review
    workflow invitations, so e.g. ``Reviewer_Checklist`` notes are
    not mistaken for reviews.
    """
    for invitation in invitations:
        low = invitation.lower()
        if low.endswith("/official_review"):
            return "review"
        if low.endswith("/meta_review") or low.endswith("/metareview"):
            return "meta_review"
        if low.endswith("/decision"):
            return "decision"
    return None


def fetch_my_reviews(
    facade: OpenReviewFacade,
    venue: str | None = None,
) -> tuple[list[Submission], dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Fetch the reviews, meta-reviews, and decisions you wrote.

    Under double-blind review your notes are signed by anonymous
    per-paper groups (e.g. ``<venue>/Submission_1/Reviewer_ABC``), not
    by your profile, so a direct ``signature`` query finds nothing.
    Instead this fetches the groups the logged-in profile is a member
    of, queries the notes signed by those groups, keeps the official
    review / meta-review / decision kinds, and groups them by the
    paper (forum) they were written on. Each forum note is fetched
    once to build the paper's :class:`~openreview_cli.models.Submission`.

    Args:
        facade: Rate-limited OpenReview facade.
        venue: Optional venue id prefix filter — when given, only notes
            with an invitation starting with this string are kept.

    Returns:
        A ``(submissions, results, errors)`` tuple. ``results`` maps
        each submission id to the same ``{"submission": ..., "reviews":
        [...], ...}`` shape as :func:`fetch_reviews`; ``errors`` is a
        list of ``{"paper": title, "error": message}`` dicts.
    """
    profile_id = facade.profile_id()
    groups = facade.get_groups(member=profile_id) or []
    signature_ids = [g.id for g in groups if getattr(g, "id", None)]

    grouped: dict[str, list[tuple[str, Any]]] = {}
    seen_note_ids: set[str] = set()
    errors: list[dict[str, str]] = []
    chunk_size = 10
    for start in range(0, len(signature_ids), chunk_size):
        chunk = signature_ids[start : start + chunk_size]
        try:
            notes = facade.get_all_notes(signatures=chunk) or []
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "paper": "",
                    "error": f"Unable to query notes for {len(chunk)} "
                    f"signature groups: {exc}",
                }
            )
            continue

        for note in notes:
            note_id_value = note_id(note)
            if note_id_value and note_id_value in seen_note_ids:
                continue

            invitations = _invitations_of(note)
            kind = _kind_of_invitations(invitations)
            if kind is None:
                continue

            if venue and not any(
                inv.lower().startswith(venue.lower()) for inv in invitations
            ):
                continue

            forum = note_forum(note)
            if forum:
                seen_note_ids.add(note_id_value or "")
                grouped.setdefault(forum, []).append((kind, note))

    submissions: list[Submission] = []
    results: dict[str, dict[str, Any]] = {}

    for forum, pairs in grouped.items():
        try:
            forum_note = facade.get_note(id=forum)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "paper": forum,
                    "error": f"Unable to read paper note {forum}: {exc}",
                }
            )
            continue

        submission = _submission_from_note(forum_note)
        reviews: list[Review] = []
        meta_reviews: list[MetaReview] = []
        decisions: list[Decision] = []

        for kind, note in pairs:
            if kind == "review":
                reviews.append(_build_review(submission.id, note))
            elif kind == "meta_review":
                meta_reviews.append(_build_meta_review(submission.id, note))
            else:
                decisions.append(_build_decision(submission.id, note))

        submissions.append(submission)
        results[submission.id] = {
            "submission": submission,
            "reviews": reviews,
            "meta_reviews": meta_reviews,
            "decisions": decisions,
        }

    return submissions, results, errors


def _reviewer_of(note: Any) -> str | None:
    """Determine a note's author from content or signatures.
    Prefers the ``reader`` content field, falling back to the note's
    signature ids, and finally to ``None``.
    """
    reader = content_value(note, "reader", None)
    if isinstance(reader, str) and reader:
        return reader

    signatures = getattr(note, "signatures", None) or []
    if signatures:
        first = signatures[0]
        return str(first) if first else None

    return None


def _content_map(note: Any) -> dict[str, Any]:
    """Flatten a note's content into ``{key: unwrapped value}``."""
    content = getattr(note, "content", {}) or {}
    return {str(key): content_value(note, str(key)) for key in content.keys()}


def _build_review(submission_id: str, note: Any) -> Review:
    """Build a :class:`Review` from a review note."""
    return Review(
        id=note_id(note) or "",
        submission_id=submission_id,
        reviewer=_reviewer_of(note),
        rating=content_value(note, "rating") or content_value(note, "Rating"),
        confidence=content_value(note, "confidence")
        or content_value(note, "Confidence"),
        content=_content_map(note),
        created=note_time(note),
    )


def _build_meta_review(submission_id: str, note: Any) -> MetaReview:
    """Build a :class:`MetaReview` from a meta-review note."""
    return MetaReview(
        id=note_id(note) or "",
        submission_id=submission_id,
        recommendation=content_value(note, "recommendation")
        or content_value(note, "metareview_recommendation"),
        content=_content_map(note),
        created=note_time(note),
    )


def _build_decision(submission_id: str, note: Any) -> Decision:
    """Build a :class:`Decision` from a decision note."""
    return Decision(
        id=note_id(note) or "",
        submission_id=submission_id,
        decision=extract_decision(note) or "Decision announced",
        created=note_time(note),
    )


def build_review_objects(
    submission: Submission,
    notes: Iterable[Any],
) -> dict[str, Any]:
    """Classify forum notes and build the model objects for one paper.

    Args:
        submission: The submission the notes belong to.
        notes: The submission's forum notes.

    Returns:
        A dict with keys ``"submission"``, ``"reviews"``,
        ``"meta_reviews"``, and ``"decisions"`` holding the
        :class:`~openreview_cli.models.Submission` and the lists of
        model objects. Comments and unclassifiable notes are ignored.
    """
    reviews: list[Review] = []
    meta_reviews: list[MetaReview] = []
    decisions: list[Decision] = []

    for note in notes or []:
        kind = classify_note(note)

        if kind == "review":
            reviews.append(_build_review(submission.id, note))
        elif kind == "meta_review":
            meta_reviews.append(_build_meta_review(submission.id, note))
        elif kind == "decision":
            decisions.append(_build_decision(submission.id, note))

    return {
        "submission": submission,
        "reviews": reviews,
        "meta_reviews": meta_reviews,
        "decisions": decisions,
    }


def fetch_reviews(
    facade: OpenReviewFacade,
    submissions: list[Submission],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Fetch reviews, meta-reviews, and decisions for submissions.

    Per-paper failures (forum fetch errors and the like) are collected
    and do not abort the run.

    Args:
        facade: Rate-limited OpenReview facade.
        submissions: Submissions to fetch reviews for.

    Returns:
        A ``(results, errors)`` tuple. ``results`` maps each submission
        id to ``{"submission": Submission, "reviews": [...],
        "meta_reviews": [...], "decisions": [...]}``; ``errors`` is a
        list of ``{"paper": title, "error": message}`` dicts.
    """
    results: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

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
            results[submission.id] = build_review_objects(submission, notes)
        except Exception as exc:  # noqa: BLE001
            errors.append({"paper": submission.title, "error": str(exc)})

    return results, errors


def save_reviews_json(
    results: dict[str, dict[str, Any]],
    output_dir: Path,
) -> list[dict[str, str]]:
    """Write per-paper review JSON files into ``output_dir``.

    Each file is named ``<submission_id>.json`` and contains the
    serialized result dict for that paper (dataclasses via
    :func:`dataclasses.asdict`). Failures are collected per paper and do
    not abort the run.

    Args:
        results: The ``results`` part of :func:`fetch_reviews`'s return.
        output_dir: Directory to write the files into (created if
            missing).

    Returns:
        A list of ``{"paper": submission_id, "error": message}`` dicts,
        one per failed paper.
    """
    errors: list[dict[str, str]] = []

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return [
            {
                "paper": "",
                "error": f"Cannot create output directory {output_dir}: {exc}",
            }
        ]

    for submission_id, payload in results.items():
        try:
            serializable = {
                "submission": dataclasses.asdict(payload["submission"]),
                "reviews": [dataclasses.asdict(r) for r in payload["reviews"]],
                "meta_reviews": [
                    dataclasses.asdict(m) for m in payload["meta_reviews"]
                ],
                "decisions": [dataclasses.asdict(d) for d in payload["decisions"]],
            }

            path = output_dir / f"{submission_id}.json"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(serializable, fh, indent=2, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {"paper": submission_id, "error": str(exc)}
            )

    return errors
