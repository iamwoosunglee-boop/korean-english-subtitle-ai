# Copyright 2026 Transcription Module contributors

"""Bound direct visual-review calls and aggregate one complete window result."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from transcription_module._visual_review_batch_models import VisualReviewBatchRecord
from transcription_module.visual_review import (
    FrameVisualObservation,
    RawVisualCandidate,
    VisualContextSpan,
    WindowVisualReviewAssignment,
    WindowVisualReviewer,
    WindowVisualReviewResult,
    visual_review_assignment_digest,
)
from transcription_module.visual_review_validation import (
    validate_visual_review_result,
)

if TYPE_CHECKING:
    from transcription_module.models import FrameId

CORE_FRAME_LIMIT: Final = 20
CONTEXT_FRAME_OVERLAP: Final = 2


@dataclass(frozen=True, slots=True)
class _VisualReviewBatch:
    assignment: WindowVisualReviewAssignment
    owned_frame_ids: tuple[FrameId, ...]


@dataclass(frozen=True, slots=True)
class BatchedVisualReview:
    """One complete parent result plus its provider execution records."""

    result: WindowVisualReviewResult
    records: tuple[VisualReviewBatchRecord, ...]
    direct_results: tuple[WindowVisualReviewResult, ...]


def _batch_assignment(
    parent: WindowVisualReviewAssignment,
    start: int,
    end: int,
) -> _VisualReviewBatch:
    context_start = max(0, start - CONTEXT_FRAME_OVERLAP)
    context_end = min(len(parent.frames), end + CONTEXT_FRAME_OVERLAP)
    owned_frame_ids = tuple(frame.frame_id for frame in parent.frames[start:end])
    unbound = parent.model_copy(
        update={
            "frames": parent.frames[context_start:context_end],
            "owned_frame_ids": owned_frame_ids,
            "reviewer_input_digest": "0" * 64,
        }
    )
    assignment = WindowVisualReviewAssignment.model_validate(
        unbound.model_copy(
            update={
                "reviewer_input_digest": visual_review_assignment_digest(unbound),
            }
        ).model_dump()
    )
    return _VisualReviewBatch(
        assignment=assignment,
        owned_frame_ids=owned_frame_ids,
    )


def plan_visual_review_batches(
    assignment: WindowVisualReviewAssignment,
) -> tuple[_VisualReviewBatch, ...]:
    if not assignment.frames:
        return (_VisualReviewBatch(assignment=assignment, owned_frame_ids=()),)
    return tuple(
        _batch_assignment(
            assignment,
            start,
            min(start + CORE_FRAME_LIMIT, len(assignment.frames)),
        )
        for start in range(0, len(assignment.frames), CORE_FRAME_LIMIT)
    )


def _batch_spans(
    namespace_prefix: str,
    result: WindowVisualReviewResult,
    candidates: tuple[RawVisualCandidate, ...],
    owned: set[FrameId],
) -> tuple[tuple[VisualContextSpan, ...], dict[str, str]]:
    referenced = {
        span_id for candidate in candidates for span_id in candidate.context_span_ids
    }
    selected = tuple(
        span
        for span in result.context_spans
        if span.supporting_frame_ids[0] in owned or span.context_span_id in referenced
    )
    renamed = {
        span.context_span_id: f"{namespace_prefix}{span.context_span_id}"
        for span in selected
    }
    return (
        tuple(
            span.model_copy(update={"context_span_id": renamed[span.context_span_id]})
            for span in selected
        ),
        renamed,
    )


def _batch_candidates(
    namespace_prefix: str,
    result: WindowVisualReviewResult,
    owned: set[FrameId],
) -> tuple[RawVisualCandidate, ...]:
    return tuple(
        candidate.model_copy(
            update={"candidate_id": f"{namespace_prefix}{candidate.candidate_id}"}
        )
        for candidate in result.candidates
        if candidate.supporting_frame_ids[0] in owned
    )


def batch_record(
    parent: WindowVisualReviewAssignment,
    batch_index: int,
    batch: _VisualReviewBatch,
    result: WindowVisualReviewResult,
) -> VisualReviewBatchRecord:
    return VisualReviewBatchRecord(
        window_index=parent.window_index,
        batch_index=batch_index,
        parent_reviewer_input_digest=parent.reviewer_input_digest,
        reviewer_input_digest=batch.assignment.reviewer_input_digest,
        assigned_frame_ids=tuple(frame.frame_id for frame in batch.assignment.frames),
        owned_frame_ids=batch.owned_frame_ids,
        result_sha256=hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
    )


def merge_visual_review_batch_results(
    assignment: WindowVisualReviewAssignment,
    batches: tuple[_VisualReviewBatch, ...],
    results: tuple[WindowVisualReviewResult, ...],
) -> WindowVisualReviewResult:
    if len(batches) != len(results):
        message = "visual review batch plan and direct results differ"
        raise ValueError(message)
    observations: list[FrameVisualObservation] = []
    spans: list[VisualContextSpan] = []
    candidates: list[RawVisualCandidate] = []
    for batch_index, (batch, result) in enumerate(zip(batches, results, strict=True)):
        validate_visual_review_result(batch.assignment, result)
        namespace_prefix = "" if len(batches) == 1 else f"batch-{batch_index}:"
        owned = set(batch.owned_frame_ids)
        observation_by_id = {item.frame_id: item for item in result.frame_observations}
        observations.extend(
            observation_by_id[frame_id] for frame_id in batch.owned_frame_ids
        )
        selected_candidates = _batch_candidates(
            namespace_prefix,
            result,
            owned,
        )
        selected_spans, renamed_spans = _batch_spans(
            namespace_prefix,
            result,
            selected_candidates,
            owned,
        )
        spans.extend(selected_spans)
        candidates.extend(
            candidate.model_copy(
                update={
                    "context_span_ids": tuple(
                        renamed_spans[span_id] for span_id in candidate.context_span_ids
                    )
                }
            )
            for candidate in selected_candidates
        )
    observation_by_id = {item.frame_id: item for item in observations}
    merged = WindowVisualReviewResult(
        window_index=assignment.window_index,
        reviewer_input_digest=assignment.reviewer_input_digest,
        frame_observations=tuple(
            observation_by_id[frame.frame_id] for frame in assignment.frames
        ),
        context_spans=tuple(spans),
        candidates=tuple(candidates),
    )
    validate_visual_review_result(assignment, merged)
    return merged


async def review_visual_assignment_in_batches(
    assignment: WindowVisualReviewAssignment,
    reviewer: WindowVisualReviewer,
) -> BatchedVisualReview:
    """Review bounded frame slices and recover one parent-bound result."""
    direct_results: list[WindowVisualReviewResult] = []
    batches = plan_visual_review_batches(assignment)
    for batch in batches:
        result = await reviewer(batch.assignment)
        validate_visual_review_result(batch.assignment, result)
        direct_results.append(result)
    direct = tuple(direct_results)
    records = tuple(
        batch_record(assignment, index, batch, result)
        for index, (batch, result) in enumerate(zip(batches, direct, strict=True))
    )
    merged = merge_visual_review_batch_results(
        assignment,
        batches,
        direct,
    )
    return BatchedVisualReview(
        result=merged,
        records=records,
        direct_results=direct,
    )
