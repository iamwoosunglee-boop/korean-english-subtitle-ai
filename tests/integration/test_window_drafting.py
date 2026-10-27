# Copyright 2026 Transcription Module contributors

"""Window drafts consume independent evidence with bounded concurrency."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from transcription_module.asr import AsrRunArtifact, AsrTranscript
from transcription_module.confirmation_models import ConfirmationField
from transcription_module.drafting import (
    DiarizationError,
    DiarizationStatus,
    DraftCue,
    SpeakerEvidenceSegment,
    UnresolvedItem,
    WholeTranscriptDraft,
    WholeTranscriptInput,
    WindowDraft,
    WindowDraftArtifact,
    WindowDraftInput,
    assemble_whole_transcript,
    confirmation_item_for_unresolved,
    create_window_drafts,
    run_optional_diarization,
    select_cue_for_unresolved,
)
from transcription_module.media import (
    PreparedFrameManifest,
    PreparedMediaManifest,
)
from transcription_module.models import (
    Cue,
    CueId,
    EpisodeContext,
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

MAX_WINDOW_WORKERS = 4
EXPECTED_WINDOW_COUNT = 5
EXPECTED_ASSEMBLED_CUE_COUNT = 3
EXPECTED_SOURCE_DRAFT_COUNT = 2
OVERLAP_TARGET_SEQUENCE = 2


def test_confirmation_item_prefers_exact_start_in_overlapping_speech() -> None:
    """Exact cue starts win over an earlier overlapping cue."""
    cues = (
        Cue(
            sequence=1,
            cue_id=CueId("cue-1"),
            start_tc_ms=Milliseconds(15_879),
            end_tc_ms=Milliseconds(19_150),
            speech="한지아의 말",
            speaker="한지아",
            uncertainty=Uncertainty.CERTAIN,
        ),
        Cue(
            sequence=2,
            cue_id=CueId("cue-2"),
            start_tc_ms=Milliseconds(17_860),
            end_tc_ms=Milliseconds(19_960),
            speech="문하린의 말",
            speaker="문하린",
            uncertainty=Uncertainty.CERTAIN,
        ),
    )
    confirmation = confirmation_item_for_unresolved(
        UnresolvedItem(at_ms=17_860, field="speech", reason="겹친 발화"), cues
    )
    assert confirmation.cue_sequence == OVERLAP_TARGET_SEQUENCE
    assert confirmation.current_value == "문하린의 말"
    assert confirmation.field is ConfirmationField.SPEECH


def test_unresolved_selector_prefers_exact_start_in_overlapping_speech() -> None:
    """The uncertainty selector marks only the exact-start overlapping cue."""
    cues = (
        DraftCue(start_tc_ms=15_879, end_tc_ms=19_150, speech="첫 발화", speaker="A"),
        DraftCue(start_tc_ms=17_860, end_tc_ms=19_960, speech="대상 발화", speaker="B"),
    )
    selected = select_cue_for_unresolved(
        cues,
        UnresolvedItem(at_ms=17_860, field="speech", reason="겹친 발화"),
    )
    assert selected is cues[1]


def test_confirmation_item_preserves_nonoverlap_mapping() -> None:
    """Non-overlapping fields retain their existing cue mapping."""
    cue = Cue(
        sequence=1,
        cue_id=CueId("cue-1"),
        start_tc_ms=Milliseconds(1_000),
        end_tc_ms=Milliseconds(2_000),
        speech="말",
        speaker="화자",
        screen_text="자막",
        uncertainty=Uncertainty.CERTAIN,
    )
    confirmation = confirmation_item_for_unresolved(
        UnresolvedItem(at_ms=1_500, field="screen_text", reason="확인"), (cue,)
    )
    assert confirmation.cue_sequence == 1
    assert confirmation.current_value == "자막"


@pytest.mark.parametrize("ellipsis", ["...", "…"])
def test_unresolved_candidates_reject_abbreviated_values(
    ellipsis: str,
) -> None:
    """A candidate is a complete replacement, never an excerpt."""
    with pytest.raises(ValueError, match="complete replacement values"):
        _ = UnresolvedItem(
            at_ms=1_000,
            field="speech",
            reason="끝부분을 들을 수 없음",
            candidates=(f"완전하지 않은 후보{ellipsis}",),
        )


def test_window_drafts_use_five_minutes_context_and_four_workers(
    tmp_path: Path,
) -> None:
    """Window inputs use approved boundaries and bounded concurrency."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000011")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=window-fixture")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="audio.wav",
            duration_ms=1_500_000,
            frame_count=0,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(text="", language="ko", segments=()),
        ),
    )
    entered_four = anyio.Event()
    release = anyio.Event()
    started: list[int] = []
    active = 0
    max_active = 0
    results: list[WindowDraftArtifact] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal active, max_active
        started.append(data.window_index)
        active += 1
        max_active = max(max_active, active)
        if active == MAX_WINDOW_WORKERS:
            entered_four.set()
        await release.wait()
        active -= 1
        return WindowDraft(
            window_index=data.window_index,
            cues=(),
            unresolved=(),
            evidence_reviewed=True,
        )

    async def scenario() -> None:
        async def execute() -> None:
            results.append(
                await create_window_drafts(
                    store,
                    run_id,
                    EmptyVisualReviewer(),
                    drafter,
                )
            )

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(execute)
            with anyio.fail_after(1):
                await entered_four.wait()
            assert started == [0, 1, 2, 3]
            assert max_active == MAX_WINDOW_WORKERS
            release.set()

    anyio.run(scenario)

    assert len(results) == 1
    result = results[0]
    assert len(result.inputs) == EXPECTED_WINDOW_COUNT
    assert [item.window_index for item in result.inputs] == [0, 1, 2, 3, 4]
    assert (
        result.inputs[0].core_start_ms,
        result.inputs[0].core_end_ms,
        result.inputs[0].context_start_ms,
        result.inputs[0].context_end_ms,
    ) == (0, 300_000, 0, 315_000)
    assert (
        result.inputs[1].core_start_ms,
        result.inputs[1].core_end_ms,
        result.inputs[1].context_start_ms,
        result.inputs[1].context_end_ms,
    ) == (300_000, 600_000, 285_000, 615_000)
    persisted = WindowDraftArtifact.model_validate_json(
        paths.window_drafts.read_text(encoding="utf-8")
    )
    assert persisted == result


def test_whole_transcript_assigns_ids_after_agent_resolves_overlap(
    tmp_path: Path,
) -> None:
    """Assembly resolves overlap, permits crosstalk, and assigns UUIDs."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000012")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=assembly-fixture")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    frame_path = paths.run_dir / "input" / "frames" / "f000001.jpg"
    frame_path.parent.mkdir(parents=True)
    _ = frame_path.write_bytes(b"visible lower third")
    frame = PreparedFrameManifest(
        frame_id=FrameId("frame-000"),
        frame_index=0,
        raw_pts=0,
        raw_pts_time="0.000",
        best_effort_timestamp=0,
        best_effort_timestamp_time="0.000",
        pts_ms=0,
        path=str(frame_path.relative_to(paths.run_dir)),
    )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="audio.wav",
            duration_ms=10_000,
            frame_count=1,
            frames=(frame,),
        ),
    )
    inputs = tuple(
        WindowDraftInput(
            window_index=index,
            core_start_ms=index * 5_000,
            core_end_ms=(index + 1) * 5_000,
            context_start_ms=0,
            context_end_ms=10_000,
            asr_segments=(),
            subtitle_tracks=(),
            frames=(frame,),
        )
        for index in range(2)
    )
    repeated = DraftCue(
        start_tc_ms=4_000,
        end_tc_ms=5_000,
        speech="중복 문장",
    )
    write_model_atomic(
        paths.window_drafts,
        WindowDraftArtifact(
            run_id=run_id,
            inputs=inputs,
            drafts=(
                WindowDraft(
                    window_index=0,
                    cues=(repeated,),
                    unresolved=(),
                    evidence_reviewed=True,
                ),
                WindowDraft(
                    window_index=1,
                    cues=(repeated,),
                    unresolved=(),
                    evidence_reviewed=True,
                ),
            ),
        ),
    )

    async def assembler(data: WholeTranscriptInput) -> WholeTranscriptDraft:
        assert len(data.window_drafts.drafts) == EXPECTED_SOURCE_DRAFT_COUNT
        assert (data.run_dir / data.frames[0].path).read_bytes()
        return WholeTranscriptDraft(
            cues=(
                repeated,
                DraftCue(
                    start_tc_ms=6_000,
                    end_tc_ms=7_000,
                    speech="동시에 한 말",
                    speaker="가",
                ),
                DraftCue(
                    start_tc_ms=6_500,
                    end_tc_ms=7_500,
                    speech="겹쳐서 한 말",
                    speaker="나",
                ),
            ),
            unresolved=(
                UnresolvedItem(
                    at_ms=6_000,
                    field="speaker",
                    reason="화면 밖 발화라 판단할 수 없음",
                    candidates=("가", "나"),
                ),
            ),
            inspected_frame_ids=(FrameId("frame-000"),),
        )

    artifact = anyio.run(assemble_whole_transcript, store, run_id, assembler)

    transcript = Transcript.model_validate_json(
        paths.transcript.read_text(encoding="utf-8")
    )
    cue_ids = [cue.cue_id for cue in transcript.cues]
    assert all(cue_id is not None for cue_id in cue_ids)
    assert len(set(cue_ids)) == EXPECTED_ASSEMBLED_CUE_COUNT
    assert [cue.speech for cue in transcript.cues].count("중복 문장") == 1
    assert transcript.cues[1].end_tc_ms > transcript.cues[2].start_tc_ms
    assert transcript.validation_status is ValidationStatus.PENDING
    assert transcript.workflow_status is WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    assert paths.confirmation_session.exists()
    assert artifact.inspected_frame_ids == (FrameId("frame-000"),)


def test_assembly_rejects_unreviewed_window_drafts(
    tmp_path: Path,
) -> None:
    """An ASR fallback cannot be promoted without evidence review."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000018")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=unreviewed-fallback")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="audio.wav",
            duration_ms=10_000,
            frame_count=0,
        ),
    )
    cue = DraftCue(
        start_tc_ms=0,
        end_tc_ms=1_000,
        speech="검수되지 않은 ASR 초안",
    )
    write_model_atomic(
        paths.window_drafts,
        WindowDraftArtifact(
            run_id=run_id,
            inputs=(),
            drafts=(
                WindowDraft(
                    window_index=0,
                    cues=(cue,),
                    unresolved=(),
                ),
            ),
        ),
    )

    async def assembler(_: WholeTranscriptInput) -> WholeTranscriptDraft:
        return WholeTranscriptDraft(cues=(cue,), unresolved=())

    with pytest.raises(ValueError, match="evidence-reviewed"):
        _ = anyio.run(assemble_whole_transcript, store, run_id, assembler)

    assert not paths.transcript.exists()


def test_assembly_rejects_visual_unresolved_without_any_frame_inspection(
    tmp_path: Path,
) -> None:
    """Visual ambiguity requires looking at stored frames first."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000017")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=frame-gate")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="audio.wav",
            duration_ms=10_000,
            frame_count=0,
        ),
    )
    cue = DraftCue(start_tc_ms=0, end_tc_ms=1_000, speech="발화")
    write_model_atomic(
        paths.window_drafts,
        WindowDraftArtifact(
            run_id=run_id,
            inputs=(),
            drafts=(
                WindowDraft(
                    window_index=0,
                    cues=(cue,),
                    unresolved=(),
                    evidence_reviewed=True,
                ),
            ),
        ),
    )

    async def assembler(_: WholeTranscriptInput) -> WholeTranscriptDraft:
        return WholeTranscriptDraft(
            cues=(cue,),
            unresolved=(
                UnresolvedItem(
                    at_ms=0,
                    field="speaker",
                    reason="화면 밖 발화라 판단할 수 없음",
                ),
            ),
            inspected_frame_ids=(),
        )

    with pytest.raises(ValueError, match="inspected frame"):
        _ = anyio.run(assemble_whole_transcript, store, run_id, assembler)


def test_optional_diarization_is_a_fallible_window_clue(
    tmp_path: Path,
) -> None:
    """Opaque clusters enter window evidence without becoming identities."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000014")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=diarization-fixture")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="whole-audio.wav",
            duration_ms=10_000,
            frame_count=0,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(text="", language="ko", segments=()),
        ),
    )
    calls: list[Path] = []

    def diarize(audio_path: Path) -> tuple[SpeakerEvidenceSegment, ...]:
        calls.append(audio_path)
        return (
            SpeakerEvidenceSegment(
                start_tc_ms=0,
                end_tc_ms=2_000,
                cluster="SPEAKER_00",
            ),
        )

    evidence = run_optional_diarization(store, run_id, diarize)
    captured: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return WindowDraft(
            window_index=data.window_index,
            cues=(),
            unresolved=(),
            evidence_reviewed=True,
        )

    _ = anyio.run(
        create_window_drafts,
        store,
        run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert calls == [Path("whole-audio.wav")]
    assert evidence.status is DiarizationStatus.AVAILABLE
    assert captured[0].speaker_segments[0].cluster == "SPEAKER_00"
    assert "person" not in evidence.model_dump_json()


def test_failed_optional_diarization_does_not_block_drafts(
    tmp_path: Path,
) -> None:
    """A persisted pyannote failure leaves drafting available."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000015")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=diarization-failure")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="source.mp4",
            audio_path="whole-audio.wav",
            duration_ms=10_000,
            frame_count=0,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(text="", language="ko", segments=()),
        ),
    )

    def fail(_: Path) -> tuple[SpeakerEvidenceSegment, ...]:
        message = "pyannote unavailable"
        raise DiarizationError(message)

    evidence = run_optional_diarization(store, run_id, fail)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        assert data.speaker_segments == ()
        return WindowDraft(
            window_index=data.window_index,
            cues=(),
            unresolved=(),
            evidence_reviewed=True,
        )

    result = anyio.run(
        create_window_drafts,
        store,
        run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert evidence.status is DiarizationStatus.FAILED
    assert evidence.error == "pyannote unavailable"
    assert len(result.drafts) == 1
