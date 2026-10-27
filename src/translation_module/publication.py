# Copyright 2026 Transcription Module contributors

"""Atomic validation and independent-review publication gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from translation_module.canonical._selected_store import load_selected_candidate
from translation_module.canonical.legacy_authority import project_legacy_candidate
from translation_module.document import DocumentStatus, TranslationDocument
from translation_module.review_routing import (
    review_assignment_digest,
    route_review,
    validate_review_assignment_scope,
)
from translation_module.review_state import (
    ReviewAttempts,
)
from translation_module.source import SourceMaturity, SourceSnapshot
from translation_module.validation import validate_translation
from translation_module.workflow import (
    ReviewAssignment,
    ReviewDomain,
    ReviewKind,
    ReviewResult,
    RouteAction,
    WorkflowError,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class PublicationReviewInputs:
    """Independent review state required for publication."""

    assignment: ReviewAssignment
    attempts: ReviewAttempts


class PublicationError(Exception):
    """A validated candidate is not eligible for publication."""


def _publish_reviewed_translation(
    source: SourceSnapshot,
    candidate: TranslationDocument,
    review: ReviewResult,
    *,
    assignment: ReviewAssignment,
    attempts: ReviewAttempts,
) -> TranslationDocument:
    """Publish only a validated candidate approved by its exact review route."""
    if source.maturity is SourceMaturity.DRAFT:
        message = "draft source cannot be published"
        raise PublicationError(message)
    try:
        attempts = ReviewAttempts.model_validate(attempts.model_dump())
    except ValidationError as error:
        message = "attempt state is invalid"
        raise PublicationError(message) from error
    validate_translation(source, candidate)
    if review.kind is ReviewKind.FOCUSED:
        message = "focused review cannot publish a candidate directly"
        raise PublicationError(message)
    _validate_attestation(candidate, assignment, review, attempts)
    if attempts.revisions:
        _validate_focused_attestation(assignment, attempts)
    source_cue_ids = tuple(cue.cue_id for cue in source.cues)
    if len(review.reviewed_source_cue_ids) != len(source_cue_ids) or set(
        review.reviewed_source_cue_ids
    ) != set(source_cue_ids):
        message = "independent review source cue coverage is incomplete"
        raise PublicationError(message)
    if len(review.reviewed_domains) != len(ReviewDomain) or set(
        review.reviewed_domains
    ) != set(ReviewDomain):
        message = "independent review domain coverage is incomplete"
        raise PublicationError(message)
    route = route_review(
        review,
        expected_candidate_digest=candidate.content_digest,
        attempts=attempts,
        focused_domains_reviewed=attempts.focused_domains_reviewed,
        assignment=assignment,
    )
    publishable = (
        route.action is RouteAction.FINALIZE
        and candidate.status is DocumentStatus.FINALIZED
        and not candidate.unresolved
    )
    if not publishable:
        message = f"{route.action.value} route does not match candidate status"
        raise PublicationError(message)
    return candidate


def publish_translation(
    authority_root: Path,
    source: SourceSnapshot,
    candidate: TranslationDocument,
    review: ReviewResult,
    review_inputs: PublicationReviewInputs,
) -> TranslationDocument:
    """Reload persisted canonical authority before legacy publication."""
    projected = project_legacy_candidate(source, candidate, authority_root)
    intended = projected.candidate
    canonical = load_selected_candidate(
        authority_root,
        intended.document_id,
        intended.revision_id,
        projected.source,
        projected.glossary,
    )
    if canonical != intended:
        message = "canonical authority does not bind the legacy candidate"
        raise PublicationError(message)
    return _publish_reviewed_translation(
        source,
        candidate,
        review,
        assignment=review_inputs.assignment,
        attempts=review_inputs.attempts,
    )


def _validate_attestation(
    candidate: TranslationDocument,
    assignment: ReviewAssignment,
    review: ReviewResult,
    attempts: ReviewAttempts,
) -> None:
    """Verify canonical assignment and candidate-lineage bindings."""
    if review_assignment_digest(assignment) != assignment.assignment_digest:
        message = "review assignment digest mismatch"
        raise PublicationError(message)
    if assignment.current_candidate_digest != candidate.content_digest:
        message = "review assignment does not match candidate"
        raise PublicationError(message)
    if (
        attempts.root_candidate_digest != assignment.root_candidate_digest
        or attempts.current_candidate_digest != assignment.current_candidate_digest
    ):
        message = "attempt chain does not match review assignment"
        raise PublicationError(message)
    if review.assignment_digest != assignment.assignment_digest:
        message = "review does not match review assignment"
        raise PublicationError(message)
    candidate_target_cue_ids = tuple(cue.cue_id for cue in candidate.cues)
    if (
        assignment.ordered_target_cue_ids != candidate_target_cue_ids
        or review.reviewed_target_cue_ids != candidate_target_cue_ids
    ):
        message = "review target scope does not match candidate"
        raise PublicationError(message)


def _validate_focused_attestation(
    assignment: ReviewAssignment,
    attempts: ReviewAttempts,
) -> None:
    """Require a parent-issued focused review for the current revised candidate."""
    focused_review = attempts.focused_review
    focused_assignment = attempts.focused_assignment
    if focused_review is None or focused_assignment is None:
        message = "revised candidate requires current focused rereview"
        raise PublicationError(message)
    if (
        focused_assignment.root_candidate_digest != assignment.root_candidate_digest
        or focused_assignment.current_candidate_digest
        != assignment.current_candidate_digest
        or focused_assignment.candidate_author_execution_id
        != assignment.candidate_author_execution_id
        or focused_assignment.reviewer_execution_id
        == assignment.candidate_author_execution_id
    ):
        message = "focused review assignment does not match candidate lineage"
        raise PublicationError(message)
    try:
        validate_review_assignment_scope(focused_review, focused_assignment)
    except WorkflowError as error:
        message = f"focused review is invalid: {error}"
        raise PublicationError(message) from error
