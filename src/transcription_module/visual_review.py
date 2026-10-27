# Copyright 2026 Transcription Module contributors

"""Policy-neutral visual review contracts before window drafting."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator

from transcription_module.models import (
    EpisodeContext,
    FrameId,
    FrozenModel,
    RunId,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

NonBlank = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class VisualFrameOwnership(StrEnum):
    """Whether one assigned frame belongs to core or read-only context."""

    CORE = "core"
    CONTEXT = "context"


class RawVisualRole(StrEnum):
    """A frame-only proposal that makes no speech-policy claim."""

    SPEECH_CAPTION = "speech_caption"
    NON_SPEECH_TEXT = "non_speech_text"
    UNKNOWN = "unknown"


class CandidateDispositionKind(StrEnum):
    """One downstream logical claim for an owned raw speech candidate."""

    EXISTING_CUE = "existing_cue"
    AUTHORIZED_INSERTION = "authorized_insertion"
    PROVISIONAL_INSERTION = "provisional_insertion"
    RETAINED_EVIDENCE = "retained_evidence"


class VisualReviewerIdentity(FrozenModel):
    """Immutable prompt and model identity used for one visual review."""

    prompt_version: NonBlank
    prompt_digest: Sha256Digest
    model_id: NonBlank
    settings_digest: Sha256Digest
    reviewer_mode: Literal["exhaustive"] = "exhaustive"


class VisualReviewFrame(FrozenModel):
    """One snapshot-bound frame exposed to the visual reviewer."""

    frame_id: FrameId
    pts_ms: NonNegative
    path: NonBlank
    sha256: Sha256Digest
    ownership: VisualFrameOwnership


class WindowVisualReviewAssignment(FrozenModel):
    """One cue-independent chronological frame assignment."""

    schema_version: Literal["window-visual-review-assignment.v1"] = (
        "window-visual-review-assignment.v1"
    )
    run_id: RunId
    window_index: NonNegative
    core_start_ms: NonNegative
    core_end_ms: Positive
    context_start_ms: NonNegative
    context_end_ms: Positive
    episode_context: EpisodeContext | None
    reviewer: VisualReviewerIdentity
    frames: tuple[VisualReviewFrame, ...]
    owned_frame_ids: tuple[FrameId, ...] | None = None
    reviewer_input_digest: Sha256Digest

    @model_validator(mode="after")
    def assignment_is_exact(self) -> Self:
        """Reject stale identity, invalid bounds, and ambiguous frame ownership."""
        if not (
            self.context_start_ms
            <= self.core_start_ms
            < self.core_end_ms
            <= self.context_end_ms
        ):
            message = "visual review assignment has invalid time bounds"
            raise ValueError(message)
        frame_ids = tuple(frame.frame_id for frame in self.frames)
        if len(frame_ids) != len(set(frame_ids)):
            message = "visual review assignment frame IDs must be unique"
            raise ValueError(message)
        if tuple(frame.pts_ms for frame in self.frames) != tuple(
            sorted(frame.pts_ms for frame in self.frames)
        ):
            message = "visual review assignment frames must be chronological"
            raise ValueError(message)
        _validate_owned_frame_ids(self.owned_frame_ids, frame_ids)
        for frame in self.frames:
            if not self.context_start_ms <= frame.pts_ms < self.context_end_ms:
                message = "visual review assignment frame leaves context bounds"
                raise ValueError(message)
            expected = (
                VisualFrameOwnership.CORE
                if self.core_start_ms <= frame.pts_ms < self.core_end_ms
                else VisualFrameOwnership.CONTEXT
            )
            if frame.ownership is not expected:
                message = "visual review assignment frame ownership is incorrect"
                raise ValueError(message)
        if self.reviewer_input_digest != visual_review_assignment_digest(self):
            message = "visual review assignment digest does not match its inputs"
            raise ValueError(message)
        return self


def _validate_owned_frame_ids(
    owned_frame_ids: tuple[FrameId, ...] | None,
    assigned_frame_ids: tuple[FrameId, ...],
) -> None:
    if owned_frame_ids is None:
        return
    if len(owned_frame_ids) != len(set(owned_frame_ids)):
        message = "visual review assignment owned frame IDs must be unique"
        raise ValueError(message)
    positions = {frame_id: index for index, frame_id in enumerate(assigned_frame_ids)}
    try:
        owned_positions = tuple(positions[frame_id] for frame_id in owned_frame_ids)
    except KeyError as error:
        message = "visual review assignment owns a foreign frame"
        raise ValueError(message) from error
    if assigned_frame_ids and (
        not owned_positions
        or owned_positions != tuple(range(owned_positions[0], owned_positions[-1] + 1))
    ):
        message = "visual review assignment ownership must be contiguous"
        raise ValueError(message)


class FrameVisualObservation(FrozenModel):
    """Complete visible strings for one assigned frame."""

    frame_id: FrameId
    visible_screen_text: tuple[NonBlank, ...] = ()

    @field_validator("visible_screen_text")
    @classmethod
    def visible_text_is_exact(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Reject padded and duplicate strings instead of normalizing them."""
        if any(text != text.strip() for text in value):
            message = "visible screen text must not have surrounding whitespace"
            raise ValueError(message)
        if len(value) != len(set(value)):
            message = "visible screen text must be unique within one frame"
            raise ValueError(message)
        return value


class VisualContextSpan(FrozenModel):
    """One ordered semantic span, including candidate-free context."""

    context_span_id: NonBlank
    supporting_frame_ids: tuple[FrameId, ...] = Field(min_length=1)
    context: NonBlank


class RawVisualCandidate(FrozenModel):
    """One visible-text occurrence without audio or speaker authority."""

    candidate_id: NonBlank
    complete_text: NonBlank
    proposed_role: RawVisualRole
    supporting_frame_ids: tuple[FrameId, ...] = Field(min_length=1)
    context_span_ids: tuple[NonBlank, ...] = ()
    evidence_for: tuple[NonBlank, ...] = ()
    evidence_against: tuple[NonBlank, ...] = ()
    missing_evidence: tuple[NonBlank, ...] = ()


class RawCandidateDisposition(FrozenModel):
    """One exclusive downstream disposition for a raw speech candidate."""

    candidate_id: NonBlank
    kind: CandidateDispositionKind
    target_cue_index: NonNegative | None = None
    evidence_ids: tuple[NonBlank, ...] = ()


class WindowVisualReviewResult(FrozenModel):
    """One reviewer result bound to an exact assignment digest."""

    schema_version: Literal["window-visual-review-result.v1"] = (
        "window-visual-review-result.v1"
    )
    window_index: NonNegative
    reviewer_input_digest: Sha256Digest
    frame_observations: tuple[FrameVisualObservation, ...]
    context_spans: tuple[VisualContextSpan, ...] = ()
    candidates: tuple[RawVisualCandidate, ...] = ()


class WindowVisualReviewArtifact(FrozenModel):
    """All validated visual assignments and results for one run."""

    schema_version: Literal["window-visual-review.v1"] = "window-visual-review.v1"
    run_id: RunId
    assignments: tuple[WindowVisualReviewAssignment, ...]
    results: tuple[WindowVisualReviewResult, ...]


class WindowVisualReviewer(Protocol):
    """Provider-neutral cue-independent visual reviewer."""

    @property
    def identity(self) -> VisualReviewerIdentity:
        """Return the immutable prompt and model identity."""
        ...

    def __call__(
        self,
        assignment: WindowVisualReviewAssignment,
    ) -> Awaitable[WindowVisualReviewResult]:
        """Review one chronological assignment."""
        ...


def visual_review_assignment_digest(
    assignment: WindowVisualReviewAssignment,
) -> str:
    """Hash canonical assignment bytes excluding the digest itself."""
    excluded = {"reviewer_input_digest"}
    if assignment.owned_frame_ids is None:
        excluded.add("owned_frame_ids")
    payload = assignment.model_dump_json(exclude=excluded)
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "CandidateDispositionKind",
    "FrameVisualObservation",
    "RawCandidateDisposition",
    "RawVisualCandidate",
    "RawVisualRole",
    "VisualContextSpan",
    "VisualFrameOwnership",
    "VisualReviewFrame",
    "VisualReviewerIdentity",
    "WindowVisualReviewArtifact",
    "WindowVisualReviewAssignment",
    "WindowVisualReviewResult",
    "WindowVisualReviewer",
    "visual_review_assignment_digest",
]
