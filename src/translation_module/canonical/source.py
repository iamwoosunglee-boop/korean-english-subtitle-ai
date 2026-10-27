# Copyright 2026 Transcription Module contributors

"""Canonical source transcript v2 contract."""

from typing import Literal, Self

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    AssetReference,
    CanonicalEvidenceManifest,
    CanonicalId,
    CanonicalMilliseconds,
    CanonicalModel,
    ContentKind,
    DocumentLifecycleStatus,
    PositiveCanonicalMilliseconds,
    UtcTimestamp,
    validate_contiguous_sequences,
    validate_evidence_references,
    validate_unique_ids,
)
from translation_module.common import Sha256Digest, SourceLocale


def _is_none(value: object) -> bool:
    return value is None


class SourceFinalization(CanonicalModel):
    """Human finalization record for one source revision."""

    finalized_at: UtcTimestamp
    finalized_by: CanonicalId


class SourceCueV2(CanonicalModel):
    """One stable source subtitle cue."""

    sequence: int = Field(ge=1, strict=True)
    cue_id: CanonicalId
    start_ms: CanonicalMilliseconds
    end_ms: PositiveCanonicalMilliseconds
    text: str = Field(min_length=1)
    speakers: tuple[CanonicalId, ...] = Field(min_length=1)
    content_kind: ContentKind
    context_note: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    screen_text: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    evidence_ids: tuple[CanonicalId, ...]

    @model_validator(mode="after")
    def interval_is_positive(self) -> Self:
        """Reject empty or reversed source intervals."""
        if self.end_ms <= self.start_ms:
            message = "source cue end_ms must be greater than start_ms"
            raise ValueError(message)
        utterance_count = len(self.text.replace("\\N", "\n").splitlines())
        if utterance_count > 1 and len(self.speakers) != utterance_count:
            message = "source cue speaker count must equal utterance line count"
            raise ValueError(message)
        return self


class SourceTranscriptV2(CanonicalModel):
    """Editable canonical source transcript revision."""

    schema_version: Literal["source-transcript.v2"] = "source-transcript.v2"
    document_id: CanonicalId
    revision_id: CanonicalId
    supersedes_revision_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    content_digest: Sha256Digest
    record_digest: Sha256Digest
    lifecycle_status: DocumentLifecycleStatus
    source_locale: SourceLocale
    duration_ms: PositiveCanonicalMilliseconds
    input_context_ref: AssetReference
    evidence: CanonicalEvidenceManifest
    cues: tuple[SourceCueV2, ...]
    finalization: SourceFinalization | None = Field(
        default=None,
        exclude_if=_is_none,
    )

    @model_validator(mode="after")
    def source_is_structurally_consistent(self) -> Self:
        """Validate local source identity, timing, evidence, and lifecycle."""
        validate_contiguous_sequences(
            tuple(cue.sequence for cue in self.cues),
            "source cue",
        )
        validate_unique_ids(
            tuple(cue.cue_id for cue in self.cues),
            "source cue",
        )
        timing = tuple((cue.start_ms, cue.end_ms) for cue in self.cues)
        if timing != tuple(sorted(timing)):
            message = "source cues must follow timing order"
            raise ValueError(message)
        if any(cue.end_ms > self.duration_ms for cue in self.cues):
            message = "source cue end_ms exceeds duration_ms"
            raise ValueError(message)
        validate_evidence_references(
            tuple(evidence_id for cue in self.cues for evidence_id in cue.evidence_ids),
            self.evidence,
            "source cues",
        )
        if any(
            entry.pts_ms is not None and entry.pts_ms >= self.duration_ms
            for entry in self.evidence.entries
        ):
            message = "evidence pts_ms must be inside source duration"
            raise ValueError(message)
        if (
            self.lifecycle_status is DocumentLifecycleStatus.FINALIZED
            and self.finalization is None
        ):
            message = "finalized source requires finalization"
            raise ValueError(message)
        if (
            self.lifecycle_status is DocumentLifecycleStatus.DRAFT
            and self.finalization is not None
        ):
            message = "draft source cannot contain finalization"
            raise ValueError(message)
        return self


def unresolved_speaker_cue_ids(
    source: SourceTranscriptV2,
) -> tuple[CanonicalId, ...]:
    """Return cues that still carry the reserved unresolved speaker marker."""
    return tuple(
        cue.cue_id
        for cue in source.cues
        if any(speaker.casefold() == "unknown" for speaker in cue.speakers)
    )


__all__ = [
    "SourceCueV2",
    "SourceFinalization",
    "SourceTranscriptV2",
    "unresolved_speaker_cue_ids",
]
