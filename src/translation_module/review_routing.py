# Copyright 2026 Transcription Module contributors

"""Digest-bound review assignments and deterministic review routing."""

import hashlib
import json

from translation_module.common import FrozenModel
from translation_module.review_state import HIGH_RISK_DOMAINS, ReviewAttempts
from translation_module.workflow import (
    AffectedKind,
    ReviewAssignment,
    ReviewDomain,
    ReviewKind,
    ReviewResult,
    RouteAction,
    StaleAttemptError,
    StaleReviewError,
    WorkflowError,
)


class ReviewRoute(FrozenModel):
    """Deterministic next action and required audit or focused capabilities."""

    action: RouteAction
    audit_defect_classes: tuple[str, ...] = ()
    focused_domains: tuple[ReviewDomain, ...] = ()


def review_assignment_digest(assignment: ReviewAssignment) -> str:
    """Hash canonical parent assignment content without its own digest."""
    payload = assignment.model_dump(mode="json", exclude={"assignment_digest"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def bind_review_assignment_digest(
    assignment: ReviewAssignment,
) -> ReviewAssignment:
    """Return a review assignment bound to its canonical runtime fields."""
    return assignment.model_copy(
        update={"assignment_digest": review_assignment_digest(assignment)}
    )


MAX_GENERAL_ATTEMPTS = 2
MAX_HIGH_RISK_ATTEMPTS = 3


def validate_review_assignment_scope(
    review: ReviewResult,
    assignment: ReviewAssignment,
) -> None:
    """Reject reviewer declarations outside a parent-bound assignment scope."""
    if review.assignment_digest != assignment.assignment_digest:
        message = "review does not match review assignment"
        raise WorkflowError(message)
    if review.candidate_digest != assignment.current_candidate_digest:
        message = "review does not match assigned candidate"
        raise WorkflowError(message)
    if (
        assignment.source_digest is None
        or not assignment.ordered_source_cue_ids
        or not assignment.ordered_target_cue_ids
        or assignment.reviewed_domains != tuple(ReviewDomain)
        or review_assignment_digest(assignment) != assignment.assignment_digest
    ):
        message = "review assignment scope is incomplete"
        raise WorkflowError(message)
    if review.kind is ReviewKind.INDEPENDENT:
        expected_source_ids = assignment.ordered_source_cue_ids
        expected_target_ids = assignment.ordered_target_cue_ids
        expected_domains = assignment.reviewed_domains
    else:
        expected_source_ids = assignment.focused_source_cue_ids
        expected_target_ids = assignment.focused_target_cue_ids
        expected_domains = assignment.focused_domains
        if not all((expected_source_ids, expected_target_ids, expected_domains)):
            message = "focused review has no parent-issued affected scope"
            raise WorkflowError(message)
    if review.reviewed_source_cue_ids != expected_source_ids:
        message = "review source scope does not match focused assignment"
        raise WorkflowError(message)
    if review.reviewed_target_cue_ids != expected_target_ids:
        message = "review target scope does not match focused assignment"
        raise WorkflowError(message)
    if review.reviewed_domains != expected_domains:
        message = "review domains do not match focused assignment"
        raise WorkflowError(message)
    source_ids = set(expected_source_ids)
    target_ids = set(expected_target_ids)
    if any(
        (
            affected.kind is AffectedKind.SOURCE_CUE
            and affected.item_id not in source_ids
        )
        or (
            affected.kind is AffectedKind.TARGET_CUE
            and affected.item_id not in target_ids
        )
        or affected.kind is AffectedKind.CONTEXT
        for finding in review.findings
        for affected in finding.affected
    ):
        message = "review affected reference is outside assignment"
        raise WorkflowError(message)


def validate_review_state(
    candidate_digest: str,
    review: ReviewResult,
    assignment: ReviewAssignment,
    attempts: ReviewAttempts,
) -> tuple[ReviewDomain, ...]:
    """Validate complete parent-issued review and focused-attempt lineage."""
    if (
        attempts.root_candidate_digest != assignment.root_candidate_digest
        or attempts.current_candidate_digest != assignment.current_candidate_digest
        or assignment.current_candidate_digest != candidate_digest
    ):
        msg = "attempt chain does not match review assignment"
        raise WorkflowError(msg)
    validate_review_assignment_scope(review, assignment)
    if attempts.focused_review is None:
        if attempts.focused_domains_reviewed:
            msg = "focused state has no parent-issued review"
            raise WorkflowError(msg)
        return ()
    focused_assignment = attempts.focused_assignment
    if focused_assignment is None:
        msg = "focused state has no parent-issued assignment"
        raise WorkflowError(msg)
    validate_review_assignment_scope(attempts.focused_review, focused_assignment)
    if (
        focused_assignment.root_candidate_digest != assignment.root_candidate_digest
        or focused_assignment.current_candidate_digest != candidate_digest
        or attempts.focused_domains_reviewed != attempts.focused_review.reviewed_domains
    ):
        msg = "focused state does not match candidate lineage"
        raise WorkflowError(msg)
    return attempts.focused_domains_reviewed


def route_review(
    review: ReviewResult,
    *,
    expected_candidate_digest: str,
    attempts: ReviewAttempts,
    focused_domains_reviewed: tuple[ReviewDomain, ...] = (),
    assignment: ReviewAssignment | None = None,
) -> ReviewRoute:
    """Choose the next action without persisting transient review state."""
    if review.candidate_digest != expected_candidate_digest:
        message = "review result is stale"
        raise StaleReviewError(message)
    if attempts.current_candidate_digest != expected_candidate_digest:
        message = "attempt state is stale"
        raise StaleAttemptError(message)
    if assignment is not None:
        validate_review_assignment_scope(review, assignment)
    review_scope = set(review.reviewed_source_cue_ids)
    if any(
        not review_scope.issubset(entry.audited_source_cue_ids)
        for entry in attempts.revisions
    ):
        message = "revision audit scope must cover the candidate review scope"
        raise WorkflowError(message)
    findings = review.findings
    if not findings:
        return ReviewRoute(action=RouteAction.FINALIZE)
    completed_audits = {
        audit.defect_class: set(audit.reviewed_source_cue_ids)
        for audit in review.completed_defect_audits
    }
    missing_audit_classes = tuple(
        dict.fromkeys(
            finding.defect_class
            for finding in findings
            if not review_scope.issubset(
                completed_audits.get(finding.defect_class, set())
            )
        )
    )
    if missing_audit_classes:
        return ReviewRoute(
            action=RouteAction.AUDIT,
            audit_defect_classes=missing_audit_classes,
        )
    high_risk_domains = tuple(
        domain
        for domain in ReviewDomain
        if domain in HIGH_RISK_DOMAINS
        and any(finding.domain is domain for finding in findings)
    )
    reviewed = set(focused_domains_reviewed)
    missing_focused_review = tuple(
        domain
        for domain in high_risk_domains
        if domain not in reviewed
        and any(finding.blocking and finding.domain is domain for finding in findings)
    )
    if missing_focused_review:
        return ReviewRoute(
            action=RouteAction.FOCUSED_REVIEW,
            focused_domains=missing_focused_review,
        )
    blockers = tuple(finding for finding in findings if finding.blocking)
    if any(finding.required_change is None for finding in blockers):
        return ReviewRoute(action=RouteAction.ESCALATE)
    has_general_finding = any(
        finding.domain not in HIGH_RISK_DOMAINS for finding in findings
    )
    high_risk_exhausted = (
        bool(high_risk_domains) and attempts.high_risk >= MAX_HIGH_RISK_ATTEMPTS
    )
    general_exhausted = has_general_finding and attempts.general >= MAX_GENERAL_ATTEMPTS
    if high_risk_exhausted or general_exhausted:
        return ReviewRoute(action=RouteAction.ESCALATE)
    return ReviewRoute(action=RouteAction.REVISE)
