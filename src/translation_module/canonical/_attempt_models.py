# Copyright 2026 Transcription Module contributors

"""Strict immutable contracts for translation attempt sidecars."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from translation_module.canonical.common import (
    CanonicalId,
    CanonicalModel,
    UtcTimestamp,
)
from translation_module.common import Sha256Digest


class AttemptBindingError(ValueError):
    """An attempt does not bind the supplied candidate graph."""


class AttemptCoverageError(ValueError):
    """Successful rationale coverage is not exact."""


class AttemptLedgerConflictError(ValueError):
    """Append-only attempt identity or lineage conflicts."""


class CorruptAttemptLedgerError(ValueError):
    """Persisted attempt bytes are incomplete or invalid."""


class SelectionBindingError(ValueError):
    """A selection does not bind one successful candidate."""


class SelectionConflictError(ValueError):
    """A translation revision already has a receipt."""


class TranslationAttemptKind(StrEnum):
    """Declared purpose of one translation execution."""

    INITIAL = "initial"
    ALTERNATIVE = "alternative"
    RETRY = "retry"


class TranslationAttemptOutcome(StrEnum):
    """Whether one execution emitted a candidate document."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TranslationAttemptBinding(CanonicalModel):
    """Exact translation dependency graph used by one attempt."""

    translation_document_id: CanonicalId
    translation_revision_id: CanonicalId
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: Sha256Digest
    prepared_context_asset_id: CanonicalId
    prepared_context_revision_id: CanonicalId
    prepared_context_content_digest: Sha256Digest
    glossary_asset_id: CanonicalId
    glossary_revision_id: CanonicalId
    glossary_content_digest: Sha256Digest


class TranslationLineRationale(CanonicalModel):
    """Korean decision rationale bound to one displayed target line."""

    target_cue_id: CanonicalId
    line_number: int = Field(ge=1, strict=True)
    speaker_id: CanonicalId
    line_text_digest: Sha256Digest
    rationale_ko: str = Field(min_length=1)

    @field_validator("rationale_ko")
    @classmethod
    def rationale_is_korean(cls, value: str) -> str:
        """Require at least one Hangul code point in the rationale."""
        if not any("가" <= character <= "힣" for character in value):
            message = "Korean rationale requires Hangul text"
            raise ValueError(message)
        return value


def _is_none(
    value: str | tuple[TranslationLineRationale, ...] | None,
) -> bool:
    return value is None


class TranslationAttempt(CanonicalModel):
    """Immutable result of one translation execution."""

    schema_version: Literal["translation-attempt.v1"] = "translation-attempt.v1"
    attempt_id: CanonicalId
    ordinal: int = Field(ge=1, strict=True)
    parent_attempt_id: CanonicalId | None = Field(default=None, exclude_if=_is_none)
    kind: TranslationAttemptKind
    outcome: TranslationAttemptOutcome
    binding: TranslationAttemptBinding
    candidate_digest: Sha256Digest | None = Field(default=None, exclude_if=_is_none)
    rationales: tuple[TranslationLineRationale, ...] | None = Field(
        default=None, min_length=1, exclude_if=_is_none
    )

    @model_validator(mode="after")
    def outcome_has_exact_artifacts(self) -> Self:
        """Successful attempts carry both artifacts; failures carry neither."""
        has_candidate = self.candidate_digest is not None
        has_rationales = self.rationales is not None
        succeeds = self.outcome is TranslationAttemptOutcome.SUCCEEDED
        if has_candidate != succeeds or has_rationales != succeeds:
            message = "candidate and rationales must both match attempt outcome"
            raise ValueError(message)
        return self


class TranslationSelectionReceipt(CanonicalModel):
    """Immutable authority selecting one candidate for one revision."""

    schema_version: Literal["translation-selection.v1"] = "translation-selection.v1"
    selection_id: CanonicalId
    selected_at: UtcTimestamp
    selected_by: CanonicalId
    translation_document_id: CanonicalId
    translation_revision_id: CanonicalId
    translation_document_digest: Sha256Digest
    attempt_id: CanonicalId
    attempt_candidate_digest: Sha256Digest


class TranslationAttemptLedgerAuthority(CanonicalModel):
    """Trusted byte identity for one store-root attempt ledger prefix."""

    schema_version: Literal["translation-attempt-ledger.v1"] = (
        "translation-attempt-ledger.v1"
    )
    translation_document_id: CanonicalId
    translation_revision_id: CanonicalId
    attempt_count: int = Field(ge=1, strict=True)
    ledger_digest: Sha256Digest
