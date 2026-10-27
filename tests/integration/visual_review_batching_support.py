# Copyright 2026 Transcription Module contributors

"""Persisted fixtures for visual-review batching integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from transcription_module.asr import AsrRunArtifact, AsrTranscript
from transcription_module.drafting import WindowDraft, WindowDraftInput
from transcription_module.media import (
    PreparedFrameManifest,
    PreparedMediaManifest,
)
from transcription_module.models import (
    EpisodeContext,
    FrameId,
    RunId,
    RunState,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.visual_review import (
    CandidateDispositionKind,
    FrameVisualObservation,
    RawCandidateDisposition,
    RawVisualCandidate,
    RawVisualRole,
    VisualReviewerIdentity,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.storage_paths import RunPaths

FRAME_COUNT = 41
OWNED_FRAME_COUNT = 20
MAX_BATCH_FRAMES = 24
EXPECTED_BATCH_COUNT = 3
UNEXPECTED_PROVIDER_CALL = "persisted visual bundle must be reused"


class RecordingVisualReviewer:
    """Record each provider assignment and return complete observations."""

    def __init__(self) -> None:
        """Start without observed provider assignments."""
        self.assignments: list[WindowVisualReviewAssignment] = []

    @property
    def identity(self) -> VisualReviewerIdentity:
        """Return one stable reviewer identity for the fixture."""
        return VisualReviewerIdentity(
            prompt_version="test-batched-review",
            prompt_digest="0" * 64,
            model_id="test-reviewer",
            settings_digest="1" * 64,
        )

    async def __call__(
        self,
        assignment: WindowVisualReviewAssignment,
    ) -> WindowVisualReviewResult:
        """Record one assignment and observe every supplied frame."""
        self.assignments.append(assignment)
        candidates = ()
        if len(self.assignments) == 1 and len(assignment.frames) > OWNED_FRAME_COUNT:
            assert assignment.owned_frame_ids == tuple(
                frame.frame_id for frame in assignment.frames[:OWNED_FRAME_COUNT]
            )
            candidates = (
                RawVisualCandidate(
                    candidate_id="boundary-candidate",
                    complete_text="경계 후보",
                    proposed_role=RawVisualRole.SPEECH_CAPTION,
                    supporting_frame_ids=(
                        assignment.frames[OWNED_FRAME_COUNT - 1].frame_id,
                        assignment.frames[OWNED_FRAME_COUNT].frame_id,
                    ),
                ),
            )
        return WindowVisualReviewResult(
            window_index=assignment.window_index,
            reviewer_input_digest=assignment.reviewer_input_digest,
            frame_observations=tuple(
                FrameVisualObservation(frame_id=frame.frame_id)
                for frame in assignment.frames
            ),
            candidates=candidates,
        )


class ReuseOnlyVisualReviewer(RecordingVisualReviewer):
    """Expose the same identity but fail if recovery calls the provider."""

    @override
    async def __call__(
        self,
        assignment: WindowVisualReviewAssignment,
    ) -> WindowVisualReviewResult:
        """Reject any provider call after the atomic bundle exists."""
        del assignment
        raise AssertionError(UNEXPECTED_PROVIDER_CALL)


class RetainingDrafter:
    """Return an empty legacy draft while consuming every raw candidate."""

    def __init__(self, paths: RunPaths) -> None:
        """Bind the run paths whose publication must precede drafting."""
        self.paths: RunPaths = paths

    async def __call__(self, data: WindowDraftInput) -> WindowDraft:
        """Require the bundle and retain every candidate as evidence."""
        assert (self.paths.run_dir / "window-visual-batches.json").is_file()
        assert self.paths.window_visual_review.is_file()
        return WindowDraft(
            window_index=data.window_index,
            cues=(),
            unresolved=(),
            evidence_reviewed=True,
            candidate_dispositions=tuple(
                RawCandidateDisposition(
                    candidate_id=candidate.candidate_id,
                    kind=CandidateDispositionKind.RETAINED_EVIDENCE,
                )
                for candidate in (
                    () if data.visual_review is None else data.visual_review.candidates
                )
            ),
        )


def prepared_store(
    tmp_path: Path,
) -> tuple[
    RunStore,
    RunId,
    RunPaths,
    tuple[PreparedFrameManifest, ...],
]:
    """Persist one real run snapshot with more than two provider batches."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000155")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=batch-fixture")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    frames = tuple(
        PreparedFrameManifest(
            frame_id=FrameId(f"frame-{index:03}"),
            frame_index=index,
            raw_pts=index * 500,
            raw_pts_time=f"{index / 2:.3f}",
            best_effort_timestamp=index * 500,
            best_effort_timestamp_time=f"{index / 2:.3f}",
            pts_ms=index * 500,
            path=f"input/frames/frame-{index:03}.jpg",
        )
        for index in range(FRAME_COUNT)
    )
    for frame in frames:
        frame_path = paths.run_dir / frame.path
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        _ = frame_path.write_bytes(f"frame-{frame.frame_index}".encode())
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="audio.wav",
            duration_ms=20_500,
            frame_count=len(frames),
            frames=frames,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(text="", language="ko", segments=()),
        ),
    )
    return store, run_id, paths, frames


__all__ = [
    "EXPECTED_BATCH_COUNT",
    "FRAME_COUNT",
    "MAX_BATCH_FRAMES",
    "OWNED_FRAME_COUNT",
    "RecordingVisualReviewer",
    "RetainingDrafter",
    "ReuseOnlyVisualReviewer",
    "prepared_store",
]
