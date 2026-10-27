# Copyright 2026 Transcription Module contributors

"""Typed contracts for bounded window drafting and transcript assembly."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, assert_never

from pydantic import Field, model_validator

from transcription_module.asr import AsrSegment  # noqa: TC001
from transcription_module.correction_receipts import CorrectionReceipt  # noqa: TC001
from transcription_module.media import (  # noqa: TC001
    PreparedFrameManifest,
    PreparedSubtitleTrack,
)
from transcription_module.models import EpisodeContext, FrameId, FrozenModel, RunId
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.visual_observations import VisualObservation  # noqa: TC001
from transcription_module.visual_review import (  # noqa: TC001
    RawCandidateDisposition,
    WindowVisualReviewResult,
)
from transcription_module.window_evidence_models import (
    ProvisionalVisualInsertion,
    WindowCueEvidence,
    WindowSubtitleCue,
    WindowVadInterval,
    WindowVerifiedTurn,
)

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DiarizationError(Exception):
    """Optional diarization could not produce usable cluster evidence."""


class DiarizationStatus(StrEnum):
    """Persisted outcome of one optional whole-audio diarization attempt."""

    AVAILABLE = "available"
    FAILED = "failed"


class DraftCue(FrozenModel):
    """One temporary utterance without a permanent cue ID."""

    start_tc_ms: NonNegativeInt
    end_tc_ms: PositiveInt
    speech: str = Field(min_length=1)
    speaker: str | None = None
    screen_text: str | None = None

    @model_validator(mode="after")
    def end_follows_start(self) -> DraftCue:
        """Reject zero-length and reversed temporary cues."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "end_tc_ms must be greater than start_tc_ms"
            raise ValueError(message)
        return self


class UnresolvedReasonCode(StrEnum):
    """Stable policy decisions for uncertain drafter outcomes."""

    SAME_START_TURN_AMBIGUITY = "same_start_turn_ambiguity"
    VISUAL_EVIDENCE_CONFLICT = "visual_evidence_conflict"
    VISUAL_EVIDENCE_INSUFFICIENT = "visual_evidence_insufficient"
    UNSUPPORTED_REWRITE = "unsupported_rewrite"
    SPEAKER_AMBIGUITY = "speaker_ambiguity"
    SCREEN_TEXT_AMBIGUITY = "screen_text_ambiguity"
    EXTERNAL_FACT_CONFLICT = "external_fact_conflict"


class UnresolvedItem(FrozenModel):
    """A concrete field the window drafter could not determine."""

    at_ms: NonNegativeInt
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    candidates: tuple[str, ...] = ()
    reason_code: UnresolvedReasonCode | None = None
    target_cue_index: NonNegativeInt | None = None
    source_evidence_ids: tuple[str, ...] = ()
    candidate_receipt_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_complete_candidates(self) -> UnresolvedItem:
        """Reject excerpts and repeated source or candidate identities."""
        if any("..." in candidate or "…" in candidate for candidate in self.candidates):
            message = "candidates must be complete replacement values"
            raise ValueError(message)
        if len(self.source_evidence_ids) != len(set(self.source_evidence_ids)):
            message = "unresolved source evidence IDs must be unique"
            raise ValueError(message)
        if len(self.candidate_receipt_ids) != len(set(self.candidate_receipt_ids)):
            message = "unresolved candidate receipt IDs must be unique"
            raise ValueError(message)
        if self.candidate_receipt_ids and len(self.candidate_receipt_ids) != len(
            self.candidates
        ):
            message = "unresolved candidates require one receipt ID each"
            raise ValueError(message)
        return self


class SpeakerEvidenceSegment(FrozenModel):
    """Fallible voice-cluster evidence, never a person identity."""

    start_tc_ms: NonNegativeInt
    end_tc_ms: PositiveInt
    cluster: str = Field(min_length=1)


class DiarizationRunArtifact(FrozenModel):
    """Optional whole-audio voice clusters or their failure reason."""

    run_id: RunId
    status: DiarizationStatus
    segments: tuple[SpeakerEvidenceSegment, ...] = ()
    error: str | None = None


class WindowDraftInput(FrozenModel):
    """Persisted evidence references supplied to one independent drafter."""

    window_index: NonNegativeInt
    run_id: RunId | None = None
    core_start_ms: NonNegativeInt
    core_end_ms: PositiveInt
    context_start_ms: NonNegativeInt
    context_end_ms: PositiveInt
    asr_segments: tuple[AsrSegment, ...]
    subtitle_tracks: tuple[PreparedSubtitleTrack, ...]
    frames: tuple[PreparedFrameManifest, ...]
    speaker_segments: tuple[SpeakerEvidenceSegment, ...] = ()
    vision_artifact_path: str | None = None
    episode_context: EpisodeContext | None = None
    era: EvidenceEra = EvidenceEra.LEGACY
    policy_digest: Sha256Digest | None = None
    inventory_digest: Sha256Digest | None = None
    source_digest: Sha256Digest | None = None
    subtitle_cues: tuple[WindowSubtitleCue, ...] = ()
    vad_intervals: tuple[WindowVadInterval, ...] = ()
    verified_turns: tuple[WindowVerifiedTurn, ...] = ()
    visual_review: WindowVisualReviewResult | None = None

    @model_validator(mode="after")
    def policy_bound_input_has_complete_identity(self) -> WindowDraftInput:
        """Keep legacy input readable and require complete policy identity."""
        match self.era:
            case EvidenceEra.LEGACY:
                return self
            case EvidenceEra.POLICY_BOUND:
                if self.run_id is None:
                    message = "policy-bound window input requires a run identity"
                    raise ValueError(message)
                if any(
                    digest is None
                    for digest in (
                        self.policy_digest,
                        self.inventory_digest,
                        self.source_digest,
                    )
                ):
                    message = "policy-bound window input requires complete digests"
                    raise ValueError(message)
                timed = (
                    *((item.start_ms, item.end_ms) for item in self.asr_segments),
                    *(
                        (item.start_tc_ms, item.end_tc_ms)
                        for item in self.subtitle_cues
                    ),
                    *(
                        (item.start_tc_ms, item.end_tc_ms)
                        for item in self.vad_intervals
                    ),
                    *(
                        (item.start_tc_ms, item.end_tc_ms)
                        for item in self.verified_turns
                    ),
                )
                if any(
                    start >= self.context_end_ms or end <= self.context_start_ms
                    for start, end in timed
                ) or any(
                    not self.context_start_ms <= frame.pts_ms < self.context_end_ms
                    for frame in self.frames
                ):
                    message = "policy-bound timed evidence must overlap its context"
                    raise ValueError(message)
                if any(segment.source_ordinal is None for segment in self.asr_segments):
                    message = "policy-bound local ASR requires stable ordinals"
                    raise ValueError(message)
                return self
            case _:
                assert_never(self.era)


class WindowDraft(FrozenModel):
    """One independently produced transcription draft."""

    window_index: NonNegativeInt
    cues: tuple[DraftCue, ...]
    unresolved: tuple[UnresolvedItem, ...]
    evidence_reviewed: bool = False
    era: EvidenceEra = EvidenceEra.LEGACY
    policy_digest: Sha256Digest | None = None
    inventory_digest: Sha256Digest | None = None
    source_digest: Sha256Digest | None = None
    observations: tuple[VisualObservation, ...] = ()
    corrections: tuple[CorrectionReceipt, ...] = ()
    cue_evidence: tuple[WindowCueEvidence, ...] = ()
    candidate_dispositions: tuple[RawCandidateDisposition, ...] = ()
    provisional_visual_insertions: tuple[ProvisionalVisualInsertion, ...] = ()

    @model_validator(mode="after")
    def policy_bound_output_rejects_legacy_authority(self) -> WindowDraft:
        """Parse old artifacts without accepting their boolean in new drafts."""
        match self.era:
            case EvidenceEra.LEGACY:
                return self
            case EvidenceEra.POLICY_BOUND:
                if self.evidence_reviewed:
                    message = (
                        "policy-bound draft cannot use legacy evidence review boolean"
                    )
                    raise ValueError(message)
                if any(
                    digest is None
                    for digest in (
                        self.policy_digest,
                        self.inventory_digest,
                        self.source_digest,
                    )
                ):
                    message = "policy-bound draft requires complete digest identity"
                    raise ValueError(message)
                return self
            case _:
                assert_never(self.era)


class WindowDraftArtifact(FrozenModel):
    """All per-window evidence inputs and corresponding draft results."""

    run_id: RunId
    inputs: tuple[WindowDraftInput, ...]
    drafts: tuple[WindowDraft, ...]


class WholeTranscriptInput(FrozenModel):
    """All drafts plus direct access to already stored PTS frames."""

    run_dir: Path
    window_drafts: WindowDraftArtifact
    frames: tuple[PreparedFrameManifest, ...]


class WholeTranscriptDraft(FrozenModel):
    """Agent-authored whole-video result before permanent cue IDs."""

    cues: tuple[DraftCue, ...]
    unresolved: tuple[UnresolvedItem, ...]
    inspected_frame_ids: tuple[FrameId, ...] = ()


class WholeTranscriptArtifact(FrozenModel):
    """Persisted assembly provenance outside the editable transcript."""

    run_id: RunId
    window_count: int
    inspected_frame_ids: tuple[FrameId, ...]
    unresolved: tuple[UnresolvedItem, ...]
    era: EvidenceEra = EvidenceEra.LEGACY
    policy_digest: Sha256Digest | None = None
    source_digest: Sha256Digest | None = None
    applied_corrections: tuple[CorrectionReceipt, ...] = ()
    provisional_visual_insertions: tuple[ProvisionalVisualInsertion, ...] = ()


WindowDrafter = Callable[[WindowDraftInput], Awaitable[WindowDraft]]
WholeTranscriptAssembler = Callable[
    [WholeTranscriptInput], Awaitable[WholeTranscriptDraft]
]
Diarizer = Callable[[Path], tuple[SpeakerEvidenceSegment, ...]]


__all__ = [
    "DiarizationError",
    "DiarizationRunArtifact",
    "DiarizationStatus",
    "Diarizer",
    "DraftCue",
    "ProvisionalVisualInsertion",
    "SpeakerEvidenceSegment",
    "UnresolvedItem",
    "UnresolvedReasonCode",
    "WholeTranscriptArtifact",
    "WholeTranscriptAssembler",
    "WholeTranscriptDraft",
    "WholeTranscriptInput",
    "WindowDraft",
    "WindowDraftArtifact",
    "WindowDraftInput",
    "WindowDrafter",
]
