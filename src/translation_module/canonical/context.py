# Copyright 2026 Transcription Module contributors

"""Canonical content context asset contract."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    AssetLifecycleStatus,
    CanonicalEvidenceManifest,
    CanonicalId,
    CanonicalModel,
    DocumentReference,
    validate_evidence_references,
    validate_unique_ids,
)
from translation_module.common import Sha256Digest


def _is_none(value: object) -> bool:
    return value is None


class ContextStage(StrEnum):
    """Where a context revision sits in the artifact dependency graph."""

    INPUT = "input"
    PREPARED = "prepared"


class ContextDecisionStatus(StrEnum):
    """Authority level for one context fact."""

    VERIFIED = "verified"
    HUMAN_CONFIRMED = "human_confirmed"
    UNRESOLVED = "unresolved"


class ContextParticipant(CanonicalModel):
    """One person or group relevant to an episode."""

    participant_id: CanonicalId
    name_ko: str = Field(min_length=1)
    name_en: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    aliases: tuple[CanonicalId, ...]
    role: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    evidence_ids: tuple[CanonicalId, ...]

    @model_validator(mode="after")
    def aliases_are_unique(self) -> Self:
        """Reject ambiguous duplicate participant aliases."""
        if len(self.aliases) != len(set(self.aliases)):
            message = "participant aliases must be unique"
            raise ValueError(message)
        return self


class ContextFact(CanonicalModel):
    """One scoped episode fact used by transcription or translation."""

    fact_id: CanonicalId
    statement: str = Field(min_length=1)
    participant_ids: tuple[CanonicalId, ...]
    scope_source_cue_ids: tuple[CanonicalId, ...]
    evidence_ids: tuple[CanonicalId, ...]
    decision_status: ContextDecisionStatus
    blocking: bool

    @model_validator(mode="after")
    def scoped_ids_are_unique(self) -> Self:
        """Reject duplicate participant and source scope references."""
        if len(self.participant_ids) != len(set(self.participant_ids)):
            message = "fact participant IDs must be unique"
            raise ValueError(message)
        if len(self.scope_source_cue_ids) != len(set(self.scope_source_cue_ids)):
            message = "fact source scope IDs must be unique"
            raise ValueError(message)
        return self


class ContentContextAsset(CanonicalModel):
    """Versioned input or prepared episode context revision."""

    schema_version: Literal["content-context.v1"] = "content-context.v1"
    asset_id: CanonicalId
    revision_id: CanonicalId
    supersedes_revision_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    content_digest: Sha256Digest
    record_digest: Sha256Digest
    stage: ContextStage
    lifecycle_status: AssetLifecycleStatus
    prepared_from_source_ref: DocumentReference | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    program: str = Field(min_length=1)
    episode_summary: str = Field(min_length=1)
    evidence: CanonicalEvidenceManifest
    participants: tuple[ContextParticipant, ...]
    facts: tuple[ContextFact, ...]

    @model_validator(mode="after")
    def context_is_structurally_consistent(self) -> Self:
        """Validate stage dependency, stable IDs, and local references."""
        if (
            self.stage is ContextStage.INPUT
            and self.prepared_from_source_ref is not None
        ):
            message = "input context cannot contain prepared_from_source_ref"
            raise ValueError(message)
        if (
            self.stage is ContextStage.PREPARED
            and self.prepared_from_source_ref is None
        ):
            message = "prepared context requires prepared_from_source_ref"
            raise ValueError(message)

        participant_ids = tuple(item.participant_id for item in self.participants)
        fact_ids = tuple(item.fact_id for item in self.facts)
        validate_unique_ids(participant_ids, "participant")
        validate_unique_ids(fact_ids, "fact")
        known_participants = set(participant_ids)
        unknown_participants = {
            participant_id
            for fact in self.facts
            for participant_id in fact.participant_ids
            if participant_id not in known_participants
        }
        if unknown_participants:
            message = f"facts reference unknown participants: {unknown_participants}"
            raise ValueError(message)
        validate_evidence_references(
            tuple(
                evidence_id
                for participant in self.participants
                for evidence_id in participant.evidence_ids
            )
            + tuple(
                evidence_id
                for fact in self.facts
                for evidence_id in fact.evidence_ids
            ),
            self.evidence,
            "context",
        )
        return self


__all__ = [
    "ContentContextAsset",
    "ContextDecisionStatus",
    "ContextFact",
    "ContextParticipant",
    "ContextStage",
]
