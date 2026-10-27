# Copyright 2026 Transcription Module contributors

"""Whole-transcript simultaneous-cue assembly scenario."""

from pathlib import Path

import anyio

from transcription_module.drafting import (
    DraftCue,
    UnresolvedItem,
    WholeTranscriptDraft,
    WholeTranscriptInput,
    WindowDraft,
    WindowDraftArtifact,
    WindowDraftInput,
    assemble_whole_transcript,
)
from transcription_module.media import PreparedFrameManifest, PreparedMediaManifest
from transcription_module.models import (
    EpisodeContext,
    FrameId,
    RunId,
    RunState,
    Transcript,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic

EXPECTED_ASSEMBLED_CUE_COUNT = 3
EXPECTED_SOURCE_DRAFT_COUNT = 2


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
                    start_tc_ms=6_000,
                    end_tc_ms=7_000,
                    speech="동시에 한 말",
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
    assert [cue.speaker for cue in transcript.cues[1:]] == ["가", "나"]
    assert [cue.speech for cue in transcript.cues[1:]] == ["동시에 한 말"] * 2
    assert transcript.validation_status is ValidationStatus.PENDING
    assert transcript.workflow_status is WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    assert paths.confirmation_session.exists()
    assert artifact.inspected_frame_ids == (FrameId("frame-000"),)
