# Copyright 2026 Transcription Module contributors

"""Observed provider limits trigger lossless visual-review batching."""

from __future__ import annotations

from transcription_module.models import FrameId, RunId
from transcription_module.visual_review import (
    FrameVisualObservation,
    VisualFrameOwnership,
    VisualReviewerIdentity,
    VisualReviewFrame,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
    visual_review_assignment_digest,
)
from transcription_module.visual_review_batching import (
    merge_visual_review_batches,
    plan_visual_review_batches,
)

EXPECTED_BATCH_COUNT = 3


def _parent_assignment(frame_count: int = 45) -> WindowVisualReviewAssignment:
    frames = tuple(
        VisualReviewFrame(
            frame_id=FrameId(f"frame-{index:03d}"),
            pts_ms=index * 500,
            path=f"frames/frame-{index:03d}.jpg",
            sha256=f"{index + 1:064x}",
            ownership=VisualFrameOwnership.CORE,
        )
        for index in range(frame_count)
    )
    unbound = WindowVisualReviewAssignment.model_construct(
        run_id=RunId("run-batched"),
        window_index=1,
        core_start_ms=0,
        core_end_ms=frame_count * 500,
        context_start_ms=0,
        context_end_ms=frame_count * 500,
        episode_context=None,
        reviewer=VisualReviewerIdentity(
            prompt_version="visual-review-generic.v8",
            prompt_digest="1" * 64,
            model_id="model-sentinel",
            settings_digest="2" * 64,
        ),
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


def test_batch_plan_owns_every_parent_frame_exactly_once() -> None:
    """Twenty-frame batches retain every frame with bounded overlap."""
    parent = _parent_assignment()

    plan = plan_visual_review_batches(parent)

    assert len(plan.assignments) == EXPECTED_BATCH_COUNT
    owned = tuple(
        frame.frame_id
        for assignment in plan.assignments
        for frame in assignment.frames
        if frame.ownership is VisualFrameOwnership.CORE
    )
    assert owned == tuple(frame.frame_id for frame in parent.frames)
    assert tuple(
        sum(
            frame.ownership is VisualFrameOwnership.CORE
            for frame in assignment.frames
        )
        for assignment in plan.assignments
    ) == (20, 20, 5)


def test_batch_results_merge_to_exact_parent_frame_order() -> None:
    """Owned observations reconstruct one complete parent result."""
    parent = _parent_assignment()
    plan = plan_visual_review_batches(parent)
    results = tuple(
        WindowVisualReviewResult(
            window_index=assignment.window_index,
            reviewer_input_digest=assignment.reviewer_input_digest,
            frame_observations=tuple(
                FrameVisualObservation(frame_id=frame.frame_id)
                for frame in assignment.frames
            ),
        )
        for assignment in plan.assignments
    )

    merged = merge_visual_review_batches(parent, plan, results)

    assert merged.window_index == parent.window_index
    assert merged.reviewer_input_digest == parent.reviewer_input_digest
    assert tuple(item.frame_id for item in merged.frame_observations) == tuple(
        frame.frame_id for frame in parent.frames
    )
