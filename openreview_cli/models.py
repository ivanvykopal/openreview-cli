"""Data models for the OpenReview CLI.

Plain dataclasses mirroring the domain objects surfaced by the tool:
submissions, reviews, meta-reviews, decisions, deadlines, and reviewer
assignments. Field names are chosen to match the OpenReview API / spec
verbatim (e.g. ``venueid``) so mapping from API notes is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Submission:
    """A paper submitted to a venue.

    ``status`` is one of ``SUBMITTED``, ``REVIEWS``, ``META_REVIEW``,
    ``DECISION``; ``status_detail`` carries a human-readable detail line
    (e.g. the decision text or the number of visible reviews).
    """

    id: str
    forum: str
    title: str
    venue: str | None
    venueid: str
    authors: list[str]
    abstract: str | None
    status: str
    status_detail: str
    previous_submission_id: str | None = None

@dataclass
class Review:
    """A peer review note attached to a submission."""

    id: str
    submission_id: str
    reviewer: str | None
    rating: str | None
    confidence: str | None
    content: dict
    created: str | None


@dataclass
class MetaReview:
    """A meta-review (area chair) note attached to a submission."""

    id: str
    submission_id: str
    recommendation: str | None
    content: dict
    created: str | None


@dataclass
class Decision:
    """An accept/reject decision note attached to a submission."""

    id: str
    submission_id: str
    decision: str
    created: str | None


@dataclass
class Comment:
    """A comment or rebuttal note attached to a submission.

    ``role`` says who wrote it: ``"authors"`` (a rebuttal),
    ``"reviewers"``, or ``"other"``. ``reply_to`` is the id of the note
    it responds to, so a reviewer reply to a rebuttal can be matched to
    it.
    """

    id: str
    submission_id: str
    role: str
    reply_to: str | None
    signatures: list[str]
    title: str | None
    comment: str | None
    created: str | None


@dataclass
class Deadline:
    """A venue stage derived from an OpenReview invitation.

    ``start`` and ``end`` are ISO-8601 strings (or ``None`` when the
    invitation does not define them); ``is_open`` reflects whether the
    stage is currently accepting submissions.
    """

    venue: str
    invitation_id: str
    name: str
    start: str | None
    end: str | None
    is_open: bool


@dataclass
class Assignment:
    """A paper assigned to the current user for reviewing."""

    submission_id: str
    forum: str
    title: str
    abstract: str | None
    authors: list[str]
    checklist: dict | None
    pdf_url: str | None
    supplementary_url: str | None
