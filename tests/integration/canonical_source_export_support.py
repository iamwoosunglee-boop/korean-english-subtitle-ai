# Copyright 2026 Transcription Module contributors

"""Deterministic canonical source export fixtures."""

from pathlib import Path

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


def canonical_inputs(
    tmp_path: Path,
) -> tuple[RunStore, RunId, RunState, Transcript, bytes]:
    """Create one fixed finalized run with bound frame evidence."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("11111111-1111-4111-8111-111111111111")
    source_url = YouTubeVideoUrl("https://youtu.be/canonical-v2")
    store.save_state(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
        )
    )
    frame_bytes = b"\x89PNG\r\n\x1a\ncanonical-frame"
    paths = store.paths(run_id)
    frame_path = paths.run_dir / "frames" / "frame-000001.png"
    frame_path.parent.mkdir(parents=True)
    _ = frame_path.write_bytes(frame_bytes)
    frame_id = FrameId("frame-000001")
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="input/source.mp4",
            audio_path="input/audio.wav",
            duration_ms=3000,
            frame_count=1,
            frames=(
                PreparedFrameManifest(
                    frame_id=frame_id,
                    frame_index=1,
                    raw_pts=90000,
                    raw_pts_time="1.000000",
                    best_effort_timestamp=90000,
                    best_effort_timestamp_time="1.000000",
                    pts_ms=1000,
                    path="frames/frame-000001.png",
                ),
            ),
        ),
    )
    state = RunState(
        run_id=run_id,
        source_url=source_url,
        workflow_status=WorkflowStatus.FINALIZED,
        validation_status=ValidationStatus.VALID,
        context=EpisodeContext(
            raw_text="루미와 日本語, 中文 맥락",
            characters=(" 루미 ", "한지아", "루미", ""),
        ),
    )
    evidence_id = EvidenceId("vision-1")
    transcript = Transcript(
        run_id=run_id,
        workflow_status=WorkflowStatus.FINALIZED,
        validation_status=ValidationStatus.VALID,
        duration_ms=Milliseconds(3000),
        cues=(
            Cue(
                sequence=1,
                cue_id=CueId("cue-1"),
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(1200),
                speech="첫 줄\n둘째 줄 日本語 中文",
                speaker="루미|한지아",
                uncertainty=Uncertainty.CERTAIN,
                screen_text="화면 자막",
                vision_evidence_ids=(evidence_id,),
            ),
            Cue(
                sequence=2,
                cue_id=CueId("cue-2"),
                start_tc_ms=Milliseconds(1300),
                end_tc_ms=Milliseconds(2500),
                speech="마지막 문장",
                speaker="한지아",
                uncertainty=Uncertainty.CERTAIN,
                screen_text=None,
                vision_evidence_ids=(evidence_id,),
            ),
        ),
        vision_evidence=(
            VisionEvidence(
                evidence_id=evidence_id,
                frame_ids=(frame_id,),
                scene="토크라운지",
            ),
        ),
    )
    return store, run_id, state, transcript, frame_bytes
