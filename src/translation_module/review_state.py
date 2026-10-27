# Copyright 2026 Transcription Module contributors

"""Typed translation review attempts, assignments, and deterministic routing."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from translation_module.common import FrozenModel, Sha256Digest, SourceCueId
from translation_module.workflow import (
    ReviewAssignment,
    ReviewDomain,
    ReviewKind,
    ReviewResult,
)

HIGH_RISK_DOMAINS = frozenset(
    {
        ReviewDomain.FIDELITY,
        ReviewDomain.SPEAKER,
        ReviewDomain.CULTURE,
        ReviewDomain.LYRICS,
    }
)


class RevisionRiskClass(StrEnum):
    """Candidate-changing revision limit category."""

    GENERAL = "general"
    HIGH_RISK = "high_risk"


class RevisionEntry(FrozenModel):
    """One candidate-changing revision and its preceding class-wide audit."""

    before_candidate_digest: Sha256Digest
    after_candidate_digest: Sha256Digest
    risk_class: RevisionRiskClass
    reviewed_domains: tuple[ReviewDomain, ...] = Field(min_length=1)
    defect_classes: tuple[str, ...] = Field(min_length=1)
    audited_source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def entry_is_consistent(self) -> Self:
        """Reject no-op revisions and duplicate audit declarations."""
        if self.before_candidate_digest == self.after_candidate_digest:
            message = "revision must change candidate digest"
            raise ValueError(message)
        if len(self.reviewed_domains) != len(set(self.reviewed_domains)):
            message = "revision reviewed domains must be unique"
            raise ValueError(message)
        if any(not defect_class for defect_class in self.defect_classes):
            message = "revision defect classes must be nonempty"
            raise ValueError(message)
        if len(self.defect_classes) != len(set(self.defect_classes)):
            message = "revision defect classes must be unique"
            raise ValueError(message)
        if len(self.audited_source_cue_ids) != len(set(self.audited_source_cue_ids)):
            message = "revision audit scope entries must be unique"
            raise ValueError(message)
        return self


class ReviewAttempts(FrozenModel):
    """Continuous candidate revision lineage used to derive attempt counts."""

    root_candidate_digest: Sha256Digest
    current_candidate_digest: Sha256Digest
    revisions: tuple[RevisionEntry, ...] = ()
    focused_domains_reviewed: tuple[ReviewDomain, ...] = ()
    focused_review: ReviewResult | None = None
    focused_assignment: ReviewAssignment | None = None

    @model_validator(mode="after")
    def revision_chain_is_continuous(self) -> Self:
        """Reject reset, disconnected, or incorrectly terminated chains."""
        if (self.focused_review is None) != (self.focused_assignment is None):
            message = "focused review requires its parent assignment"
            raise ValueError(message)
        if not self.revisions:
            if self.root_candidate_digest != self.current_candidate_digest:
                message = "empty revision chain requires root and current to match"
                raise ValueError(message)
            if self.focused_review is not None and (
                self.focused_review.kind is not ReviewKind.FOCUSED
                or self.focused_review.candidate_digest != self.current_candidate_digest
            ):
                message = "focused review must match the current revised candidate"
                raise ValueError(message)
            return self
        if self.revisions[0].before_candidate_digest != self.root_candidate_digest:
            message = "revision chain must start at root candidate"
            raise ValueError(message)
        if any(
            previous.after_candidate_digest != current.before_candidate_digest
            for previous, current in zip(
                self.revisions,
                self.revisions[1:],
                strict=False,
            )
        ):
            message = "revision chain must be continuous"
            raise ValueError(message)
        if self.revisions[-1].after_candidate_digest != self.current_candidate_digest:
            message = "revision chain must end at current candidate"
            raise ValueError(message)
        if self.focused_review is not None and (
            self.focused_review.kind is not ReviewKind.FOCUSED
            or self.focused_review.candidate_digest != self.current_candidate_digest
        ):
            message = "focused review must match the current revised candidate"
            raise ValueError(message)
        return self

    @property
    def general(self) -> int:
        """Derive general attempt count from immutable revision entries."""
        return sum(
            any(domain not in HIGH_RISK_DOMAINS for domain in entry.reviewed_domains)
            for entry in self.revisions
        )

    @property
    def high_risk(self) -> int:
        """Derive high-risk attempt count from immutable revision entries."""
        return sum(
            any(domain in HIGH_RISK_DOMAINS for domain in entry.reviewed_domains)
            for entry in self.revisions
        )
