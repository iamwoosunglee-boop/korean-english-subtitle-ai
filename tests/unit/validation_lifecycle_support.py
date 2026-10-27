# Copyright 2026 Transcription Module contributors

"""Deterministic fixtures for validation lifecycle tests."""

from pathlib import Path

from transcription_module.final_review import (
    apply_final_review,
    prepare_final_review,
)
from transcription_module.final_review_models import (
    FinalReviewResult,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    Cue,
    CueId,
    EpisodeContext,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic


def transcript_fixture(run_id: RunId, speech: str) -> Transcript:
    """Build one fixed single-cue transcript."""
    return Transcript(
        run_id=run_id,
        workflow_status=WorkflowStatus.PROCESSING,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(2_000),
        cues=(
            Cue(
                sequence=1,
                cue_id=CueId("cue-1"),
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(1_000),
                speech=speech,
                speaker="fixture speaker",
                uncertainty=Uncertainty.CERTAIN,
            ),
        ),
        vision_evidence=(),
    )


def validation_store_fixture(tmp_path: Path) -> tuple[RunStore, RunId]:
    """Create one fixed validation run and media manifest."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000009")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=validation-fixture"
            ),
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="검증 fixture 문맥"),
        )
    )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=validation-fixture"
            ),
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="video.mp4",
            audio_path="audio.wav",
            duration_ms=Milliseconds(2_000),
            frame_count=0,
        ),
    )
    return store, run_id


def approve_final_review_fixture(store: RunStore, run_id: RunId) -> None:
    """Persist explicit approval for the current transcript."""
    transcript = store.load_transcript(run_id)
    if any(cue.cue_id is None for cue in transcript.cues):
        transcript = transcript.model_copy(
            update={
                "cues": tuple(
                    cue.model_copy(update={"cue_id": CueId(f"cue-{cue.sequence}")})
                    for cue in transcript.cues
                )
            }
        )
        store.save_transcript(transcript)
    paths = store.paths(run_id)
    manifest = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    write_model_atomic(
        paths.media_manifest,
        manifest.model_copy(update={"duration_ms": transcript.duration_ms}),
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
