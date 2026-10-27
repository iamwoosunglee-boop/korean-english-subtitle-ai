# Copyright 2026 Transcription Module contributors

"""Adversarial integration contract for final-review execution."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from tests.integration.final_review_execution_support import (
    create_run,
    execute,
    semantic_result,
)
from transcription_module.final_review import FinalReviewError
from transcription_module.final_review_execution import (
    FinalReviewExecutionError,
    FinalReviewSegmentInput,
)
from transcription_module.models import CueId

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.final_review_models import ReviewSegmentResult


@pytest.mark.parametrize("reviewer_fails", [False, True], ids=("success", "failure"))
def test_execute_final_review_protects_source_media_and_restores_exact_modes(
    tmp_path: Path,
    reviewer_fails: bool,  # noqa: FBT001
) -> None:
    """Deny source reads during callbacks and restore modes on every exit."""
    fixture = create_run(tmp_path, segment_count=1)
    video_mode = stat.S_IMODE(fixture.video.stat().st_mode)
    audio_mode = stat.S_IMODE(fixture.audio.stat().st_mode)

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        assert stat.S_IMODE(fixture.video.stat().st_mode) == 0
        assert stat.S_IMODE(fixture.audio.stat().st_mode) == 0
        with pytest.raises(PermissionError):
            _ = fixture.video.read_bytes()
        with pytest.raises(PermissionError):
            _ = fixture.audio.read_bytes()
        if reviewer_fails:
            message = "reviewer failed"
            raise RuntimeError(message)
        return semantic_result(data)

    if reviewer_fails:
        with pytest.raises(FinalReviewExecutionError, match="reviewer callback failed"):
            _ = execute(fixture, reviewer)
    else:
        _ = execute(fixture, reviewer)

    assert stat.S_IMODE(fixture.video.stat().st_mode) == video_mode
    assert stat.S_IMODE(fixture.audio.stat().st_mode) == audio_mode


@pytest.mark.parametrize(
    "malformation",
    ["missing", "reordered", "foreign", "id-only", "semantic-empty"],
)
def test_execute_final_review_rejects_invalid_reviewer_output_without_mutation(
    tmp_path: Path,
    malformation: str,
) -> None:
    """Reject inexact, foreign, ID-only, and semantically empty outputs."""
    fixture = create_run(tmp_path, segment_count=1)
    paths = fixture.store.paths(fixture.run_id)
    transcript_before = paths.transcript.read_bytes()

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        valid = semantic_result(data)
        if malformation == "missing":
            return valid.model_copy(update={"frame_ids": (), "frame_observations": ()})
        if malformation == "reordered":
            return valid.model_copy(
                update={
                    "frame_ids": tuple(reversed(valid.frame_ids)),
                    "frame_observations": tuple(reversed(valid.frame_observations)),
                }
            )
        if malformation == "foreign":
            foreign = valid.frame_observations[0].model_copy(
                update={"compared_cue_ids": (CueId("foreign-cue"),)}
            )
            return valid.model_copy(update={"frame_observations": (foreign,)})
        if malformation == "id-only":
            return valid.model_copy(update={"frame_observations": ()})
        empty = valid.frame_observations[0].model_copy(
            update={"visible_screen_text": ()}
        )
        return valid.model_copy(update={"frame_observations": (empty,)})

    with pytest.raises(FinalReviewExecutionError):
        _ = execute(fixture, reviewer)

    assert paths.transcript.read_bytes() == transcript_before
    assert not paths.final_review_result.exists()
    assert not paths.final_review_artifact.exists()


def test_failed_rereview_preserves_current_result_and_receipt(tmp_path: Path) -> None:
    """A failed replacement review cannot erase the last valid publication."""
    fixture = create_run(tmp_path, segment_count=1)
    paths = fixture.store.paths(fixture.run_id)

    async def accepted(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        return semantic_result(data)

    _ = execute(fixture, accepted)
    transcript_before = paths.transcript.read_bytes()
    result_before = paths.final_review_result.read_bytes()
    receipt_before = paths.final_review_artifact.read_bytes()

    async def id_only(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        return semantic_result(data).model_copy(update={"frame_observations": ()})

    with pytest.raises(FinalReviewExecutionError):
        _ = execute(fixture, id_only)

    assert paths.transcript.read_bytes() == transcript_before
    assert paths.final_review_result.read_bytes() == result_before
    assert paths.final_review_artifact.read_bytes() == receipt_before


def test_execute_final_review_rejects_stale_mutation_between_prepare_and_apply(
    tmp_path: Path,
) -> None:
    """Let apply rebuild the digest and reject evidence changed during review."""
    fixture = create_run(tmp_path, segment_count=1)
    paths = fixture.store.paths(fixture.run_id)
    transcript_before = paths.transcript.read_bytes()

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        frame = paths.run_dir / data.frames[0].path
        _ = frame.write_bytes(b"mutated after assignment preparation")
        return semantic_result(data)

    with pytest.raises(FinalReviewError, match="stale"):
        _ = execute(fixture, reviewer)

    assert paths.transcript.read_bytes() == transcript_before
    assert not paths.final_review_result.exists()
    assert not paths.final_review_artifact.exists()
