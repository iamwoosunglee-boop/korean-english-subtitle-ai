# Copyright 2026 Transcription Module contributors

"""Confirmation answers bind the source current when consent begins."""

from pathlib import Path

import pytest

from transcription_module.confirmation import (
    apply_confirmation_answer,
    create_confirmation_session,
    set_confirmation_consent,
)
from transcription_module.confirmation_log import append_confirmation_requests
from transcription_module.confirmation_models import (
    ConfirmationError,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.models import (
    Cue,
    CueId,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore


def test_answer_is_rejected_after_any_source_edit(tmp_path: Path) -> None:
    """A later unrelated cue edit makes a consented answer stale."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("confirmation-freshness")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtu.be/freshness"),
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
        )
    )
    transcript = Transcript(
        run_id=run_id,
        workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(2_000),
        cues=(
            Cue(
                sequence=1,
                cue_id=CueId("cue-1"),
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(1_000),
                speech="확인 전",
                speaker="화자",
                uncertainty=Uncertainty.NEEDS_CONFIRMATION,
            ),
            Cue(
                sequence=2,
                cue_id=CueId("cue-2"),
                start_tc_ms=Milliseconds(1_000),
                end_tc_ms=Milliseconds(2_000),
                speech="다른 발화",
                speaker="화자",
                uncertainty=Uncertainty.CERTAIN,
            ),
        ),
        vision_evidence=(),
    )
    store.save_transcript(transcript)
    item = ConfirmationItem(
        confirmation_id=ConfirmationId("request-1"),
        cue_id=CueId("cue-1"),
        cue_sequence=1,
        field=ConfirmationField.SPEECH,
        start_tc_ms=Milliseconds(0),
        end_tc_ms=Milliseconds(1_000),
        current_value="확인 전",
        cue_speech="확인 전",
        candidates=("확정 발화",),
        evidence=("fixture",),
    )
    append_confirmation_requests(store.paths(run_id).confirmations, (item,))
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    store.save_transcript(
        transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0],
                    transcript.cues[1].model_copy(update={"speech": "다른 발화 수정"}),
                )
            }
        )
    )
    before = store.paths(run_id).transcript.read_bytes()

    with pytest.raises(ConfirmationError) as stale:
        _ = apply_confirmation_answer(
            store,
            run_id,
            item.confirmation_id,
            "확정 발화",
        )

    assert stale.value.code.value == "stale_confirmation"
    assert store.paths(run_id).transcript.read_bytes() == before
