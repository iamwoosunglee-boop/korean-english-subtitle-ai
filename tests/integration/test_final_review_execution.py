# Copyright 2026 Transcription Module contributors

"""Happy-path integration contract for bounded final-review execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from tests.integration.final_review_execution_support import (
    create_run,
    execute,
    semantic_result,
)
from transcription_module.final_review_execution import (
    FinalReviewSegmentInput,
    execute_final_review,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import write_model_atomic

if TYPE_CHECKING:
    import pytest

    from transcription_module.final_review_models import (
        FinalReviewReceipt,
        ReviewSegmentResult,
    )

_MAX_REVIEWERS = 4
_EXPECTED_EVIDENCE_INPUTS = 2
_EXPECTED_SEGMENTS = 5


def test_execute_final_review_supplies_complete_semantic_evidence_and_applies_atomically(  # noqa: E501
    tmp_path: Path,
) -> None:
    """Supply complete segment evidence and publish only after all reviews."""
    fixture = create_run(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    before = paths.transcript.read_bytes()
    inputs: list[FinalReviewSegmentInput] = []

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        inputs.append(data)
        assert paths.transcript.read_bytes() == before
        assert not paths.final_review_result.exists()
        assert not paths.final_review_artifact.exists()
        assert data.purpose
        assert "화면" in data.purpose
        assert data.failure_context
        assert "누락" in data.failure_context
        assert {"id-list", "contact-sheet", "self-report"}.issubset(
            data.forbidden_proxy_metrics
        )
        assert data.assignment_digest
        assert all(not Path(frame.path).is_absolute() for frame in data.frames)
        assert all((data.run_dir / frame.path).is_file() for frame in data.frames)
        assert tuple(frame.frame_id for frame in data.frames) == data.segment.frame_ids
        assert tuple(cue.cue_id for cue in data.cues) == data.segment.cue_ids
        assert data.asr_segments
        assert data.korean_vtt_evidence
        assert data.korean_vtt_evidence[0].path == "input/manual.ko.vtt"
        assert "WEBVTT" in data.korean_vtt_evidence[0].contents
        serialized = data.model_dump_json()
        assert str(fixture.video) not in serialized
        assert str(fixture.audio) not in serialized
        return semantic_result(data)

    receipt = execute(fixture, reviewer)

    assert len(inputs) == _EXPECTED_EVIDENCE_INPUTS
    assignment = fixture.store.load_final_review_assignment(fixture.run_id)
    assert tuple(data.segment for data in inputs) == assignment.segments
    assert fixture.store.load_final_review_artifact(fixture.run_id) == receipt
    assert paths.final_review_result.exists()
    assert paths.transcript.read_bytes() == before


def test_execute_final_review_bounds_concurrency_and_preserves_assignment_order(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    """Use four workers while collecting out-of-order completions by assignment."""
    fixture = create_run(tmp_path, segment_count=_EXPECTED_SEGMENTS)
    entered_four = anyio.Event()
    started: list[str] = []
    completed: list[str] = []
    active = 0
    max_active = 0
    gates = [anyio.Event() for _ in range(_EXPECTED_SEGMENTS)]
    finished = [anyio.Event() for _ in range(_EXPECTED_SEGMENTS)]
    fifth_started = anyio.Event()
    receipts: list[FinalReviewReceipt] = []

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        nonlocal active, max_active
        index = int(data.segment.segment_id.removeprefix("segment-")) - 1
        started.append(data.segment.segment_id)
        if index == _EXPECTED_SEGMENTS - 1:
            fifth_started.set()
        active += 1
        max_active = max(max_active, active)
        if active == _MAX_REVIEWERS:
            entered_four.set()
        await gates[index].wait()
        active -= 1
        completed.append(data.segment.segment_id)
        finished[index].set()
        return semantic_result(data)

    async def scenario() -> None:
        async def run() -> None:
            receipts.append(
                await execute_final_review(fixture.store, fixture.run_id, reviewer)
            )

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(run)
            with anyio.fail_after(1):
                await entered_four.wait()
            assert started == [
                "segment-0001",
                "segment-0002",
                "segment-0003",
                "segment-0004",
            ]
            gates[3].set()
            with anyio.fail_after(1):
                await finished[3].wait()
            with anyio.fail_after(1):
                await fifth_started.wait()
            gates[4].set()
            with anyio.fail_after(1):
                await finished[4].wait()
            for index in (2, 1, 0):
                gates[index].set()
                with anyio.fail_after(1):
                    await finished[index].wait()

    anyio.run(scenario)

    assert max_active == _MAX_REVIEWERS
    assert completed == [
        "segment-0004",
        "segment-0005",
        "segment-0003",
        "segment-0002",
        "segment-0001",
    ]
    assignment = fixture.store.load_final_review_assignment(fixture.run_id)
    result = fixture.store.load_final_review_result(fixture.run_id)
    assert tuple(segment.segment_id for segment in result.segments) == tuple(
        segment.segment_id for segment in assignment.segments
    )
    assert len(receipts) == 1


def test_execute_final_review_accepts_confined_cwd_relative_media_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepared manifests may persist project-relative source and VTT paths."""
    fixture = create_run(tmp_path, segment_count=1)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    monkeypatch.chdir(tmp_path)
    write_model_atomic(
        paths.media_manifest,
        media.model_copy(
            update={
                "video_path": str(fixture.video.relative_to(tmp_path)),
                "audio_path": str(fixture.audio.relative_to(tmp_path)),
                "subtitle_tracks": (
                    media.subtitle_tracks[0].model_copy(
                        update={"path": str(fixture.vtt.relative_to(tmp_path))}
                    ),
                ),
            }
        ),
    )

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        return semantic_result(data)

    _ = execute(fixture, reviewer)

    assert paths.final_review_artifact.exists()
