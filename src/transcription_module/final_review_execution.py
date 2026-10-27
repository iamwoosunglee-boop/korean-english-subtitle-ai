# Copyright 2026 Transcription Module contributors

"""Bounded semantic final-review execution over stored evidence only."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from transcription_module._final_review import build_assignment
from transcription_module._final_review_execution_evidence import (
    build_segment_inputs,
    source_media_unreadable,
    validate_segment_result,
)
from transcription_module.final_review import (
    FinalReviewError,
    apply_final_review,
    prepare_final_review,
)
from transcription_module.final_review_execution_models import (
    FinalReviewExecutionError,
    FinalReviewSegmentInput,
    FinalReviewSegmentReviewer,
    KoreanVttEvidence,
)
from transcription_module.final_review_models import (
    FinalReviewReceipt,
    FinalReviewResult,
    ReviewSegmentResult,
)

if TYPE_CHECKING:
    from transcription_module.models import RunId
    from transcription_module.storage import RunStore


async def execute_final_review(
    store: RunStore,
    run_id: RunId,
    reviewer: FinalReviewSegmentReviewer,
) -> FinalReviewReceipt:
    """Review every assignment segment with at most four concurrent callbacks."""
    assignment = build_assignment(store, run_id)
    media, inputs = build_segment_inputs(store, run_id, assignment)
    results: list[ReviewSegmentResult | None] = [None] * len(inputs)
    limiter = anyio.CapacityLimiter(4)

    async def run_one(index: int, review_input: FinalReviewSegmentInput) -> None:
        async with limiter:
            results[index] = await reviewer(review_input)

    with source_media_unreadable(store.paths(run_id).run_dir, media):
        try:
            async with anyio.create_task_group() as tasks:
                for index, review_input in enumerate(inputs):
                    _ = tasks.start_soon(run_one, index, review_input)
        except ExceptionGroup as error:
            message = "final review reviewer callback failed"
            raise FinalReviewExecutionError(message) from error

    completed: list[ReviewSegmentResult] = []
    for review_input, result in zip(inputs, results, strict=True):
        if result is None:
            message = "final review reviewer returned no segment result"
            raise FinalReviewExecutionError(message)
        completed.append(validate_segment_result(review_input, result))
    persisted = prepare_final_review(store, run_id)
    if persisted.assignment_digest != assignment.assignment_digest:
        message = "final review execution evidence became stale"
        raise FinalReviewError(message)
    return apply_final_review(
        store,
        run_id,
        FinalReviewResult(
            run_id=run_id,
            assignment_digest=assignment.assignment_digest,
            segments=tuple(completed),
        ),
    )


__all__ = [
    "FinalReviewExecutionError",
    "FinalReviewSegmentInput",
    "FinalReviewSegmentReviewer",
    "KoreanVttEvidence",
    "execute_final_review",
]
