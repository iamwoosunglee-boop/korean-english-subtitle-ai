# Copyright 2026 Transcription Module contributors

"""Exact ordered frame and cue coverage checks for final review."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._final_review import FinalReviewError
from transcription_module.vision import (
    AuditSegment,
    VisionContractError,
    assert_full_vision_coverage,
)

if TYPE_CHECKING:
    from transcription_module.final_review_models import (
        FinalReviewAssignment,
        FinalReviewResult,
    )
    from transcription_module.models import Transcript


def assert_coverage(
    assignment: FinalReviewAssignment,
    result: FinalReviewResult,
    transcript: Transcript,
) -> None:
    """Assert exact ordered assignment and full transcript coverage."""
    expected = tuple(
        (segment.segment_id, segment.frame_ids, segment.cue_ids)
        for segment in assignment.segments
    )
    actual = tuple(
        (segment.segment_id, segment.frame_ids, segment.cue_ids)
        for segment in result.segments
    )
    if actual != expected:
        msg = "final review coverage must exactly match assignment order"
        raise FinalReviewError(msg)
    if any(
        tuple(observation.frame_id for observation in segment.frame_observations)
        != segment.frame_ids
        for segment in result.segments
    ):
        msg = "final review frame observations must exactly match frame coverage"
        raise FinalReviewError(msg)
    if any(
        cue_id not in set(segment.cue_ids)
        for segment in result.segments
        for observation in segment.frame_observations
        for cue_id in observation.compared_cue_ids
    ):
        msg = "frame observation cue IDs must belong to their review segment"
        raise FinalReviewError(msg)
    if any(
        cue_id not in set(segment.cue_ids)
        for segment in result.segments
        for replacement in segment.replacements
        for cue_id in replacement.target_cue_ids
    ):
        msg = "replacement target cue IDs must belong to their review segment"
        raise FinalReviewError(msg)
    sequence_by_id = {
        cue_id: cue.sequence
        for cue, cue_id in zip(transcript.cues, assignment.cue_ids, strict=True)
    }
    try:
        assert_full_vision_coverage(
            tuple(
                AuditSegment(
                    start_tc_ms=segment.start_tc_ms,
                    end_tc_ms=segment.end_tc_ms,
                    owned_cue_sequences=tuple(
                        sequence_by_id[cue_id] for cue_id in segment.cue_ids
                    ),
                )
                for segment in assignment.segments
            ),
            duration_ms=transcript.duration_ms,
            cues=transcript.cues,
        )
    except (KeyError, VisionContractError) as error:
        msg = "final review coverage is incomplete"
        raise FinalReviewError(msg) from error


__all__ = ["assert_coverage"]
