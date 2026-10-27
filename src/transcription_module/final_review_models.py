# Copyright 2026 Transcription Module contributors

"""Immutable boundary contracts for transcript-wide final review."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, InstanceOf, model_validator

from transcription_module.models import (
    CueId,
    EvidenceId,
    FrameId,
    FrozenModel,
    Milliseconds,
    PositiveMilliseconds,
    RunId,
    Uncertainty,
)
from transcription_module.speaker_notation import validate_speaker_notation

Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
StableRunId = Annotated[RunId, Field(min_length=1)]
StableCueId = Annotated[CueId, Field(min_length=1)]
StableEvidenceId = Annotated[EvidenceId, Field(min_length=1)]
StableFrameId = Annotated[FrameId, Field(min_length=1)]
StableSegmentId = Annotated[str, Field(min_length=1)]
VisibleScreenText = Annotated[str, Field(min_length=1)]


class ReviewSegmentAssignment(FrozenModel):
    """One ordered, time-bounded portion of the assigned evidence universe."""

    segment_id: StableSegmentId
    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)
    frame_ids: tuple[StableFrameId, ...] = ()
    cue_ids: tuple[StableCueId, ...] = ()

    @model_validator(mode="after")
    def interval_and_identities_are_valid(self) -> Self:
        """Reject reversed intervals and duplicate identities within a segment."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "review segment end_tc_ms must be greater than start_tc_ms"
            raise ValueError(message)
        if len(self.frame_ids) != len(set(self.frame_ids)):
            message = "review segment frame IDs must be unique"
            raise ValueError(message)
        if len(self.cue_ids) != len(set(self.cue_ids)):
            message = "review segment cue IDs must be unique"
            raise ValueError(message)
        return self


class FinalReviewAssignment(FrozenModel):
    """System-defined ordered frames and cues bound to current run evidence."""

    run_id: StableRunId
    duration_ms: PositiveMilliseconds
    frame_ids: tuple[StableFrameId, ...]
    cue_ids: tuple[StableCueId, ...]
    media_digest: Sha256Digest
    transcript_digest: Sha256Digest
    assignment_digest: Sha256Digest
    segments: tuple[ReviewSegmentAssignment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identities_are_unique_and_grouped_exactly_once(self) -> Self:
        """Keep top-level identity order equal to the segment grouping."""
        segment_ids = tuple(segment.segment_id for segment in self.segments)
        if len(segment_ids) != len(set(segment_ids)):
            message = "review assignment segment IDs must be unique"
            raise ValueError(message)
        if len(self.frame_ids) != len(set(self.frame_ids)):
            message = "review assignment frame IDs must be unique"
            raise ValueError(message)
        if len(self.cue_ids) != len(set(self.cue_ids)):
            message = "review assignment cue IDs must be unique"
            raise ValueError(message)
        grouped_frame_ids = tuple(
            frame_id for segment in self.segments for frame_id in segment.frame_ids
        )
        if grouped_frame_ids != self.frame_ids:
            message = "review assignment segments must preserve exact frame order"
            raise ValueError(message)
        grouped_cue_ids = tuple(
            cue_id for segment in self.segments for cue_id in segment.cue_ids
        )
        if grouped_cue_ids != self.cue_ids:
            message = "review assignment segments must preserve exact cue order"
            raise ValueError(message)
        return self


class CueRevision(FrozenModel):
    """One complete certain cue produced by a grouped replacement."""

    cue_id: StableCueId
    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: PositiveMilliseconds
    speech: str
    speaker: str | None = None
    uncertainty: Literal[Uncertainty.CERTAIN]
    screen_text: str | None = None
    vision_evidence_ids: tuple[StableEvidenceId, ...] = ()

    @model_validator(mode="after")
    def interval_and_evidence_are_valid(self) -> Self:
        """Reject reversed intervals and ambiguous evidence references."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "cue revision end_tc_ms must be greater than start_tc_ms"
            raise ValueError(message)
        if len(self.vision_evidence_ids) != len(set(self.vision_evidence_ids)):
            message = "cue revision vision evidence IDs must be unique"
            raise ValueError(message)
        validate_speaker_notation(self.speaker, self.speech, require_speaker=True)
        return self


CueRevisionInput = Annotated[
    InstanceOf[CueRevision] | CueRevision,
    Field(union_mode="left_to_right"),
]


class CueReplacement(FrozenModel):
    """Replace one or more existing cues with zero or more complete revisions."""

    target_cue_ids: tuple[StableCueId, ...] = Field(min_length=1)
    revisions: tuple[CueRevisionInput, ...] = ()

    @model_validator(mode="after")
    def replacement_identities_are_unique(self) -> Self:
        """Reject repeated targets or produced IDs within one replacement."""
        if len(self.target_cue_ids) != len(set(self.target_cue_ids)):
            message = "cue replacement target IDs must be unique"
            raise ValueError(message)
        produced_ids = tuple(revision.cue_id for revision in self.revisions)
        if len(produced_ids) != len(set(produced_ids)):
            message = "cue replacement produced IDs must be unique"
            raise ValueError(message)
        return self


class FrameReviewObservation(FrozenModel):
    """Semantic evidence recorded after inspecting one assigned frame."""

    frame_id: StableFrameId
    visible_screen_text: tuple[VisibleScreenText, ...]
    compared_cue_ids: tuple[StableCueId, ...]

    @model_validator(mode="after")
    def compared_cue_identities_are_unique(self) -> Self:
        """Keep one unambiguous comparison record per cue."""
        if len(self.compared_cue_ids) != len(set(self.compared_cue_ids)):
            message = "frame observation compared cue IDs must be unique"
            raise ValueError(message)
        return self


class ReviewSegmentResult(FrozenModel):
    """Explicit evidence acknowledgement and corrections for one segment."""

    segment_id: StableSegmentId
    frame_ids: tuple[StableFrameId, ...] = ()
    cue_ids: tuple[StableCueId, ...] = ()
    frame_observations: tuple[FrameReviewObservation, ...] = ()
    replacements: tuple[CueReplacement, ...] = ()

    @model_validator(mode="after")
    def reviewed_identities_are_unique(self) -> Self:
        """Require ordered semantic observations and segment-owned corrections."""
        if len(self.frame_ids) != len(set(self.frame_ids)):
            message = "review result frame IDs must be unique"
            raise ValueError(message)
        if len(self.cue_ids) != len(set(self.cue_ids)):
            message = "review result cue IDs must be unique"
            raise ValueError(message)
        observed_frame_ids = tuple(
            observation.frame_id for observation in self.frame_observations
        )
        if observed_frame_ids != self.frame_ids:
            message = "frame observations must exactly match reviewed frame IDs"
            raise ValueError(message)
        owned_cue_ids = set(self.cue_ids)
        if any(
            cue_id not in owned_cue_ids
            for observation in self.frame_observations
            for cue_id in observation.compared_cue_ids
        ):
            message = "frame observation cue IDs must belong to review segment"
            raise ValueError(message)
        if any(
            cue_id not in owned_cue_ids
            for replacement in self.replacements
            for cue_id in replacement.target_cue_ids
        ):
            message = "replacement target cue IDs must belong to review segment"
            raise ValueError(message)
        return self


class FinalReviewResult(FrozenModel):
    """Complete reviewer response bound to one exact assignment digest."""

    run_id: StableRunId
    assignment_digest: Sha256Digest
    segments: tuple[ReviewSegmentResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def result_identities_are_unique(self) -> Self:
        """Reject repeated coverage, targets, and produced IDs globally."""
        segment_ids = tuple(segment.segment_id for segment in self.segments)
        if len(segment_ids) != len(set(segment_ids)):
            message = "review result segment IDs must be unique"
            raise ValueError(message)
        frame_ids = tuple(
            frame_id for segment in self.segments for frame_id in segment.frame_ids
        )
        if len(frame_ids) != len(set(frame_ids)):
            message = "review result frame IDs must be unique"
            raise ValueError(message)
        cue_ids = tuple(
            cue_id for segment in self.segments for cue_id in segment.cue_ids
        )
        if len(cue_ids) != len(set(cue_ids)):
            message = "review result cue IDs must be unique"
            raise ValueError(message)
        replacements = tuple(
            replacement
            for segment in self.segments
            for replacement in segment.replacements
        )
        target_ids = tuple(
            cue_id
            for replacement in replacements
            for cue_id in replacement.target_cue_ids
        )
        if len(target_ids) != len(set(target_ids)):
            message = "cue replacement target IDs must be unique across the result"
            raise ValueError(message)
        produced_ids = tuple(
            revision.cue_id
            for replacement in replacements
            for revision in replacement.revisions
        )
        if len(produced_ids) != len(set(produced_ids)):
            message = "cue replacement produced IDs must be unique across the result"
            raise ValueError(message)
        return self


class FinalReviewReceipt(FrozenModel):
    """Commit marker binding a clean review to current media and transcript."""

    run_id: StableRunId
    assignment_digest: Sha256Digest
    result_digest: Sha256Digest
    media_digest: Sha256Digest
    transcript_digest: Sha256Digest
    result_cue_ids: tuple[StableCueId, ...]

    @model_validator(mode="after")
    def result_identities_are_unique(self) -> Self:
        """Keep the committed resulting cue order unambiguous."""
        if len(self.result_cue_ids) != len(set(self.result_cue_ids)):
            message = "final review receipt cue IDs must be unique"
            raise ValueError(message)
        return self

__all__ = [
    "CueReplacement",
    "CueRevision",
    "FinalReviewAssignment",
    "FinalReviewReceipt",
    "FinalReviewResult",
    "FrameReviewObservation",
    "ReviewSegmentAssignment",
    "ReviewSegmentResult",
    "Sha256Digest",
]
