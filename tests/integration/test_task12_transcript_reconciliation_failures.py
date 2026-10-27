# Copyright 2026 Transcription Module contributors

"""Fail-closed and legacy routing for whole-transcript reconciliation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from tests.integration.task12_assembly_support import (
    assemble_without_agent,
    persist_policy_windows,
    policy_assembly_fixture,
)
from tests.integration.task12_correction_support import replacement_variant
from tests.integration.window_keep_claim_support import automatic_keep_draft
from transcription_module.cue_provenance import CueProvenanceError
from transcription_module.drafting import (
    DraftCue,
    WholeTranscriptDraft,
    WholeTranscriptInput,
    WindowDraft,
    WindowDraftArtifact,
    WindowDraftInput,
    assemble_whole_transcript,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    CueId,
    EpisodeContext,
    FrameId,
    RunId,
    RunState,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.visual_correction import TextSlice
from transcription_module.window_evidence import WindowEvidenceError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("mutation", "message"),
    [("frame", "frame"), ("source", "unknown ASR")],
)
def test_unknown_frame_or_source_fails_before_publication(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    """Given forged policy references, no transcript authority path changes."""
    fixture = policy_assembly_fixture(tmp_path / mutation)
    draft = replacement_variant(
        fixture.window_input,
        TextSlice(start=8, end=10),
    )
    if mutation == "frame":
        observation = draft.observations[0].model_copy(
            update={"frame_ids": (FrameId("unknown-frame"),)}
        )
        forged = draft.model_copy(update={"observations": (observation,)})
    else:
        evidence = draft.cue_evidence[0].model_copy(
            update={
                "asr_ordinals": (99,),
                "source_evidence_ids": (
                    "asr:99",
                    "subtitle:automatic-ko-orig:1",
                    "vision:obs-shared-correction",
                ),
            }
        )
        forged = draft.model_copy(update={"cue_evidence": (evidence,)})
    _ = persist_policy_windows(fixture, (forged,), validate=False)
    paths = fixture.store.paths(fixture.run_id)
    state_before = paths.state.read_bytes()

    with pytest.raises(WindowEvidenceError, match=message):
        _ = assemble_without_agent(fixture)

    assert paths.state.read_bytes() == state_before
    assert not any(
        path.exists()
        for path in (
            paths.transcript,
            paths.transcript_csv,
            paths.confirmations,
            paths.whole_transcript_artifact,
        )
    )


def test_random_cue_id_replay_fails_without_republication(tmp_path: Path) -> None:
    """Given a replaced deterministic ID, replay rejects all existing bytes."""
    fixture = policy_assembly_fixture(tmp_path)
    _ = persist_policy_windows(
        fixture,
        (automatic_keep_draft(fixture.window_input),),
    )
    _ = assemble_without_agent(fixture)
    paths = fixture.store.paths(fixture.run_id)
    transcript = fixture.store.load_transcript(fixture.run_id)
    changed = transcript.model_copy(
        update={
            "cues": (
                transcript.cues[0].model_copy(
                    update={"cue_id": CueId("random-v4-replay-id")}
                ),
            )
        }
    )
    write_model_atomic(paths.transcript, changed)
    guarded = (
        paths.transcript,
        paths.transcript_csv,
        paths.state,
        paths.whole_transcript_artifact,
    )
    before = tuple(path.read_bytes() for path in guarded)

    with pytest.raises(CueProvenanceError, match="deterministic"):
        _ = assemble_without_agent(fixture)

    assert tuple(path.read_bytes() for path in guarded) == before


def test_legacy_run_still_routes_through_legacy_agent_assembler(
    tmp_path: Path,
) -> None:
    """Given legacy evidence, the existing callback remains the sole route."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("legacy-task12-run")
    source_url = YouTubeVideoUrl("https://youtube.test/legacy-task12")
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
            duration_ms=2_000,
            frame_count=0,
        ),
    )
    cue = DraftCue(start_tc_ms=100, end_tc_ms=900, speech="기존 경로")
    window_input = WindowDraftInput(
        window_index=0,
        core_start_ms=0,
        core_end_ms=2_000,
        context_start_ms=0,
        context_end_ms=2_000,
        asr_segments=(),
        subtitle_tracks=(),
        frames=(),
    )
    write_model_atomic(
        paths.window_drafts,
        WindowDraftArtifact(
            run_id=run_id,
            inputs=(window_input,),
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
    calls: list[WholeTranscriptInput] = []

    async def assembler(data: WholeTranscriptInput) -> WholeTranscriptDraft:
        calls.append(data)
        return WholeTranscriptDraft(cues=(cue,), unresolved=())

    _ = anyio.run(assemble_whole_transcript, store, run_id, assembler)

    assert len(calls) == 1
    assert store.load_transcript(run_id).cues[0].speech == "기존 경로"
