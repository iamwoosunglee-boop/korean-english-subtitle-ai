# Copyright 2026 Transcription Module contributors

"""Deterministic routing for translation review and user answers."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from translation_module.common import (
    FrozenModel,
    Sha256Digest,
    SourceCueId,
    TargetCueId,
)


class WorkflowError(Exception):
    """Base error for rejected translation workflow input."""


class StaleReviewError(WorkflowError):
    """Review belongs to different candidate bytes."""


class StaleAttemptError(WorkflowError):
    """Revision-attempt state belongs to different candidate bytes."""


class AffectedKind(StrEnum):
    """Typed review finding target."""

    SOURCE_CUE = "source_cue"
    TARGET_CUE = "target_cue"
    CONTEXT = "context"


class FindingSeverity(StrEnum):
    """Reviewer-assigned impact severity."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ReviewDomain(StrEnum):
    """Closed v1 review capability tags."""

    FIDELITY = "fidelity"
    SPEAKER = "speaker"
    CULTURE = "culture"
    LYRICS = "lyrics"
    NATURALNESS = "naturalness"
    SEGMENTATION = "segmentation"


class RouteAction(StrEnum):
    """Next orchestrator action after one independent review."""

    FINALIZE = "finalize"
    AUDIT = "audit"
    FOCUSED_REVIEW = "focused_review"
    REVISE = "revise"
    ESCALATE = "escalate"


class ReviewKind(StrEnum):
    """Reviewer authority for publication."""

    INDEPENDENT = "independent"
    FOCUSED = "focused"


class AffectedReference(FrozenModel):
    """One source, target or retained-context review reference."""

    kind: AffectedKind
    item_id: str = Field(min_length=1)


class ReviewAssignment(FrozenModel):
    """Parent-issued reviewer assignment bound to one candidate lineage."""

    assignment_digest: Sha256Digest
    root_candidate_digest: Sha256Digest
    current_candidate_digest: Sha256Digest
    candidate_author_execution_id: str = Field(min_length=1)
    reviewer_execution_id: str = Field(min_length=1)
    source_digest: Sha256Digest | None = None
    ordered_source_cue_ids: tuple[SourceCueId, ...] = ()
    ordered_target_cue_ids: tuple[TargetCueId, ...] = ()
    reviewed_domains: tuple[ReviewDomain, ...] = ()
    translator_session_id: str | None = None
    reviser_session_id: str | None = None
    assembler_session_id: str | None = None
    focused_source_cue_ids: tuple[SourceCueId, ...] = ()
    focused_target_cue_ids: tuple[TargetCueId, ...] = ()
    focused_domains: tuple[ReviewDomain, ...] = ()

    @model_validator(mode="after")
    def executions_are_distinct(self) -> Self:
        """Require the parent runtime to name separate declared executions."""
        if self.candidate_author_execution_id == self.reviewer_execution_id:
            message = "review assignment requires different execution IDs"
            raise ValueError(message)
        runtime_sessions = (
            self.translator_session_id,
            self.reviser_session_id,
            self.assembler_session_id,
        )
        sessions = (*runtime_sessions, self.reviewer_execution_id)
        if any(session is not None for session in runtime_sessions) and (
            None in sessions or len(sessions) != len(set(sessions))
        ):
            message = "review assignment requires distinct runtime sessions"
            raise ValueError(message)
        if self.ordered_source_cue_ids and self.reviewed_domains != tuple(ReviewDomain):
            message = "review assignment requires exactly six review domains"
            raise ValueError(message)
        focused_scope = (
            self.focused_source_cue_ids,
            self.focused_target_cue_ids,
            self.focused_domains,
        )
        if any(focused_scope) and not all(focused_scope):
            message = "focused assignment requires source, target, and domain scope"
            raise ValueError(message)
        if (
            not set(self.focused_source_cue_ids).issubset(self.ordered_source_cue_ids)
            or not set(self.focused_target_cue_ids).issubset(
                self.ordered_target_cue_ids
            )
            or not set(self.focused_domains).issubset(self.reviewed_domains)
        ):
            message = "focused assignment scope must be within parent assignment"
            raise ValueError(message)
        return self


class ReviewFinding(FrozenModel):
    """Structured independent-review finding."""

    finding_id: str = Field(min_length=1)
    candidate_digest: Sha256Digest
    affected: tuple[AffectedReference, ...] = Field(min_length=1)
    severity: FindingSeverity
    domain: ReviewDomain
    defect_class: str = Field(min_length=1)
    blocking: bool
    problem: str = Field(min_length=1)
    required_change: str | None = None


class CompletedDefectAudit(FrozenModel):
    """Candidate-wide audit completed for one defect class."""

    defect_class: str = Field(min_length=1)
    reviewed_source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def scope_is_unique(self) -> Self:
        """Reject duplicated source scope declarations."""
        if len(self.reviewed_source_cue_ids) != len(set(self.reviewed_source_cue_ids)):
            message = "completed defect audit scope entries must be unique"
            raise ValueError(message)
        return self


class ReviewResult(FrozenModel):
    """Reviewer findings and scope bound to one parent assignment."""

    candidate_digest: Sha256Digest
    assignment_digest: Sha256Digest | None = None
    reviewed_source_cue_ids: tuple[SourceCueId, ...] = ()
    reviewed_target_cue_ids: tuple[TargetCueId, ...]
    reviewed_domains: tuple[ReviewDomain, ...] = ()
    completed_defect_audits: tuple[CompletedDefectAudit, ...] = ()
    kind: ReviewKind = ReviewKind.INDEPENDENT
    findings: tuple[ReviewFinding, ...] = ()

    @model_validator(mode="after")
    def review_is_internally_consistent(self) -> Self:
        """Reject stale findings and invalid declared assignment or scope data."""
        if any(
            finding.candidate_digest != self.candidate_digest
            for finding in self.findings
        ):
            message = "review findings must match candidate digest"
            raise ValueError(message)
        if len(self.reviewed_source_cue_ids) != len(
            set(self.reviewed_source_cue_ids)
        ) or len(self.reviewed_domains) != len(set(self.reviewed_domains)):
            message = "review scope entries must be unique"
            raise ValueError(message)
        audit_classes = tuple(
            audit.defect_class for audit in self.completed_defect_audits
        )
        if len(audit_classes) != len(set(audit_classes)):
            message = "completed defect audit classes must be unique"
            raise ValueError(message)
        if self.kind is ReviewKind.INDEPENDENT:
            if self.assignment_digest is None:
                message = "independent review requires assignment digest"
                raise ValueError(message)
            if not self.reviewed_source_cue_ids or not self.reviewed_domains:
                message = "independent review scope cannot be empty"
                raise ValueError(message)
        elif self.assignment_digest is None or not self.reviewed_source_cue_ids:
            message = "focused review requires assignment and nonempty scope"
            raise ValueError(message)
        return self
