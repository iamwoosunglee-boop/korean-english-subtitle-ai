# Copyright 2026 Transcription Module contributors

"""Immutable records proving one consumer explicitly opened one frame."""

from enum import StrEnum
from typing import ClassVar, Literal, Self, final, override

from pydantic import Field, model_validator

from translation_module.canonical.common import CanonicalId, CanonicalModel
from translation_module.canonical.digest import canonical_sha256
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.common import Sha256Digest


class EvidenceActorRole(StrEnum):
    """The workflow role that consumed frame bytes."""

    TRANSLATOR = "translator"
    REVIEWER = "reviewer"


class EvidenceOpenContext(CanonicalModel):
    """Canonical source and exact candidate content being consumed."""

    source: SourceTranscriptV2
    consumer_content_digest: Sha256Digest


class EvidenceOpenRequest(CanonicalModel):
    """Typed boundary for one explicit frame-open action."""

    evidence_id: CanonicalId
    actor_role: EvidenceActorRole
    consumer_id: CanonicalId
    consumer_content_digest: Sha256Digest


class EvidenceOpenRecord(CanonicalModel):
    """One immutable, digest-bound explicit frame-open proof."""

    schema_version: Literal["evidence-open.v1"] = "evidence-open.v1"
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: Sha256Digest
    evidence_id: CanonicalId
    locator: str = Field(min_length=1)
    declared_resource_digest: Sha256Digest
    observed_resource_digest: Sha256Digest
    actor_role: EvidenceActorRole
    consumer_id: CanonicalId
    consumer_content_digest: Sha256Digest
    record_digest: Sha256Digest

    @model_validator(mode="after")
    def declared_and_observed_digests_match(self) -> Self:
        """Reject records that do not prove the declared frame bytes."""
        if self.declared_resource_digest != self.observed_resource_digest:
            message = "declared and observed evidence digests differ"
            raise ValueError(message)
        return self


def bind_evidence_open_digest(record: EvidenceOpenRecord) -> EvidenceOpenRecord:
    """Bind one evidence-open record to its complete immutable payload."""
    payload = record.model_dump(mode="json", exclude={"record_digest"})
    return record.model_copy(update={"record_digest": canonical_sha256(payload)})


def verify_evidence_open_digest(record: EvidenceOpenRecord) -> None:
    """Reject a record whose stored digest does not match its payload."""
    payload = record.model_dump(mode="json", exclude={"record_digest"})
    if record.record_digest != canonical_sha256(payload):
        raise EvidenceOpenRecordError(
            evidence_id=record.evidence_id,
            reason="record digest mismatch",
        )


@final
class EvidenceOpenError(Exception):
    """An explicit frame-open action could not produce valid proof."""

    __slots__: ClassVar[tuple[str, ...]] = ("evidence_id", "reason")
    evidence_id: str
    reason: str

    def __init__(self, evidence_id: str, reason: str) -> None:
        """Retain typed evidence identity and failure reason."""
        self.evidence_id = evidence_id
        self.reason = reason
        super().__init__(evidence_id, reason)

    @override
    def __str__(self) -> str:
        """Render stable evidence identity and failure reason."""
        return f"evidence {self.evidence_id}: {self.reason}"


@final
class EvidenceOpenRecordError(Exception):
    """Persisted evidence-open proof is absent, stale, or corrupt."""

    __slots__: ClassVar[tuple[str, ...]] = ("evidence_id", "reason")
    evidence_id: str
    reason: str

    def __init__(self, evidence_id: str, reason: str) -> None:
        """Retain typed evidence identity and failure reason."""
        self.evidence_id = evidence_id
        self.reason = reason
        super().__init__(evidence_id, reason)

    @override
    def __str__(self) -> str:
        """Render stable record identity and failure reason."""
        return f"evidence-open record {self.evidence_id}: {self.reason}"


__all__ = [
    "EvidenceActorRole",
    "EvidenceOpenContext",
    "EvidenceOpenError",
    "EvidenceOpenRecord",
    "EvidenceOpenRecordError",
    "EvidenceOpenRequest",
    "bind_evidence_open_digest",
    "verify_evidence_open_digest",
]
