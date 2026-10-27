# Copyright 2026 Transcription Module contributors

"""Standalone source transcript and evidence exchange contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from translation_module.common import (
    ContentKind,
    EpisodeContext,
    EvidenceId,
    FrozenModel,
    NonNegativeMilliseconds,
    PositiveMilliseconds,
    Sha256Digest,
    SourceCueId,
    SourceLocale,
)


class SourceMaturity(StrEnum):
    """Publication maturity declared by the source owner."""

    DRAFT = "draft"
    FINALIZED = "finalized"


class EvidenceKind(StrEnum):
    """External evidence resource category."""

    FRAME = "frame"
    IMAGE = "image"
    TEXT = "text"


class EvidenceEntry(FrozenModel):
    """One durable evidence resource resolved outside run storage."""

    evidence_id: EvidenceId
    kind: EvidenceKind = Field(strict=True)
    media_type: str = Field(min_length=1)
    resource_digest: Sha256Digest
    locator: str = Field(min_length=1)
    pts_ms: NonNegativeMilliseconds | None = None

    @model_validator(mode="after")
    def frame_has_timestamp(self) -> Self:
        """Require source PTS for frame evidence."""
        match self.kind:
            case EvidenceKind.FRAME:
                if self.pts_ms is None:
                    message = "frame evidence requires pts_ms"
                    raise ValueError(message)
            case EvidenceKind.IMAGE | EvidenceKind.TEXT:
                pass
        return self


class EvidenceManifest(FrozenModel):
    """Versioned locator table for external evidence."""

    schema_version: Literal["translation-evidence.v1"] = "translation-evidence.v1"
    base_uri: str = Field(min_length=1)
    entries: tuple[EvidenceEntry, ...] = ()


def _is_none(value: object) -> bool:
    return value is None


class SourceCue(FrozenModel):
    """One independently identifiable source transcript cue."""

    sequence: int = Field(ge=1, strict=True)
    cue_id: SourceCueId
    start_ms: NonNegativeMilliseconds
    end_ms: PositiveMilliseconds
    text: str = Field(min_length=1)
    speakers: tuple[str, ...] = Field(min_length=1)
    content_kind: ContentKind = Field(strict=True)
    context_note: str | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    screen_text: str | None = None
    evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed half-open intervals."""
        if self.end_ms <= self.start_ms:
            message = "end_ms must be greater than start_ms"
            raise ValueError(message)
        return self


class SourceSnapshot(FrozenModel):
    """Standalone source exchange artifact accepted for translation."""

    schema_version: Literal["source-transcript.v1"] = "source-transcript.v1"
    document_id: str = Field(min_length=1)
    content_digest: Sha256Digest
    maturity: SourceMaturity = Field(strict=True)
    source_locale: SourceLocale
    duration_ms: PositiveMilliseconds
    context: EpisodeContext
    context_evidence_ids: tuple[EvidenceId, ...] = ()
    evidence: EvidenceManifest
    cues: tuple[SourceCue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def snapshot_is_internally_consistent(self) -> Self:
        """Enforce source sequence, identity, timing and evidence contracts."""
        sequences = tuple(cue.sequence for cue in self.cues)
        if sequences != tuple(range(1, len(self.cues) + 1)):
            message = "source cue sequences must be contiguous"
            raise ValueError(message)
        cue_ids = tuple(cue.cue_id for cue in self.cues)
        if len(cue_ids) != len(set(cue_ids)):
            message = "source cue IDs must be unique"
            raise ValueError(message)
        timing_order = tuple((cue.start_ms, cue.end_ms) for cue in self.cues)
        if timing_order != tuple(sorted(timing_order)):
            message = "source cues must follow timing order"
            raise ValueError(message)
        if any(cue.end_ms > self.duration_ms for cue in self.cues):
            message = "source cue end_ms exceeds duration_ms"
            raise ValueError(message)
        evidence_ids = tuple(entry.evidence_id for entry in self.evidence.entries)
        if len(evidence_ids) != len(set(evidence_ids)):
            message = "evidence IDs must be unique"
            raise ValueError(message)
        known_evidence = set(evidence_ids)
        if len(self.context_evidence_ids) != len(set(self.context_evidence_ids)):
            message = "source context evidence IDs must be unique"
            raise ValueError(message)
        unknown_context = set(self.context_evidence_ids) - known_evidence
        if unknown_context:
            message = f"source context references unknown evidence: {unknown_context}"
            raise ValueError(message)
        unknown = {
            evidence_id
            for cue in self.cues
            for evidence_id in cue.evidence_ids
            if evidence_id not in known_evidence
        }
        if unknown:
            message = f"source cues reference unknown evidence: {unknown}"
            raise ValueError(message)
        if any(
            entry.pts_ms is not None and entry.pts_ms >= self.duration_ms
            for entry in self.evidence.entries
        ):
            message = "evidence pts_ms must be inside source duration"
            raise ValueError(message)
        return self
