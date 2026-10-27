# Copyright 2026 Transcription Module contributors

"""Typed inputs and receipts for same-video paired diagnostics."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self, cast
from uuid import UUID  # noqa: TC003 - Pydantic runtime field

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[int, Field(ge=0)]


class DiagnosticModel(BaseModel):
    """Reject unknown or mutable diagnostic data at file boundaries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class Contamination(StrEnum):
    """Whether an evaluation arm is policy-clean."""

    CONTAMINATED = "contaminated"
    POLICY_CLEAN = "policy_clean"


class BaselineRun(DiagnosticModel):
    """The preserved contaminated pre-change comparison arm."""

    run_id: UUID
    contamination: Literal[Contamination.CONTAMINATED]
    label: Literal["derived-assisted contaminated pre-change baseline"]


class CandidateRun(DiagnosticModel):
    """The same-video policy-clean comparison arm."""

    run_id: UUID
    contamination: Literal[Contamination.POLICY_CLEAN]
    label: Literal["policy-clean paired diagnostic"]


class AlignmentClaim(DiagnosticModel):
    """One candidate claim aligned by time, source identity, and ownership."""

    case_id: NonBlank
    interval_id: NonBlank
    source_cue_ids: tuple[NonBlank, ...] = Field(min_length=1)
    speakers: tuple[NonBlank, ...] = Field(min_length=1)
    turn_id: NonBlank
    start_ms: NonNegative
    end_ms: Annotated[int, Field(gt=0)]

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_speaker(cls, value: object) -> object:
        """Read historical single/pipe speaker fixtures as ordered arrays."""
        if not isinstance(value, dict):
            return value
        mapping = cast("dict[str, object]", value)
        if "speakers" in mapping:
            return mapping
        speaker = mapping.get("speaker")
        if not isinstance(speaker, str):
            return mapping
        migrated = dict(mapping)
        del migrated["speaker"]
        migrated["speakers"] = tuple(speaker.split("|"))
        return migrated

    @model_validator(mode="after")
    def interval_is_valid(self) -> Self:
        """Reject inverted intervals and repeated source identities."""
        if self.end_ms <= self.start_ms:
            message = "alignment end must follow start"
            raise ValueError(message)
        if len(self.source_cue_ids) != len(set(self.source_cue_ids)):
            message = "alignment source cue IDs must be unique"
            raise ValueError(message)
        if len(self.speakers) != len(set(self.speakers)):
            message = "alignment simultaneous speakers must be unique"
            raise ValueError(message)
        return self


class CharacterTrace(DiagnosticModel):
    """One changed character and its exact candidate-value provenance."""

    case_id: NonBlank
    character_id: NonBlank
    value: Annotated[str, StringConstraints(min_length=1, max_length=1)]
    source_evidence_id: NonBlank | None
    source_value: NonBlank | None

    @model_validator(mode="after")
    def trace_is_supported_or_explicitly_missing(self) -> Self:
        """Require complete source fields and exact character support."""
        both_present = (
            self.source_evidence_id is not None and self.source_value is not None
        )
        both_missing = (
            self.source_evidence_id is None and self.source_value is None
        )
        if not (both_present or both_missing):
            message = "character trace source fields must be present together"
            raise ValueError(message)
        if (
            self.source_value is not None
            and self.source_evidence_id is not None
            and self.value not in self.source_value
        ):
            message = "changed character is absent from its source value"
            raise ValueError(message)
        return self


class DiagnosticOutcomes(DiagnosticModel):
    """Frozen case outcomes; no cue-count projection is accepted."""

    correct_meaning_case_ids: tuple[NonBlank, ...]
    mandatory_missing_before_case_ids: tuple[NonBlank, ...]
    mandatory_missing_after_case_ids: tuple[NonBlank, ...]
    auto_corrected_case_ids: tuple[NonBlank, ...]
    auto_inserted_case_ids: tuple[NonBlank, ...]
    changed_character_count: NonNegative
    correction_trace: tuple[CharacterTrace, ...]

    @model_validator(mode="after")
    def memberships_and_trace_are_closed(self) -> Self:
        """Reject duplicate memberships and incomplete trace ledgers."""
        memberships = (
            self.correct_meaning_case_ids,
            self.mandatory_missing_before_case_ids,
            self.mandatory_missing_after_case_ids,
            self.auto_corrected_case_ids,
            self.auto_inserted_case_ids,
        )
        if any(len(values) != len(set(values)) for values in memberships):
            message = "diagnostic outcome case IDs must be unique"
            raise ValueError(message)
        if not set(self.mandatory_missing_after_case_ids) <= set(
            self.mandatory_missing_before_case_ids
        ):
            message = "post-policy omissions must be a pre-policy subset"
            raise ValueError(message)
        character_ids = tuple(item.character_id for item in self.correction_trace)
        if len(character_ids) != len(set(character_ids)):
            message = "changed character IDs must be unique"
            raise ValueError(message)
        if self.changed_character_count != len(character_ids):
            message = "changed character count must match trace audit rows"
            raise ValueError(message)
        return self


class SafetyCounts(DiagnosticModel):
    """Absolute safety failures; every accepted value must be zero."""

    disallowed_track_contribution: NonNegative
    non_speech_unknown_elevation: NonNegative
    user_confirmed_overwrite: NonNegative
    critical_wrong_owner: NonNegative
    correct_to_wrong_auto_damage: NonNegative
    unsupported_additions_before: NonNegative = 0
    unsupported_additions_after: NonNegative = 0


class PairedDiagnosticInput(DiagnosticModel):
    """Typed evaluator input with no ASS or raw cue-count generation seam."""

    schema_version: Literal["paired-diagnostic-input.v1"]
    evaluation_kind: Literal["same_video_paired_diagnostic"]
    comparison_method: Literal["comparable_intervals"]
    alignment_basis: Literal["source_identity_time_speaker"]
    baseline_run: BaselineRun
    candidate_run: CandidateRun
    policy_digest: Sha256Digest
    alignment_claims: tuple[AlignmentClaim, ...]
    outcomes: DiagnosticOutcomes
    safety_counts: SafetyCounts


class MetricId(StrEnum):
    """Approved effectiveness metrics."""

    MEANING_ACCURACY = "meaning_accuracy"
    MANDATORY_OMISSION = "mandatory_omission"
    VISUAL_CORRECTION = "visual_correction"
    VISUAL_GAP_INSERTION = "visual_gap_insertion"
    MANDATORY_OMISSION_REDUCTION = "mandatory_omission_reduction"


class MetricStatus(StrEnum):
    """A metric is counted or explicitly not applicable."""

    COUNTED = "counted"
    NOT_APPLICABLE = "N/A"


class ThresholdRelation(StrEnum):
    """Direction used to apply an integer threshold."""

    AT_LEAST = "at_least"
    AT_MOST = "at_most"


class OverallStatus(StrEnum):
    """Whole-receipt acceptance state."""

    PASS = "pass"  # noqa: S105 - acceptance status, not a secret
    FAIL = "fail"


class AlignmentAudit(DiagnosticModel):
    """Explicit comparable-interval inclusion and exclusion counts."""

    reference_cases: NonNegative
    reference_intervals: NonNegative
    input_rows: NonNegative
    population_complete: bool = True
    aligned_cases: NonNegative
    aligned_intervals: NonNegative
    ambiguous_rows: NonNegative
    tied_rows: NonNegative
    unmatched_rows: NonNegative
    excluded_cases: NonNegative
    unknown_cases: NonNegative
    unmatched_reference_intervals: NonNegative


class MetricReceipt(DiagnosticModel):
    """One exact fraction and its approved integer threshold."""

    metric_id: MetricId
    numerator: NonNegative
    denominator: NonNegative
    rate: NonBlank
    status: MetricStatus
    threshold_relation: ThresholdRelation
    threshold_count: NonNegative
    passed: bool


class CorrectionTraceAudit(DiagnosticModel):
    """Exact changed-character traceability gate."""

    numerator: NonNegative
    denominator: NonNegative
    rate: NonBlank
    required_rate: Literal["100%"] = "100%"
    untraced_character_ids: tuple[NonBlank, ...]
    passed: bool


class SafetyAudit(DiagnosticModel):
    """Zero-only safety gate receipt."""

    threshold: Literal[0] = 0
    counts: SafetyCounts
    passed: bool


class Sha256Manifest(DiagnosticModel):
    """Hashes binding both inputs and the semantic receipt payload."""

    fixture_sha256: Sha256Digest
    diagnostic_input_sha256: Sha256Digest
    receipt_payload_sha256: Sha256Digest


class AcceptanceReceipt(DiagnosticModel):
    """Deterministic same-video acceptance receipt."""

    schema_version: Literal["paired-diagnostic-receipt.v1"]
    evaluation_kind: Literal["policy-clean paired diagnostic"]
    comparison_method: Literal["comparable_intervals"]
    alignment_basis: Literal["source_identity_time_speaker"]
    scope: Literal["same_video_only"]
    generalization_allowed: Literal[False]
    baseline_run: BaselineRun
    candidate_run: CandidateRun
    policy_digest: Sha256Digest
    alignment: AlignmentAudit
    metrics: tuple[MetricReceipt, ...]
    safety: SafetyAudit
    correction_trace: CorrectionTraceAudit
    overall_status: OverallStatus
    manifest: Sha256Manifest


__all__ = [
    "AcceptanceReceipt",
    "AlignmentAudit",
    "AlignmentClaim",
    "BaselineRun",
    "CandidateRun",
    "CharacterTrace",
    "CorrectionTraceAudit",
    "DiagnosticModel",
    "DiagnosticOutcomes",
    "MetricId",
    "MetricReceipt",
    "MetricStatus",
    "NonBlank",
    "NonNegative",
    "OverallStatus",
    "PairedDiagnosticInput",
    "SafetyAudit",
    "SafetyCounts",
    "Sha256Digest",
    "Sha256Manifest",
    "ThresholdRelation",
]
