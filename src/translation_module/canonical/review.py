# Copyright 2026 Transcription Module contributors

"""Canonical immutable translation review artifact contract."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    CanonicalId,
    CanonicalModel,
    UtcTimestamp,
    validate_unique_ids,
)
from translation_module.common import Sha256Digest
from translation_module.workflow import FindingSeverity, ReviewDomain


def _is_none(value: object) -> bool:
    return value is None


class CanonicalReviewKind(StrEnum):
    """Provenance class for one review artifact."""

    INDEPENDENT_AI = "independent_ai"
    ISOLATED_SELF_REVIEW = "isolated_self_review"
    HUMAN_REVIEW = "human_review"


class ReviewArtifactResult(StrEnum):
    """Stored outcome for one exact review binding."""

    PASSED = "passed"
    FAILED = "failed"


class ReviewProfile(CanonicalModel):
    """Versioned review policy identity."""

    profile_id: CanonicalId
    version: str = Field(min_length=1)


class ReviewBinding(CanonicalModel):
    """Exact candidate and dependency identity reviewed."""

    selection_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    attempt_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    translation_document_id: CanonicalId
    translation_revision_id: CanonicalId
    candidate_content_digest: Sha256Digest
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: Sha256Digest
    prepared_context_asset_id: CanonicalId
    prepared_context_revision_id: CanonicalId
    prepared_context_content_digest: Sha256Digest
    glossary_asset_id: CanonicalId
    glossary_revision_id: CanonicalId
    glossary_content_digest: Sha256Digest
    review_profile: ReviewProfile

    @model_validator(mode="after")
    def selection_identity_is_complete(self) -> Self:
        """Require selection and attempt identities together."""
        if (self.selection_id is None) != (self.attempt_id is None):
            message = "selection_id and attempt_id must be set together"
            raise ValueError(message)
        return self


class ReviewCoverage(CanonicalModel):
    """Exact source, target, boundary, and glossary scope inspected."""

    source_cue_ids: tuple[CanonicalId, ...]
    target_cue_ids: tuple[CanonicalId, ...]
    target_boundary_pairs: tuple[tuple[CanonicalId, CanonicalId], ...]
    glossary_entry_ids: tuple[CanonicalId, ...]

    @model_validator(mode="after")
    def coverage_entries_are_unique(self) -> Self:
        """Reject repeated coverage entries and degenerate boundaries."""
        validate_unique_ids(self.source_cue_ids, "review source cue")
        validate_unique_ids(self.target_cue_ids, "review target cue")
        validate_unique_ids(self.glossary_entry_ids, "review glossary entry")
        if len(self.target_boundary_pairs) != len(set(self.target_boundary_pairs)):
            message = "review target boundary pairs must be unique"
            raise ValueError(message)
        if any(left == right for left, right in self.target_boundary_pairs):
            message = "review target boundary pair must contain distinct cues"
            raise ValueError(message)
        return self


class CanonicalReviewFinding(CanonicalModel):
    """One immutable review finding."""

    finding_id: CanonicalId
    affected_source_cue_ids: tuple[CanonicalId, ...]
    affected_target_cue_ids: tuple[CanonicalId, ...]
    domain: ReviewDomain
    severity: FindingSeverity
    blocking: bool
    problem: str = Field(min_length=1)
    required_change: str = Field(min_length=1)
    resolution_status: Literal["open"] = "open"

    @model_validator(mode="after")
    def finding_has_scope(self) -> Self:
        """Require every finding to name affected source or target cues."""
        if not self.affected_source_cue_ids and not self.affected_target_cue_ids:
            message = "review finding requires affected source or target cues"
            raise ValueError(message)
        return self


class ReviewProvenance(CanonicalModel):
    """Execution metadata retained outside review semantic identity."""

    review_kind: CanonicalReviewKind
    reviewer_execution_ids: tuple[CanonicalId, ...] = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    completed_at: UtcTimestamp


class TranslationReviewArtifact(CanonicalModel):
    """Immutable review result for one exact translation binding."""

    schema_version: Literal["translation-review.v1"] = "translation-review.v1"
    review_id: CanonicalId
    supersedes_review_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    content_digest: Sha256Digest
    record_digest: Sha256Digest
    binding: ReviewBinding
    coverage: ReviewCoverage
    result: ReviewArtifactResult
    findings: tuple[CanonicalReviewFinding, ...]
    provenance: ReviewProvenance

    @model_validator(mode="after")
    def review_is_structurally_consistent(self) -> Self:
        """Validate finding identity and the stored pass result."""
        validate_unique_ids(
            tuple(finding.finding_id for finding in self.findings),
            "review finding",
        )
        has_open_blocking = any(
            finding.blocking and finding.resolution_status == "open"
            for finding in self.findings
        )
        if self.result is ReviewArtifactResult.PASSED and has_open_blocking:
            message = "passed review cannot contain an open blocking finding"
            raise ValueError(message)
        return self


__all__ = [
    "CanonicalReviewFinding",
    "CanonicalReviewKind",
    "ReviewArtifactResult",
    "ReviewBinding",
    "ReviewCoverage",
    "ReviewProfile",
    "ReviewProvenance",
    "TranslationReviewArtifact",
]
