# Copyright 2026 Transcription Module contributors

"""Task 11 snapshots legacy persisted inputs before drafting or publication."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from transcription_module.asr import AsrRunArtifact, AsrSegment, AsrTranscript
from transcription_module.drafting import (
    DiarizationRunArtifact,
    DiarizationStatus,
    SpeakerEvidenceSegment,
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.window_evidence_errors import WindowEvidenceError

if TYPE_CHECKING:
    from pathlib import Path


def _legacy_run(tmp_path: Path) -> tuple[RunStore, RunId]:
    store = RunStore(tmp_path / "runs")
    run_id = RunId("window-fd-snapshot")
    source_url = YouTubeVideoUrl("https://youtube.test/window-fd-snapshot")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="audio.wav",
            duration_ms=1_000,
            frame_count=0,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(
                text="원본",
                language="ko",
                segments=(AsrSegment(start_ms=0, end_ms=1_000, speech="원본"),),
            ),
        ),
    )
    return store, run_id


def _hardlink_in_place(path: Path) -> None:
    original = path.with_name(f"{path.name}.original")
    _ = path.replace(original)
    os.link(original, path)


async def _empty_draft(data: WindowDraftInput) -> WindowDraft:
    return WindowDraft(
        window_index=data.window_index,
        cues=(),
        unresolved=(),
        evidence_reviewed=True,
    )


def test_hardlinked_authority_fails_before_callback(tmp_path: Path) -> None:
    """A multiply linked authority cannot reach a drafter callback."""
    store, run_id = _legacy_run(tmp_path)
    paths = store.paths(run_id)
    _hardlink_in_place(paths.asr_transcript)
    called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal called
        called = True
        return await _empty_draft(data)

    with pytest.raises(WindowEvidenceError, match="hard-linked"):
        _ = anyio.run(
            create_window_drafts,
            store,
            run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert not called
    assert not paths.window_drafts.exists()


def test_hardlinked_derived_artifact_fails_before_callback(tmp_path: Path) -> None:
    """A multiply linked derived diarization file grants no callback access."""
    store, run_id = _legacy_run(tmp_path)
    paths = store.paths(run_id)
    write_model_atomic(
        paths.diarization_artifact,
        DiarizationRunArtifact(
            run_id=run_id,
            status=DiarizationStatus.AVAILABLE,
            segments=(
                SpeakerEvidenceSegment(
                    start_tc_ms=0,
                    end_tc_ms=1_000,
                    cluster="cluster-a",
                ),
            ),
        ),
    )
    _hardlink_in_place(paths.diarization_artifact)
    called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal called
        called = True
        return await _empty_draft(data)

    with pytest.raises(WindowEvidenceError, match="hard-linked"):
        _ = anyio.run(
            create_window_drafts,
            store,
            run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert not called
    assert not paths.window_drafts.exists()


def test_replacement_during_callback_uses_snapshot_and_blocks_publication(
    tmp_path: Path,
) -> None:
    """A callback sees captured bytes, while the publication barrier rejects drift."""
    store, run_id = _legacy_run(tmp_path)
    paths = store.paths(run_id)
    observed_speech: list[str] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        observed_speech.extend(segment.speech for segment in data.asr_segments)
        replacement = paths.asr_transcript.with_suffix(".replacement")
        write_model_atomic(
            replacement,
            AsrRunArtifact(
                run_id=run_id,
                transcript=AsrTranscript(
                    text="교체 경로 내용",
                    language="ko",
                    segments=(
                        AsrSegment(
                            start_ms=0,
                            end_ms=1_000,
                            speech="교체 경로 내용",
                        ),
                    ),
                ),
            ),
        )
        _ = replacement.replace(paths.asr_transcript)
        return await _empty_draft(data)

    with pytest.raises(WindowEvidenceError, match="replaced"):
        _ = anyio.run(
            create_window_drafts,
            store,
            run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert observed_speech == ["원본"]
    assert not paths.window_drafts.exists()
