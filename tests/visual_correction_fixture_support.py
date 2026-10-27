# Copyright 2026 Transcription Module contributors
# ruff: noqa: EM101, EM102, TRY003

"""Strict test-side contracts for the frozen visual-correction fixture."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path  # noqa: TC003 - Pydantic runtime field
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonBlank = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FrozenFixtureModel(BaseModel):
    """Reject drift at fixture parsing boundaries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class VisualRole(StrEnum):
    """Expected transcript-facing role of observed text."""

    SPEECH_SUPPORTING = "speech_supporting"
    NON_SPEECH = "non_speech"
    UNKNOWN = "unknown"


class EvidenceState(StrEnum):
    """Whether the frozen evidence settles the expected decision."""

    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    INSUFFICIENT = "insufficient"


class SemanticRelation(StrEnum):
    """Frozen relation between visible text and audible speech."""

    EXACT = "exact"
    SELECTIVE = "selective"
    CONDENSED = "condensed"
    PARAPHRASE = "paraphrase"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class CorrectionAction(StrEnum):
    """The only four expected visual-correction outcomes."""

    KEEP = "KEEP"
    REPLACE_SPAN = "REPLACE_SPAN"
    INSERT_CUE = "INSERT_CUE"
    HOLD_FOR_CONFIRMATION = "HOLD_FOR_CONFIRMATION"


class MetricPopulationStatus(StrEnum):
    """A denominator is counted or explicitly not applicable."""

    COUNTED = "counted"
    NOT_APPLICABLE = "N/A"


class SourceIdentity(FrozenFixtureModel):
    """Source ownership that distinguishes equal-time utterances."""

    source_cue_ids: tuple[NonBlank, ...] = Field(min_length=1)
    speaker: NonBlank
    turn_id: NonBlank

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> Self:
        if len(self.source_cue_ids) != len(set(self.source_cue_ids)):
            raise ValueError("source cue IDs must be unique")
        return self


class FixtureFrame(FrozenFixtureModel):
    """One stored PTS frame available to generation."""

    frame_id: NonBlank
    pts_ms: NonNegative


class FixtureAsrSegment(FrozenFixtureModel):
    """One stable ASR ordinal and its bounded hypothesis."""

    ordinal: Positive
    start_ms: NonNegative
    end_ms: Positive
    speech: NonBlank

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("ASR end must follow start")
        return self


class FixtureVadInterval(FrozenFixtureModel):
    """One reusable speech-presence interval."""

    interval_id: NonBlank
    start_ms: NonNegative
    end_ms: Positive
    speech_present: bool

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("VAD end must follow start")
        return self


class FixtureSubtitleCue(FrozenFixtureModel):
    """One timed subtitle string retained in the inventory."""

    cue_id: NonBlank
    start_ms: NonNegative
    end_ms: Positive
    text: NonBlank

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("subtitle end must follow start")
        return self


class FixtureSubtitleTrack(FrozenFixtureModel):
    """One exact subtitle variant with immutable bytes."""

    track_id: NonBlank
    variant: NonBlank
    source: Literal["automatic", "manual"]
    sha256: Sha256Digest
    eligible_for_generation: bool
    cues: tuple[FixtureSubtitleCue, ...] = ()

    @model_validator(mode="after")
    def cue_ids_are_unique(self) -> Self:
        cue_ids = tuple(cue.cue_id for cue in self.cues)
        if len(cue_ids) != len(set(cue_ids)):
            raise ValueError("subtitle cue IDs must be unique per track")
        return self


class GenerationInputs(FrozenFixtureModel):
    """Only evidence allowed to influence generation."""

    frames: tuple[FixtureFrame, ...] = Field(min_length=1)
    asr_segments: tuple[FixtureAsrSegment, ...] = Field(min_length=1)
    vad_intervals: tuple[FixtureVadInterval, ...] = Field(min_length=1)
    subtitle_tracks: tuple[FixtureSubtitleTrack, ...] = Field(min_length=1)


class ComparableInterval(FrozenFixtureModel):
    """Reference-only alignment keyed by identity, time, and speaker."""

    interval_id: NonBlank
    source_identity: SourceIdentity
    start_ms: NonNegative
    end_ms: Positive
    ass_event_ids: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("comparable interval end must follow start")
        return self


class ReferenceOnly(FrozenFixtureModel):
    """Ground truth that labels evaluation but cannot enter generation."""

    ass_path: Path
    alignment_basis: Literal["source_identity_time_speaker"]
    comparable_intervals: tuple[ComparableInterval, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reference_contract_is_closed(self) -> Self:
        if self.ass_path.suffix.lower() != ".ass":
            raise ValueError("reference answer path must be ASS")
        interval_ids = tuple(item.interval_id for item in self.comparable_intervals)
        if len(interval_ids) != len(set(interval_ids)):
            raise ValueError("comparable interval IDs must be unique")
        identities_by_time: dict[tuple[int, int], set[SourceIdentity]] = {}
        for item in self.comparable_intervals:
            key = (item.start_ms, item.end_ms)
            identities = identities_by_time.setdefault(key, set())
            if item.source_identity in identities:
                raise ValueError(
                    "equal-time intervals require distinct source identity"
                )
            identities.add(item.source_identity)
        return self


class ExpectedCue(FrozenFixtureModel):
    """One expected final cue projection for a fixture case."""

    cue_id: NonBlank
    source_identity: SourceIdentity
    start_ms: NonNegative
    end_ms: Positive
    speech: NonBlank
    speaker: NonBlank

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("expected cue end must follow start")
        return self


class VisualCorrectionCase(FrozenFixtureModel):
    """One frozen positive or abstention behavior."""

    case_id: NonBlank
    source_identity: SourceIdentity
    frame_ids: tuple[NonBlank, ...] = ()
    asr_ordinals: tuple[Positive, ...] = ()
    vad_interval_ids: tuple[NonBlank, ...] = ()
    subtitle_track_ids: tuple[NonBlank, ...] = ()
    comparable_interval_ids: tuple[NonBlank, ...] = Field(min_length=1)
    expected_role: VisualRole
    expected_state: EvidenceState
    semantic_relation: SemanticRelation
    expected_action: CorrectionAction
    expected_final_cues: tuple[ExpectedCue, ...] = Field(min_length=1)
    expected_reason_codes: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        for values, label in (
            (self.frame_ids, "frame"),
            (self.asr_ordinals, "ASR ordinal"),
            (self.vad_interval_ids, "VAD interval"),
            (self.subtitle_track_ids, "subtitle track"),
            (self.comparable_interval_ids, "comparable interval"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} reference")
        return self


class MetricPopulation(FrozenFixtureModel):
    """Frozen numerator and denominator membership for one metric."""

    metric_id: NonBlank
    case_ids: tuple[NonBlank, ...]
    numerator_case_ids: tuple[NonBlank, ...]
    expected_denominator: NonNegative
    expected_numerator: NonNegative
    status: MetricPopulationStatus

    @model_validator(mode="after")
    def counts_and_status_match_membership(self) -> Self:
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("metric denominator case IDs must be unique")
        if len(self.numerator_case_ids) != len(set(self.numerator_case_ids)):
            raise ValueError("metric numerator case IDs must be unique")
        if not set(self.numerator_case_ids) <= set(self.case_ids):
            raise ValueError("metric numerator must be a denominator subset")
        if self.expected_denominator != len(self.case_ids):
            raise ValueError("expected denominator does not match case membership")
        if self.expected_numerator != len(self.numerator_case_ids):
            raise ValueError("expected numerator does not match case membership")
        expected_status = (
            MetricPopulationStatus.NOT_APPLICABLE
            if self.expected_denominator == 0
            else MetricPopulationStatus.COUNTED
        )
        if self.status is not expected_status:
            raise ValueError("metric status does not match denominator")
        return self


class VisualCorrectionFixture(FrozenFixtureModel):
    """Versioned, reference-safe benchmark input and expected outcomes."""

    schema_version: Literal["visual-correction-regressions.v1"]
    generation_inputs: GenerationInputs
    reference_only: ReferenceOnly
    cases: tuple[VisualCorrectionCase, ...] = Field(min_length=1)
    metric_populations: tuple[MetricPopulation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def fixture_references_are_closed(self) -> Self:
        """Require every case and metric reference to resolve exactly once."""
        frame_ids = {item.frame_id for item in self.generation_inputs.frames}
        asr_ordinals = {item.ordinal for item in self.generation_inputs.asr_segments}
        vad_ids = {item.interval_id for item in self.generation_inputs.vad_intervals}
        track_ids = {item.track_id for item in self.generation_inputs.subtitle_tracks}
        interval_ids = {
            item.interval_id for item in self.reference_only.comparable_intervals
        }
        case_ids = tuple(case.case_id for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("fixture case IDs must be unique")
        for case in self.cases:
            if not set(case.frame_ids) <= frame_ids:
                raise ValueError(f"case {case.case_id} references unknown frame")
            if not set(case.asr_ordinals) <= asr_ordinals:
                raise ValueError(f"case {case.case_id} references unknown ASR ordinal")
            if not set(case.vad_interval_ids) <= vad_ids:
                raise ValueError(f"case {case.case_id} references unknown VAD interval")
            if not set(case.subtitle_track_ids) <= track_ids:
                raise ValueError(
                    f"case {case.case_id} references unknown subtitle track"
                )
            if not set(case.comparable_interval_ids) <= interval_ids:
                raise ValueError(
                    f"case {case.case_id} references unknown comparable interval"
                )
        metric_ids = tuple(metric.metric_id for metric in self.metric_populations)
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metric IDs must be unique")
        known_cases = set(case_ids)
        if any(
            not set(metric.case_ids) <= known_cases
            for metric in self.metric_populations
        ):
            raise ValueError("metric population references unknown case")
        return self


def load_visual_correction_fixture(path: Path) -> VisualCorrectionFixture:
    """Parse one frozen fixture through the strict versioned boundary."""
    return VisualCorrectionFixture.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "MetricPopulationStatus",
    "VisualCorrectionFixture",
    "load_visual_correction_fixture",
]
