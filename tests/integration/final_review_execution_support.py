# Copyright 2026 Transcription Module contributors

"""Shared stored-run fixtures for final-review execution integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anyio

from transcription_module.asr import AsrRunArtifact, AsrSegment, AsrTranscript
from transcription_module.final_review_execution import (
    FinalReviewSegmentInput,
    FinalReviewSegmentReviewer,
    execute_final_review,
)
from transcription_module.final_review_models import (
    FinalReviewReceipt,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.media import (
    PreparedFrameManifest,
    PreparedMediaManifest,
    PreparedSubtitleTrack,
    SubtitleSource,
)
from transcription_module.models import (
    Cue,
    CueId,
    FrameId,
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


@dataclass(frozen=True, slots=True)
class RunFixture:
    """Stored evidence paths and identities for one test run."""

    store: RunStore
    run_id: RunId
    video: Path
    audio: Path
    vtt: Path


def create_run(tmp_path: Path, *, segment_count: int = 2) -> RunFixture:
    """Create a complete run with two stored frames per five-minute segment."""
    run_id = RunId("final-review-execution-run")
    store = RunStore(tmp_path / "runs")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtube.test/final-review"),
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
        )
    )
    duration_ms = Milliseconds((segment_count - 1) * 300_000 + 1_000)
    frames: list[PreparedFrameManifest] = []
    cues: list[Cue] = []
    asr_segments: list[AsrSegment] = []
    for index in range(segment_count):
        start = index * 300_000
        for offset in range(2):
            frame_index = index * 2 + offset
            frame_id = FrameId(f"frame-{frame_index:04d}")
            relative = Path("input/frames") / f"{frame_id}.png"
            frame_path = paths.run_dir / relative
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            _ = frame_path.write_bytes(f"stored frame {frame_index}".encode())
            frames.append(
                PreparedFrameManifest(
                    frame_id=frame_id,
                    frame_index=frame_index,
                    raw_pts=frame_index * 1001,
                    raw_pts_time=f"{(start + offset) / 1000:.3f}",
                    best_effort_timestamp=frame_index * 1001,
                    best_effort_timestamp_time=f"{(start + offset) / 1000:.3f}",
                    pts_ms=start + offset,
                    path=relative.as_posix(),
                )
            )
        cues.append(
            Cue(
                sequence=index + 1,
                cue_id=CueId(f"cue-{index:04d}"),
                start_tc_ms=Milliseconds(start),
                end_tc_ms=Milliseconds(start + 500),
                speech=f"발화 {index}",
                speaker="화자",
                uncertainty=Uncertainty.CERTAIN,
                screen_text=f"화면 문구 {index}",
            )
        )
        asr_segments.append(
            AsrSegment(
                start_ms=start,
                end_ms=start + 500,
                speech=f"ASR 발화 {index}",
            )
        )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            validation_status=ValidationStatus.PENDING,
            duration_ms=duration_ms,
            cues=tuple(cues),
            vision_evidence=(),
        )
    )
    input_dir = paths.run_dir / "input"
    video = input_dir / "source.mp4"
    audio = input_dir / "audio.wav"
    vtt = input_dir / "manual.ko.vtt"
    _ = video.write_bytes(b"source video")
    _ = audio.write_bytes(b"source audio")
    _ = vtt.write_text("WEBVTT\n\n00:00.000 --> 00:00.500\n화면 문구\n")
    video.chmod(0o640)
    audio.chmod(0o604)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtube.test/final-review"),
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            video_path=str(video),
            audio_path=str(audio),
            subtitle_tracks=(
                PreparedSubtitleTrack(
                    path=str(vtt),
                    language="ko",
                    source=SubtitleSource.MANUAL,
                ),
            ),
            duration_ms=duration_ms,
            frame_count=len(frames),
            frames=tuple(frames),
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(
                text=" ".join(segment.speech for segment in asr_segments),
                language="ko",
                segments=tuple(asr_segments),
            ),
        ),
    )
    return RunFixture(store=store, run_id=run_id, video=video, audio=audio, vtt=vtt)


def semantic_result(review_input: FinalReviewSegmentInput) -> ReviewSegmentResult:
    """Return complete observations accounting for every current screen text."""
    return ReviewSegmentResult(
        segment_id=review_input.segment.segment_id,
        frame_ids=review_input.segment.frame_ids,
        cue_ids=review_input.segment.cue_ids,
        frame_observations=tuple(
            FrameReviewObservation(
                frame_id=frame.frame_id,
                visible_screen_text=tuple(
                    cue.screen_text
                    for cue in review_input.cues
                    if cue.screen_text is not None
                ),
                compared_cue_ids=review_input.segment.cue_ids,
            )
            for frame in review_input.frames
        ),
    )


def execute(
    fixture: RunFixture,
    reviewer: FinalReviewSegmentReviewer,
) -> FinalReviewReceipt:
    """Execute the async runner through its synchronous test surface."""
    return anyio.run(
        execute_final_review,
        fixture.store,
        fixture.run_id,
        reviewer,
    )


__all__ = ["RunFixture", "create_run", "execute", "semantic_result"]
