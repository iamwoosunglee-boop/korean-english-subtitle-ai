# Copyright 2026 Transcription Module contributors

"""Existing protected evidence can be revalidated without recomputation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from transcription_module.asr import AsrRunArtifact, AsrSegment, AsrTranscript
from transcription_module.drafting import (
    DiarizationRunArtifact,
    DiarizationStatus,
    SpeakerEvidenceSegment,
)
from transcription_module.evidence_resume import (
    EvidenceResumeError,
    validate_resumable_evidence,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.protected import ProtectedRunArtifact
from transcription_module.storage import RunStore, write_model_atomic

if TYPE_CHECKING:
    from pathlib import Path

RUN_ID = RunId("64ba00b6-045d-41f7-8f4a-8aa5f685b7f0")
OTHER_RUN_ID = RunId("00000000-0000-0000-0000-000000000001")
DURATION_MS = 2_000


def _create_resumable_run(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path / "runs")
    paths = store.create(
        RunState(
            run_id=RUN_ID,
            source_url=YouTubeVideoUrl("https://youtu.be/BBBBBBBBBBB"),
            workflow_status=WorkflowStatus.PROCESSING,
            validation_status=ValidationStatus.PENDING,
            context=EpisodeContext(raw_text="이전 루미 편의 완성본"),
        )
    )
    input_dir = paths.run_dir / "input"
    input_dir.mkdir()
    video_path = input_dir / "source.mp4"
    audio_path = input_dir / "audio.wav"
    _ = video_path.write_bytes(b"video")
    _ = audio_path.write_bytes(b"audio")
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=RUN_ID,
            source_url=YouTubeVideoUrl("https://youtu.be/BBBBBBBBBBB"),
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(video_path),
            audio_path=str(audio_path),
            duration_ms=DURATION_MS,
            frame_count=0,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=RUN_ID,
            transcript=AsrTranscript(
                text="안녕하세요",
                language="ko",
                segments=(
                    AsrSegment(
                        start_ms=100,
                        end_ms=1_000,
                        speech="안녕하세요",
                    ),
                ),
            ),
        ),
    )
    write_model_atomic(
        paths.diarization_artifact,
        DiarizationRunArtifact(
            run_id=RUN_ID,
            status=DiarizationStatus.AVAILABLE,
            segments=(
                SpeakerEvidenceSegment(
                    start_tc_ms=100,
                    end_tc_ms=1_000,
                    cluster="SPEAKER_00",
                ),
            ),
        ),
    )
    write_model_atomic(
        paths.run_dir / "protected-run.json",
        ProtectedRunArtifact(
            start_fingerprint="before",
            end_fingerprint="after",
            source_changed=True,
            status="failed",
            error="source changed during protected execution",
        ),
    )
    return store


def test_validates_existing_evidence_without_recomputing(tmp_path: Path) -> None:
    """A source-change failure may reuse complete, matching evidence."""
    store = _create_resumable_run(tmp_path)

    summary = validate_resumable_evidence(RUN_ID, store)

    assert summary.run_id == RUN_ID
    assert summary.duration_ms == DURATION_MS
    assert summary.asr_segment_count == 1
    assert summary.diarization_segment_count == 1


def test_rejects_evidence_from_another_run(tmp_path: Path) -> None:
    """Every persisted evidence artifact remains bound to one run ID."""
    store = _create_resumable_run(tmp_path)
    paths = store.paths(RUN_ID)
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=OTHER_RUN_ID,
            transcript=AsrTranscript(text="", language="ko", segments=()),
        ),
    )

    with pytest.raises(EvidenceResumeError, match="run ID"):
        _ = validate_resumable_evidence(RUN_ID, store)


def test_rejects_missing_referenced_media(tmp_path: Path) -> None:
    """A manifest alone cannot authorize reuse of missing source evidence."""
    store = _create_resumable_run(tmp_path)
    (store.paths(RUN_ID).run_dir / "input" / "audio.wav").unlink()

    with pytest.raises(EvidenceResumeError, match="referenced media"):
        _ = validate_resumable_evidence(RUN_ID, store)


def test_rejects_run_that_already_started_drafting(tmp_path: Path) -> None:
    """Resume evidence never overwrites downstream drafting artifacts."""
    store = _create_resumable_run(tmp_path)
    _ = store.paths(RUN_ID).window_drafts.write_text("{}", encoding="utf-8")

    with pytest.raises(EvidenceResumeError, match="downstream"):
        _ = validate_resumable_evidence(RUN_ID, store)
