# Copyright 2026 Transcription Module contributors

"""Deterministic visual-review providers for integration tests."""

from __future__ import annotations

from typing import Final

from transcription_module.drafting import (
    FrameVisualObservation,
    VisualReviewerIdentity,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
)

TEST_VISUAL_REVIEWER_IDENTITY: Final = VisualReviewerIdentity(
    prompt_version="test-v1",
    prompt_digest="1" * 64,
    model_id="test-model",
    settings_digest="2" * 64,
)


def empty_visual_review(
    assignment: WindowVisualReviewAssignment,
) -> WindowVisualReviewResult:
    """Observe every assigned frame without proposing visible text."""
    return WindowVisualReviewResult(
        window_index=assignment.window_index,
        reviewer_input_digest=assignment.reviewer_input_digest,
        frame_observations=tuple(
            FrameVisualObservation(frame_id=frame.frame_id)
            for frame in assignment.frames
        ),
    )


class EmptyVisualReviewer:
    """Return exact empty observations for every assigned frame."""

    @property
    def identity(self) -> VisualReviewerIdentity:
        """Return the fixed test reviewer identity."""
        return TEST_VISUAL_REVIEWER_IDENTITY

    async def __call__(
        self,
        assignment: WindowVisualReviewAssignment,
    ) -> WindowVisualReviewResult:
        """Return complete empty observations for one assignment."""
        return empty_visual_review(assignment)


__all__ = [
    "TEST_VISUAL_REVIEWER_IDENTITY",
    "EmptyVisualReviewer",
    "empty_visual_review",
]
