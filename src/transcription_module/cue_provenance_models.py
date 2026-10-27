# Copyright 2026 Transcription Module contributors

"""Typed cue identity, provenance, and validation input contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.correction_receipts import (  # noqa: TC001
    CorrectionReceiptSet,
)
from transcription_module.models import CueId, FrozenModel, RunId, Transcript
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.visual_observations import (  # noqa: TC001
    VisualObservationSet,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
PositiveOrdinal = Annotated[int, Field(ge=1)]


class CueIdentityInput(FrozenModel):
    """Exact ingredients used to preserve or derive one cue identity."""

    era: EvidenceEra
    existing_cue_id: CueId | None = None
    source_evidence_ids: tuple[NonBlank, ...] = Field(min_length=1)
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    speech: str

    @model_validator(mode="after")
    def identity_input_is_ordered_and_unique(self) -> Self:
        """Reject invalid intervals and repeated source evidence identities."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "cue identity end must follow start"
            raise ValueError(message)
        if len(self.source_evidence_ids) != len(set(self.source_evidence_ids)):
            message = "cue identity source evidence IDs must be unique"
            raise ValueError(message)
        return self


class CueProvenanceRecord(FrozenModel):
    """One cue mapped to exact source, evidence, correction, and speech bytes."""

    schema_version: Literal["cue-provenance.v1"] = "cue-provenance.v1"
    cue_id: CueId
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    source_digest: Sha256Digest
    source_evidence_ids: tuple[NonBlank, ...] = Field(min_length=1)
    asr_ordinals: tuple[PositiveOrdinal, ...] = ()
    subtitle_track_ids: tuple[NonBlank, ...] = ()
    subtitle_cue_ids: tuple[NonBlank, ...] = ()
    observation_ids: tuple[NonBlank, ...] = ()
    correction_receipt_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    speech_digest: Sha256Digest
    speaker: str | None = None
    existing_cue_preserved: bool

    @model_validator(mode="after")
    def provenance_fields_are_consistent(self) -> Self:
        """Require current policy identity and unique exact references."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "cue provenance end must follow start"
            raise ValueError(message)
        for values, label in (
            (self.source_evidence_ids, "source evidence IDs"),
            (self.asr_ordinals, "ASR ordinals"),
            (self.subtitle_track_ids, "subtitle track IDs"),
            (self.subtitle_cue_ids, "subtitle cue IDs"),
            (self.observation_ids, "observation IDs"),
        ):
            if len(values) != len(set(values)):
                message = f"cue provenance {label} must be unique"
                raise ValueError(message)
        if self.era is EvidenceEra.POLICY_BOUND and self.policy_digest is None:
            message = "policy-bound cue provenance requires policy digest"
            raise ValueError(message)
        return self


class CueProvenanceSet(FrozenModel):
    """Complete cue-order provenance for one run and evidence era."""

    schema_version: Literal["cue-provenance-set.v1"] = "cue-provenance-set.v1"
    run_id: RunId
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    records: tuple[CueProvenanceRecord, ...]

    @model_validator(mode="after")
    def record_bindings_match_set(self) -> Self:
        """Reject duplicate cue IDs, mixed eras, and foreign policy records."""
        cue_ids = tuple(record.cue_id for record in self.records)
        if len(cue_ids) != len(set(cue_ids)):
            message = "cue provenance IDs must be unique"
            raise ValueError(message)
        if any(record.era is not self.era for record in self.records):
            message = "cue provenance set cannot mix evidence eras"
            raise ValueError(message)
        if self.era is EvidenceEra.POLICY_BOUND:
            if self.policy_digest is None:
                message = "policy-bound cue provenance set requires policy digest"
                raise ValueError(message)
            if any(
                record.policy_digest != self.policy_digest for record in self.records
            ):
                message = "cue provenance record has a foreign policy digest"
                raise ValueError(message)
        return self


class CueProvenanceValidationInput(FrozenModel):
    """Complete current authority required to verify every cue trace."""

    transcript: Transcript
    provenance: CueProvenanceSet
    corrections: CorrectionReceiptSet
    observations: VisualObservationSet
    source_digest: Sha256Digest
    policy_digest: Sha256Digest | None = None


__all__ = [
    "CueIdentityInput",
    "CueProvenanceRecord",
    "CueProvenanceSet",
    "CueProvenanceValidationInput",
]
