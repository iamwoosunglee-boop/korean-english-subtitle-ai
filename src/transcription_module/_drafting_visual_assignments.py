# Copyright 2026 Transcription Module contributors

"""Build and verify immutable visual assignments for transcription windows."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from transcription_module._drafting_models import WindowDraftInput
from transcription_module.visual_review import (
    VisualFrameOwnership,
    VisualReviewerIdentity,
    VisualReviewFrame,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    visual_review_assignment_digest,
)
from transcription_module.visual_review_validation import (
    validate_visual_review_result,
)

if TYPE_CHECKING:
    from transcription_module._drafting_window_evidence import WindowEvidenceSnapshot
    from transcription_module.window_snapshot_models import FileSnapshot


def base_window_inputs(
    evidence: WindowEvidenceSnapshot,
) -> tuple[WindowDraftInput, ...]:
    """Project complete bounded inputs from one opened evidence snapshot."""
    return tuple(
        _window_input(index, start, evidence)
        for index, start in enumerate(range(0, evidence.media.duration_ms, 300_000))
    )


def validate_reusable_visual_artifact(
    artifact: WindowVisualReviewArtifact,
) -> None:
    """Reject incomplete, foreign, or invalid persisted review pairs."""
    assignment_indexes = tuple(item.window_index for item in artifact.assignments)
    result_indexes = tuple(item.window_index for item in artifact.results)
    if (
        not assignment_indexes
        or assignment_indexes != tuple(sorted(set(assignment_indexes)))
        or result_indexes != assignment_indexes
    ):
        message = "reusable visual review artifact has incomplete window pairs"
        raise ValueError(message)
    for assignment, result in zip(
        artifact.assignments,
        artifact.results,
        strict=True,
    ):
        if assignment.run_id != artifact.run_id:
            message = "reusable visual review assignment belongs to another run"
            raise ValueError(message)
        validate_visual_review_result(assignment, result)


def assignment_matches_current(
    run_dir: Path,
    data: WindowDraftInput,
    assignment: WindowVisualReviewAssignment,
    selected_reviewer: VisualReviewerIdentity,
    snapshots: tuple[FileSnapshot, ...],
) -> bool:
    """Compare one persisted assignment with freshly opened frame bytes."""
    frame_hashes = {snapshot.label: snapshot.sha256 for snapshot in snapshots}
    expected = visual_assignment(data, selected_reviewer, frame_hashes)
    if assignment.model_dump(
        exclude={"frames", "reviewer_input_digest"}
    ) != expected.model_dump(exclude={"frames", "reviewer_input_digest"}):
        return False
    if len(assignment.frames) != len(expected.frames):
        return False
    for actual, current in zip(
        assignment.frames,
        expected.frames,
        strict=True,
    ):
        if actual.model_dump(exclude={"path"}) != current.model_dump(exclude={"path"}):
            return False
        if not _path_matches_digest(run_dir, actual.path, current.sha256):
            return False
    return True


def visual_assignment(
    data: WindowDraftInput,
    reviewer: VisualReviewerIdentity,
    frame_hashes: dict[str, str],
) -> WindowVisualReviewAssignment:
    """Bind one window input and current frame hashes to a reviewer identity."""
    frames = tuple(
        VisualReviewFrame(
            frame_id=frame.frame_id,
            pts_ms=frame.pts_ms,
            path=frame.path,
            sha256=frame_hashes[f"frame:{frame.frame_id}"],
            ownership=(
                VisualFrameOwnership.CORE
                if data.core_start_ms <= frame.pts_ms < data.core_end_ms
                else VisualFrameOwnership.CONTEXT
            ),
        )
        for frame in data.frames
    )
    unbound = WindowVisualReviewAssignment.model_construct(
        run_id=data.run_id,
        window_index=data.window_index,
        core_start_ms=data.core_start_ms,
        core_end_ms=data.core_end_ms,
        context_start_ms=data.context_start_ms,
        context_end_ms=data.context_end_ms,
        episode_context=data.episode_context,
        reviewer=reviewer,
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


def _candidate_frame_paths(run_dir: Path, value: str) -> tuple[Path, ...]:
    path = Path(value)
    candidates = (path,) if path.is_absolute() else (Path.cwd() / path, run_dir / path)
    return tuple(candidate.resolve() for candidate in candidates if candidate.exists())


def _path_matches_digest(run_dir: Path, value: str, digest: str) -> bool:
    return any(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path in _candidate_frame_paths(run_dir, value)
    )


def _window_input(
    index: int,
    core_start: int,
    evidence: WindowEvidenceSnapshot,
) -> WindowDraftInput:
    media = evidence.media
    core_end = min(core_start + 300_000, media.duration_ms)
    context_start = max(0, core_start - 15_000)
    context_end = min(media.duration_ms, core_end + 15_000)
    return WindowDraftInput(
        window_index=index,
        run_id=media.run_id,
        core_start_ms=core_start,
        core_end_ms=core_end,
        context_start_ms=context_start,
        context_end_ms=context_end,
        asr_segments=tuple(
            item
            for item in evidence.asr_segments
            if item.start_ms < context_end and item.end_ms > context_start
        ),
        subtitle_tracks=evidence.subtitle_tracks,
        frames=tuple(
            item for item in media.frames if context_start <= item.pts_ms < context_end
        ),
        speaker_segments=tuple(
            item
            for item in evidence.speaker_segments
            if item.start_tc_ms < context_end and item.end_tc_ms > context_start
        ),
        vision_artifact_path=evidence.vision_artifact_path,
        episode_context=evidence.episode_context,
        era=evidence.era,
        policy_digest=evidence.policy_digest,
        inventory_digest=evidence.inventory_digest,
        source_digest=evidence.source_digest,
        subtitle_cues=tuple(
            item
            for item in evidence.subtitle_cues
            if item.start_tc_ms < context_end and item.end_tc_ms > context_start
        ),
        vad_intervals=tuple(
            item
            for item in evidence.vad_intervals
            if item.start_tc_ms < context_end and item.end_tc_ms > context_start
        ),
        verified_turns=tuple(
            item
            for item in evidence.verified_turns
            if item.start_tc_ms < context_end and item.end_tc_ms > context_start
        ),
    )
