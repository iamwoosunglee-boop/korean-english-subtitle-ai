# Copyright 2026 Transcription Module contributors

"""Canonical translation document v2 contract."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    AssetReference,
    CanonicalId,
    CanonicalMilliseconds,
    CanonicalModel,
    ContentKind,
    DocumentLifecycleStatus,
    DocumentReference,
    PositiveCanonicalMilliseconds,
    UtcTimestamp,
    validate_contiguous_sequences,
    validate_unique_ids,
)
from translation_module.common import Sha256Digest
from translation_module.workflow import ReviewDomain


def _is_none(value: object) -> bool:
    return value is None


class LineageStatus(StrEnum):
    """Confidence in one target cue's source lineage."""

    UNASSIGNED = "unassigned"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CONFIRMED = "confirmed"


class DecisionStatus(StrEnum):
    """Whether the document has an open human translation decision."""

    CLEAR = "clear"
    NEEDS_CONFIRMATION = "needs_confirmation"


class ReviewAcknowledgement(StrEnum):
    """Human acknowledgement used when finalizing one translation revision."""

    PASSED = "passed"
    NOT_RUN_ACKNOWLEDGED = "not_run_acknowledged"
    STALE_ACKNOWLEDGED = "stale_acknowledged"
    FAILED_FINDINGS_DISPOSED = "failed_findings_disposed"
    LEGACY_STATUS_IMPORTED = "legacy_status_imported"


class FindingDisposition(CanonicalModel):
    """Human disposition for one exact immutable review finding."""

    review_id: CanonicalId
    finding_id: CanonicalId
    disposition: Literal["human_accepted"] = "human_accepted"
    reason: str = Field(min_length=1)


class TranslationFinalization(CanonicalModel):
    """Human finalization record for one translation revision."""

    finalized_at: UtcTimestamp
    finalized_by: CanonicalId
    review_acknowledgement: ReviewAcknowledgement
    finding_dispositions: tuple[FindingDisposition, ...]

    @model_validator(mode="after")
    def dispositions_are_unique(self) -> Self:
        """Reject repeated dispositions for the same immutable finding."""
        keys = tuple(
            (item.review_id, item.finding_id) for item in self.finding_dispositions
        )
        if len(keys) != len(set(keys)):
            message = "finding dispositions must be unique"
            raise ValueError(message)
        return self


class TranslationOption(CanonicalModel):
    """One complete answer to an unresolved translation decision."""

    option_id: CanonicalId
    text: str = Field(min_length=1)
    meaning: str = Field(min_length=1)
    risk: str = Field(min_length=1)


class UnresolvedDecision(CanonicalModel):
    """One open decision requiring a human answer."""

    decision_id: CanonicalId
    source_cue_ids: tuple[CanonicalId, ...] = Field(min_length=1)
    target_cue_ids: tuple[CanonicalId, ...] = Field(min_length=1)
    domain: ReviewDomain
    question: str = Field(min_length=1)
    options: tuple[TranslationOption, ...] = Field(min_length=1)
    recommendation_id: CanonicalId

    @model_validator(mode="after")
    def decision_is_structurally_consistent(self) -> Self:
        """Validate option identity and the recommended option reference."""
        option_ids = tuple(option.option_id for option in self.options)
        validate_unique_ids(option_ids, "translation option")
        if self.recommendation_id not in option_ids:
            message = "recommendation_id must reference a translation option"
            raise ValueError(message)
        return self


class TargetCueV2(CanonicalModel):
    """One editable translated subtitle cue."""

    sequence: int = Field(ge=1, strict=True)
    cue_id: CanonicalId
    source_cue_ids: tuple[CanonicalId, ...]
    lineage_status: LineageStatus
    start_ms: CanonicalMilliseconds
    end_ms: PositiveCanonicalMilliseconds
    text: str = Field(min_length=1)
    speakers: tuple[CanonicalId, ...] = Field(min_length=1)
    content_kind: ContentKind
    context_ids: tuple[CanonicalId, ...]
    evidence_ids: tuple[CanonicalId, ...]
    translation_note: str | None = Field(
        default=None,
        min_length=1,
        exclude_if=_is_none,
    )

    @model_validator(mode="after")
    def target_is_structurally_consistent(self) -> Self:
        """Validate timing and the declared source lineage state."""
        if "\\N" in self.text:
            message = "target text must use actual LF line breaks, not ASS separators"
            raise ValueError(message)
        if self.end_ms <= self.start_ms:
            message = "target cue end_ms must be greater than start_ms"
            raise ValueError(message)
        has_lineage = bool(self.source_cue_ids)
        if self.lineage_status is LineageStatus.UNASSIGNED and has_lineage:
            message = "unassigned lineage must not contain source cue IDs"
            raise ValueError(message)
        if self.lineage_status is not LineageStatus.UNASSIGNED and not has_lineage:
            message = "assigned lineage requires source cue IDs"
            raise ValueError(message)
        return self


class TranslationDocumentV2(CanonicalModel):
    """Editable canonical translation revision."""

    schema_version: Literal["translation-document.v2"] = "translation-document.v2"
    document_id: CanonicalId
    revision_id: CanonicalId
    supersedes_revision_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    content_digest: Sha256Digest
    record_digest: Sha256Digest
    source_locale: Literal["ko-KR"] = "ko-KR"
    target_locale: Literal["en-US"] = "en-US"
    source_ref: DocumentReference
    prepared_context_ref: AssetReference
    glossary_ref: AssetReference
    lifecycle_status: DocumentLifecycleStatus
    decision_status: DecisionStatus
    cues: tuple[TargetCueV2, ...]
    unresolved_decisions: tuple[UnresolvedDecision, ...]
    finalization: TranslationFinalization | None = Field(
        default=None,
        exclude_if=_is_none,
    )

    @model_validator(mode="after")
    def translation_is_structurally_consistent(self) -> Self:
        """Validate target identity, decisions, and local lifecycle shape."""
        validate_contiguous_sequences(
            tuple(cue.sequence for cue in self.cues),
            "target cue",
        )
        target_ids = tuple(cue.cue_id for cue in self.cues)
        validate_unique_ids(target_ids, "target cue")
        decision_ids = tuple(item.decision_id for item in self.unresolved_decisions)
        validate_unique_ids(decision_ids, "unresolved decision")
        known_targets = set(target_ids)
        unknown_targets = {
            target_id
            for decision in self.unresolved_decisions
            for target_id in decision.target_cue_ids
            if target_id not in known_targets
        }
        if unknown_targets:
            message = f"decisions reference unknown targets: {unknown_targets}"
            raise ValueError(message)
        has_decisions = bool(self.unresolved_decisions)
        if self.decision_status is DecisionStatus.CLEAR and has_decisions:
            message = "decision_status clear requires no unresolved decisions"
            raise ValueError(message)
        if (
            self.decision_status is DecisionStatus.NEEDS_CONFIRMATION
            and not has_decisions
        ):
            message = "decision_status needs_confirmation requires a decision"
            raise ValueError(message)
        if (
            self.lifecycle_status is DocumentLifecycleStatus.FINALIZED
            and self.finalization is None
        ):
            message = "finalized translation requires finalization"
            raise ValueError(message)
        if (
            self.lifecycle_status is DocumentLifecycleStatus.DRAFT
            and self.finalization is not None
        ):
            message = "draft translation cannot contain finalization"
            raise ValueError(message)
        return self


__all__ = [
    "DecisionStatus",
    "FindingDisposition",
    "LineageStatus",
    "ReviewAcknowledgement",
    "TargetCueV2",
    "TranslationDocumentV2",
    "TranslationFinalization",
    "TranslationOption",
    "UnresolvedDecision",
]
