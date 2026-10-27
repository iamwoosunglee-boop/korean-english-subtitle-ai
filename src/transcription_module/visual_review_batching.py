# Copyright 2026 Transcription Module contributors

"""Lossless batching after an observed visual-provider capacity limit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from transcription_module.models import FrozenModel
from transcription_module.visual_review import (
    RawVisualCandidate,
    VisualContextSpan,
    VisualFrameOwnership,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
    visual_review_assignment_digest,
)
from transcription_module.visual_review_validation import (
    validate_visual_review_result,
)

if TYPE_CHECKING:
    from transcription_module.models import FrameId
    from transcription_module.visual_review import FrameVisualObservation

DEFAULT_VISUAL_BATCH_SIZE = 20
DEFAULT_VISUAL_BATCH_OVERLAP = 2


class VisualReviewBatchError(Exception):
    """A batch plan cannot preserve exact parent-frame ownership."""


class VisualReviewBatchPlan(FrozenModel):
    """Ordered bounded assignments for one parent visual window."""

    schema_version: Literal["visual-review-batch-plan.v1"] = (
        "visual-review-batch-plan.v1"
    )
    parent_reviewer_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_size: int = Field(gt=0)
    overlap_size: int = Field(ge=0)
    assignments: tuple[WindowVisualReviewAssignment, ...]


@dataclass(frozen=True, slots=True)
class _BatchSlice:
    start: int
    size: int
    overlap: int


def plan_visual_review_batches(
    parent: WindowVisualReviewAssignment,
    batch_size: int = DEFAULT_VISUAL_BATCH_SIZE,
    overlap_size: int = DEFAULT_VISUAL_BATCH_OVERLAP,
) -> VisualReviewBatchPlan:
    """Partition all parent frames with exact ownership and bounded overlap."""
    if batch_size <= 0 or overlap_size < 0 or overlap_size >= batch_size:
        message = "visual review batch and overlap sizes are invalid"
        raise VisualReviewBatchError(message)
    assignments = tuple(
        _batch_assignment(
            parent,
            index,
            _BatchSlice(start=start, size=batch_size, overlap=overlap_size),
        )
        for index, start in enumerate(range(0, len(parent.frames), batch_size))
    )
    owned_ids = tuple(
        frame.frame_id
        for assignment in assignments
        for frame in assignment.frames
        if frame.ownership is VisualFrameOwnership.CORE
    )
    parent_ids = tuple(frame.frame_id for frame in parent.frames)
    if owned_ids != parent_ids:
        message = "visual review batches do not own every parent frame exactly once"
        raise VisualReviewBatchError(message)
    return VisualReviewBatchPlan(
        parent_reviewer_input_digest=parent.reviewer_input_digest,
        batch_size=batch_size,
        overlap_size=overlap_size,
        assignments=assignments,
    )


def merge_visual_review_batches(
    parent: WindowVisualReviewAssignment,
    plan: VisualReviewBatchPlan,
    results: tuple[WindowVisualReviewResult, ...],
) -> WindowVisualReviewResult:
    """Merge validated batch results into one exact parent-window result."""
    if (
        plan.parent_reviewer_input_digest != parent.reviewer_input_digest
        or len(results) != len(plan.assignments)
    ):
        message = "visual review batch results do not match their parent plan"
        raise VisualReviewBatchError(message)
    observations: list[FrameVisualObservation] = []
    spans: list[VisualContextSpan] = []
    candidates: list[RawVisualCandidate] = []
    parent_ownership = {
        frame.frame_id: frame.ownership for frame in parent.frames
    }
    for assignment, result in zip(plan.assignments, results, strict=True):
        validate_visual_review_result(assignment, result)
        by_frame = {
            observation.frame_id: observation
            for observation in result.frame_observations
        }
        observations.extend(
            by_frame[frame.frame_id]
            for frame in assignment.frames
            if frame.ownership is VisualFrameOwnership.CORE
        )
        span_ids = {
            span.context_span_id: (
                f"batch-{assignment.window_index}:{span.context_span_id}"
            )
            for span in result.context_spans
        }
        spans.extend(
            span.model_copy(
                update={"context_span_id": span_ids[span.context_span_id]}
            )
            for span in result.context_spans
        )
        candidates.extend(
            candidate.model_copy(
                update={
                    "candidate_id": (
                        f"batch-{assignment.window_index}:{candidate.candidate_id}"
                    ),
                    "context_span_ids": tuple(
                        span_ids[span_id] for span_id in candidate.context_span_ids
                    ),
                }
            )
            for candidate in result.candidates
            if parent_ownership[candidate.supporting_frame_ids[0]]
            is VisualFrameOwnership.CORE
        )
    merged = WindowVisualReviewResult(
        window_index=parent.window_index,
        reviewer_input_digest=parent.reviewer_input_digest,
        frame_observations=tuple(observations),
        context_spans=tuple(spans),
        candidates=_coalesce_candidates(parent, candidates),
    )
    validate_visual_review_result(parent, merged)
    return merged


def _batch_assignment(
    parent: WindowVisualReviewAssignment,
    batch_index: int,
    batch_slice: _BatchSlice,
) -> WindowVisualReviewAssignment:
    start = batch_slice.start
    end = min(start + batch_slice.size, len(parent.frames))
    left = max(0, start - batch_slice.overlap)
    right = min(len(parent.frames), end + batch_slice.overlap)
    core_start = parent.frames[start].pts_ms
    core_end = (
        parent.frames[end].pts_ms if end < len(parent.frames) else parent.context_end_ms
    )
    context_start = parent.frames[left].pts_ms
    context_end = (
        parent.frames[right].pts_ms
        if right < len(parent.frames)
        else parent.context_end_ms
    )
    frames = tuple(
        frame.model_copy(
            update={
                "ownership": (
                    VisualFrameOwnership.CORE
                    if start <= position < end
                    else VisualFrameOwnership.CONTEXT
                )
            }
        )
        for position, frame in enumerate(parent.frames[left:right], start=left)
    )
    unbound = WindowVisualReviewAssignment.model_construct(
        run_id=parent.run_id,
        window_index=batch_index,
        core_start_ms=core_start,
        core_end_ms=core_end,
        context_start_ms=context_start,
        context_end_ms=context_end,
        episode_context=parent.episode_context,
        reviewer=parent.reviewer,
        frames=frames,
        reviewer_input_digest="0" * 64,
    )
    return WindowVisualReviewAssignment.model_validate(
        unbound.model_copy(
            update={
                "reviewer_input_digest": visual_review_assignment_digest(unbound),
            }
        ).model_dump()
    )


def _coalesce_candidates(
    parent: WindowVisualReviewAssignment,
    candidates: list[RawVisualCandidate],
) -> tuple[RawVisualCandidate, ...]:
    positions = {
        frame.frame_id: index for index, frame in enumerate(parent.frames)
    }
    merged: list[RawVisualCandidate] = []
    for candidate in candidates:
        if merged and _same_occurrence(merged[-1], candidate, positions):
            previous = merged[-1]
            merged[-1] = previous.model_copy(
                update={
                    "supporting_frame_ids": tuple(
                        frame.frame_id
                        for frame in parent.frames
                        if frame.frame_id
                        in {
                            *previous.supporting_frame_ids,
                            *candidate.supporting_frame_ids,
                        }
                    ),
                    "context_span_ids": _unique(
                        (*previous.context_span_ids, *candidate.context_span_ids)
                    ),
                    "evidence_for": _unique(
                        (*previous.evidence_for, *candidate.evidence_for)
                    ),
                    "evidence_against": _unique(
                        (*previous.evidence_against, *candidate.evidence_against)
                    ),
                    "missing_evidence": _unique(
                        (*previous.missing_evidence, *candidate.missing_evidence)
                    ),
                }
            )
        else:
            merged.append(candidate)
    return tuple(merged)


def _same_occurrence(
    left: RawVisualCandidate,
    right: RawVisualCandidate,
    positions: dict[FrameId, int],
) -> bool:
    return (
        left.complete_text == right.complete_text
        and left.proposed_role is right.proposed_role
        and positions[right.supporting_frame_ids[0]]
        <= positions[left.supporting_frame_ids[-1]] + 1
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "DEFAULT_VISUAL_BATCH_OVERLAP",
    "DEFAULT_VISUAL_BATCH_SIZE",
    "VisualReviewBatchError",
    "VisualReviewBatchPlan",
    "merge_visual_review_batches",
    "plan_visual_review_batches",
]
