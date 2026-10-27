# Copyright 2026 Transcription Module contributors

"""Deterministic finalized-run fixture for translation export tests."""

from pathlib import Path
from typing import Final

from transcription_module.final_review import (
    apply_final_review,
    prepare_final_review,
)
from transcription_module.final_review_models import (
    FinalReviewResult,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.media import PreparedFrameManifest, PreparedMediaManifest
from transcription_module.models import (
    Cue,
    CueId,
    EpisodeContext,
    EvidenceId,
    FrameId,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    VisionEvidence,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.validation import validate_run

FRAME_PTS_MS: Final = 500
CONTEXT_FRAME_PTS_MS: Final = 1_500
CONTEXT_FRAME_BYTES: Final = b"episode context frame"


def finalized_run_fixture(tmp_path: Path) -> tuple[RunStore, RunId, Path, bytes]:
    """Create one explicitly approved and validated export run."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("translation-export-fixture")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=export-fixture")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            context=EpisodeContext(
                raw_text="은하섬 멤버들이 게스트를 맞이하는 공연 회차",
                characters=("한지아", "박도현"),
            ),
        )
    )
    frame_bytes = b"current frame bytes"
    frame_path = paths.run_dir / "input" / "frames" / "f000001.jpg"
    frame_path.parent.mkdir(parents=True)
    _ = frame_path.write_bytes(frame_bytes)
    context_frame_path = paths.run_dir / "input" / "frames" / "f000002.jpg"
    _ = context_frame_path.write_bytes(CONTEXT_FRAME_BYTES)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="input/source.mp4",
            audio_path="input/audio.wav",
            duration_ms=Milliseconds(4_000),
            frame_count=2,
            frames=(
                PreparedFrameManifest(
                    frame_id=FrameId("f000001"),
                    frame_index=0,
                    raw_pts=15,
                    raw_pts_time="0.500000",
                    best_effort_timestamp=15,
                    best_effort_timestamp_time="0.500000",
                    pts_ms=Milliseconds(FRAME_PTS_MS),
                    path="input/frames/f000001.jpg",
                ),
                PreparedFrameManifest(
                    frame_id=FrameId("f000002"),
                    frame_index=1,
                    raw_pts=45,
                    raw_pts_time="1.500000",
                    best_effort_timestamp=45,
                    best_effort_timestamp_time="1.500000",
                    pts_ms=Milliseconds(CONTEXT_FRAME_PTS_MS),
                    path="input/frames/f000002.jpg",
                ),
            ),
        ),
    )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            validation_status=ValidationStatus.PENDING,
            duration_ms=Milliseconds(4_000),
            cues=(
                Cue(
                    sequence=1,
                    cue_id=CueId("cue-unknown"),
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="어서 오세요.",
                    speaker="미확인 화자",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    cue_id=CueId("cue-multiple"),
                    start_tc_ms=Milliseconds(1_000),
                    end_tc_ms=Milliseconds(2_000),
                    speech="환영합니다.",
                    speaker="한지아|박도현",
                    uncertainty=Uncertainty.CERTAIN,
                    screen_text="환영",
                    vision_evidence_ids=(EvidenceId("vision-1"),),
                ),
            ),
            vision_evidence=(
                VisionEvidence(
                    evidence_id=EvidenceId("vision-1"),
                    frame_ids=(FrameId("f000001"),),
                    scene="두 사람이 함께 인사한다.",
                    rough_lower_third="환영",
                ),
            ),
        )
    )
    assignment = prepare_final_review(store, run_id)
    result = FinalReviewResult(
        run_id=run_id,
        assignment_digest=assignment.assignment_digest,
        segments=tuple(
            ReviewSegmentResult(
                segment_id=segment.segment_id,
                frame_ids=segment.frame_ids,
                cue_ids=segment.cue_ids,
                frame_observations=tuple(
                    FrameReviewObservation(
                        frame_id=frame_id,
                        visible_screen_text=(),
                        compared_cue_ids=segment.cue_ids,
                    )
                    for frame_id in segment.frame_ids
                ),
            )
            for segment in assignment.segments
        ),
    )
    _ = apply_final_review(store, run_id, result)
    _ = validate_run(store, run_id)
    return store, run_id, frame_path, frame_bytes
