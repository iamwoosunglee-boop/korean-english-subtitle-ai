# Copyright 2026 Transcription Module contributors

"""Generic visual-review prompt adapter preserves typed runtime input."""

from __future__ import annotations

from transcription_module.models import EpisodeContext, RunId
from transcription_module.visual_review import (
    WindowVisualReviewAssignment,
    visual_review_assignment_digest,
)
from transcription_module.visual_review_prompt import (
    build_visual_review_prompt,
    selected_visual_reviewer_identity,
)


def _assignment() -> WindowVisualReviewAssignment:
    unbound = WindowVisualReviewAssignment.model_construct(
        run_id=RunId("run-sentinel"),
        window_index=0,
        core_start_ms=0,
        core_end_ms=1_000,
        context_start_ms=0,
        context_end_ms=1_000,
        episode_context=EpisodeContext(raw_text="sentinel episode context"),
        reviewer=selected_visual_reviewer_identity(
            model_id="model-sentinel",
            settings_digest="2" * 64,
        ),
        frames=(),
        reviewer_input_digest="0" * 64,
    )
    return WindowVisualReviewAssignment.model_validate(
        unbound.model_copy(
            update={
                "reviewer_input_digest": visual_review_assignment_digest(unbound),
            }
        ).model_dump()
    )


def test_prompt_request_preserves_exact_typed_assignment() -> None:
    """Runtime episode and frame data remain outside the generic template."""
    assignment = _assignment()

    request = build_visual_review_prompt(assignment)

    assert request.assignment == assignment
    assert request.template_digest == assignment.reviewer.prompt_digest
    assert request.template_version == assignment.reviewer.prompt_version
