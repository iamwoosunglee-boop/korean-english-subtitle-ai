# Copyright 2026 Transcription Module contributors

"""Fixed source and media fixtures for the canonical workflow proof."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

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

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_URL: Final = YouTubeVideoUrl("https://youtube.test/canonical-workflow")


def create_workflow_run(root: Path) -> tuple[RunStore, RunId]:
    """Create one isolated unresolved run with two referenced frames."""
    run_id = RunId("90000000-0000-4000-8000-000000000009")
    store = RunStore(root / "runs")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=SOURCE_URL,
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            context=EpisodeContext(
                raw_text="고정된 번역 맥락",
                characters=("화자-가", "화자-나"),
            ),
        )
    )
    paths = store.paths(run_id)
    frames: list[PreparedFrameManifest] = []
    values = (("frame-a", b"frame-a", 500), ("frame-b", b"frame-b", 1500))
    for index, (name, payload, pts_ms) in enumerate(values, start=1):
        path = paths.run_dir / "frames" / f"{name}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(payload)
        frames.append(
            PreparedFrameManifest(
                frame_id=FrameId(name),
                frame_index=index,
                raw_pts=index,
                raw_pts_time=f"{pts_ms / 1000:g}",
                best_effort_timestamp=index,
                best_effort_timestamp_time=f"{pts_ms / 1000:g}",
                pts_ms=Milliseconds(pts_ms),
                path=f"frames/{name}.jpg",
            )
        )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=SOURCE_URL,
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            video_path="input/source.mp4",
            audio_path="input/audio.wav",
            duration_ms=Milliseconds(3000),
            frame_count=2,
            frames=tuple(frames),
        ),
    )
    store.save_transcript(initial_transcript(run_id))
    return store, run_id


def initial_transcript(run_id: RunId) -> Transcript:
    """Return unresolved source with stable same-time distinct-speaker order."""
    return Transcript(
        run_id=run_id,
        workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(3000),
        cues=(
            Cue(
                sequence=1,
                cue_id=CueId("cue-unresolved"),
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(900),
                speech="미확정 발화",
                speaker="화자-가",
                uncertainty=Uncertainty.NEEDS_CONFIRMATION,
                vision_evidence_ids=(EvidenceId("vision-a"),),
            ),
            Cue(
                sequence=2,
                cue_id=CueId("cue-speaker-a"),
                start_tc_ms=Milliseconds(1000),
                end_tc_ms=Milliseconds(1800),
                speech="동시 발화",
                speaker="화자-가",
                uncertainty=Uncertainty.CERTAIN,
                vision_evidence_ids=(EvidenceId("vision-a"),),
            ),
            Cue(
                sequence=3,
                cue_id=CueId("cue-speaker-b"),
                start_tc_ms=Milliseconds(1000),
                end_tc_ms=Milliseconds(1800),
                speech="동시 발화",
                speaker="화자-나",
                uncertainty=Uncertainty.CERTAIN,
                vision_evidence_ids=(EvidenceId("vision-b"),),
            ),
        ),
        vision_evidence=(
            VisionEvidence(
                evidence_id=EvidenceId("vision-a"),
                frame_ids=(FrameId("frame-a"),),
                scene="장면 A",
            ),
            VisionEvidence(
                evidence_id=EvidenceId("vision-b"),
                frame_ids=(FrameId("frame-b"),),
                scene="장면 B",
            ),
        ),
    )
