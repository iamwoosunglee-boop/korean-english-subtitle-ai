# Copyright 2026 Transcription Module contributors

"""Source blockers and minimal confirmation workflow support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tests.e2e.canonical_workflow_fixture_support import create_workflow_run
from transcription_module.confirmation import (
    apply_confirmation_answer,
    create_confirmation_session,
    set_confirmation_consent,
)
from transcription_module.confirmation_log import (
    append_confirmation_requests,
    load_confirmation_history,
)
from transcription_module.confirmation_models import (
    ConfirmationError,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.models import Cue, CueId, Transcript, Uncertainty
from transcription_module.source_finalization import (
    SourceFinalizationBlockedError,
    assert_source_finalizable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.models import RunId
    from transcription_module.storage import RunStore


@dataclass(frozen=True, slots=True)
class ConfirmedWorkflowResult:
    """Confirmed run plus observed source authority facts."""

    store: RunStore
    run_id: RunId
    observations: dict[str, str | int | bool]


def _item(identifier: str, cue: Cue, value: str) -> ConfirmationItem:
    return ConfirmationItem(
        confirmation_id=ConfirmationId(identifier),
        cue_id=cue.cue_id,
        cue_sequence=cue.sequence,
        field=ConfirmationField.SPEECH,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        current_value=cue.speech,
        cue_speech=cue.speech,
        candidates=(value,),
        evidence=("fixed evidence",),
    )


def _prove_source_blockers(transcript: Transcript) -> tuple[str, str, str]:
    before = transcript.model_dump_json()
    with pytest.raises(SourceFinalizationBlockedError) as unresolved:
        assert_source_finalizable(transcript)
    blank_cue = transcript.cues[0].model_copy(
        update={"uncertainty": Uncertainty.CERTAIN, "speaker": " "}
    )
    blank = transcript.model_copy(update={"cues": (blank_cue, *transcript.cues[1:])})
    with pytest.raises(SourceFinalizationBlockedError) as empty:
        assert_source_finalizable(blank)
    duplicate_cue = transcript.cues[2].model_copy(update={"speaker": "화자-가"})
    resolved = transcript.cues[0].model_copy(
        update={"uncertainty": Uncertainty.CERTAIN}
    )
    duplicate = transcript.model_copy(
        update={"cues": (resolved, transcript.cues[1], duplicate_cue)}
    )
    with pytest.raises(SourceFinalizationBlockedError) as repeated:
        assert_source_finalizable(duplicate)
    assert transcript.model_dump_json() == before
    assert tuple(cue.cue_id for cue in transcript.cues[1:]) == (
        CueId("cue-speaker-a"),
        CueId("cue-speaker-b"),
    )
    return (
        unresolved.value.findings[0].code.value,
        empty.value.findings[0].code.value,
        repeated.value.findings[-1].code.value,
    )


def run_source_workflow(root: Path) -> ConfirmedWorkflowResult:
    """Exercise blockers, one-at-a-time answers, and stale rejection."""
    store, run_id = create_workflow_run(root)
    transcript = store.load_transcript(run_id)
    blocker_codes = _prove_source_blockers(transcript)
    path = store.paths(run_id).confirmations
    first = _item("request-one", transcript.cues[0], "확정 발화")
    append_confirmation_requests(path, (first,))
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    _ = apply_confirmation_answer(
        store,
        run_id,
        first.confirmation_id,
        "확정 발화",
    )

    transcript = store.load_transcript(run_id)
    second = _item("request-two", transcript.cues[1], "수정 발화")
    append_confirmation_requests(path, (second,))
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    changed_last = transcript.cues[2].model_copy(update={"speech": "동시 발화 변경"})
    store.save_transcript(
        transcript.model_copy(update={"cues": (*transcript.cues[:2], changed_last)})
    )
    stale_before = store.paths(run_id).transcript.read_bytes()
    with pytest.raises(ConfirmationError) as stale:
        _ = apply_confirmation_answer(
            store,
            run_id,
            second.confirmation_id,
            "수정 발화",
        )
    assert stale.value.code.value == "stale_confirmation"
    assert store.paths(run_id).transcript.read_bytes() == stale_before
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    _ = apply_confirmation_answer(
        store,
        run_id,
        second.confirmation_id,
        "수정 발화",
    )
    history = load_confirmation_history(path)
    return ConfirmedWorkflowResult(
        store,
        run_id,
        {
            "unresolved": blocker_codes[0],
            "blank": blocker_codes[1],
            "duplicate": blocker_codes[2],
            "confirmation_records": len(history.records),
            "incomplete_closure_blocked": True,
            "answer_one_closures": int(first.confirmation_id in history.applied_ids),
            "same_time_order_preserved": True,
        },
    )
