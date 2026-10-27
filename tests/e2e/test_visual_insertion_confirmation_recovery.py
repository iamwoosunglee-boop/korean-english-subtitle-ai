# Copyright 2026 Transcription Module contributors

"""Recovery and freshness for provisional insertion decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.e2e.canonical_workflow_finalization_support import (
    make_provisional_confirmation_run,
)
from transcription_module.confirmation import (
    apply_visual_insertion_confirmation,
    get_next_confirmation,
)
from transcription_module.confirmation_log import (
    append_confirmation_records,
    append_confirmation_requests,
    load_confirmation_history,
)
from transcription_module.confirmation_models import (
    ConfirmationError,
    ConfirmationErrorCode,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationRecord,
    VisualInsertionAction,
)
from transcription_module.models import Milliseconds, WorkflowStatus

if TYPE_CHECKING:
    from pathlib import Path


def test_rejection_retires_dependent_question_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    """Reject only the cue while retaining raw evidence and one receipt."""
    store, run_id, cue_id = make_provisional_confirmation_run(
        tmp_path,
        speaker=None,
    )
    paths = store.paths(run_id)
    decision = get_next_confirmation(store, run_id)
    append_confirmation_requests(
        paths.confirmations,
        (
            ConfirmationItem(
                confirmation_id=ConfirmationId("dependent-speaker"),
                cue_id=cue_id,
                cue_sequence=1,
                field=ConfirmationField.SPEAKER,
                depends_on_confirmation_id=decision.confirmation_id,
                start_tc_ms=Milliseconds(500),
                end_tc_ms=Milliseconds(1_500),
                current_value=None,
                cue_speech="화면으로 복원한 발화",
                candidates=("한지아", "알 수 없음"),
                evidence=("dependent fixture",),
            ),
        ),
    )
    raw_before = paths.window_visual_review.read_bytes()
    provisional_before = paths.window_drafts.read_bytes()

    rejected = apply_visual_insertion_confirmation(
        store,
        run_id,
        decision.confirmation_id,
        VisualInsertionAction.REJECT,
    )
    log_after_first = paths.confirmations.read_bytes()
    replayed = apply_visual_insertion_confirmation(
        store,
        run_id,
        decision.confirmation_id,
        VisualInsertionAction.REJECT,
    )

    history = load_confirmation_history(paths.confirmations)
    assert rejected == replayed
    assert rejected.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert store.load_transcript(run_id).cues == ()
    assert history.retired_ids == frozenset(
        {ConfirmationId("dependent-speaker")}
    )
    assert paths.confirmations.read_bytes() == log_after_first
    assert paths.window_visual_review.read_bytes() == raw_before
    assert paths.window_drafts.read_bytes() == provisional_before
    assert tuple(record.kind for record in history.records[-3:]) == (
        "visual_insertion_answer",
        "visual_insertion_applied",
        "retired",
    )


@pytest.mark.parametrize(
    "scope_path",
    [
        "window_visual_review",
        "window_drafts",
        "whole_transcript_artifact",
    ],
)
def test_stale_confirmation_scope_rejects_and_invalidates_dependents(
    tmp_path: Path,
    scope_path: str,
) -> None:
    """Changing bound evidence after consent rejects application fail-closed."""
    store, run_id, _ = make_provisional_confirmation_run(tmp_path)
    paths = store.paths(run_id)
    decision = get_next_confirmation(store, run_id)
    for path in (
        paths.validation_artifact,
        paths.final_review_assignment,
        paths.final_review_result,
        paths.final_review_artifact,
        paths.input_context,
        paths.canonical_source,
    ):
        _ = path.write_text('{"stale":true}\n', encoding="utf-8")
    scoped_paths = {
        "window_visual_review": paths.window_visual_review,
        "window_drafts": paths.window_drafts,
        "whole_transcript_artifact": paths.whole_transcript_artifact,
    }
    _ = scoped_paths[scope_path].write_text(
        '{"confirmation_scope":"changed"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfirmationError) as stale:
        _ = apply_visual_insertion_confirmation(
            store,
            run_id,
            decision.confirmation_id,
            VisualInsertionAction.ACCEPT,
        )

    assert stale.value.code is ConfirmationErrorCode.STALE_CONFIRMATION
    assert len(store.load_transcript(run_id).cues) == 1
    assert all(
        not path.exists()
        for path in (
            paths.validation_artifact,
            paths.final_review_assignment,
            paths.final_review_result,
            paths.final_review_artifact,
            paths.input_context,
            paths.canonical_source,
        )
    )


def test_interrupted_rejection_recovers_from_durable_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A logged answer replays after transcript publication interruption."""
    store, run_id, _ = make_provisional_confirmation_run(tmp_path)
    paths = store.paths(run_id)
    decision = get_next_confirmation(store, run_id)

    def interrupt_application(
        path: Path,
        records: tuple[ConfirmationRecord, ...],
    ) -> None:
        if records and records[0].kind == "visual_insertion_applied":
            message = "fixture interruption after transcript publication"
            raise RuntimeError(message)
        append_confirmation_records(path, records)

    monkeypatch.setattr(
        "transcription_module.confirmation.append_confirmation_records",
        interrupt_application,
    )
    with pytest.raises(RuntimeError, match="fixture interruption"):
        _ = apply_visual_insertion_confirmation(
            store,
            run_id,
            decision.confirmation_id,
            VisualInsertionAction.REJECT,
        )

    interrupted = load_confirmation_history(paths.confirmations)
    assert store.load_transcript(run_id).cues == ()
    assert interrupted.answered_ids == frozenset({decision.confirmation_id})
    assert interrupted.applied_ids == frozenset()

    monkeypatch.setattr(
        "transcription_module.confirmation.append_confirmation_records",
        append_confirmation_records,
    )
    recovered = apply_visual_insertion_confirmation(
        store,
        run_id,
        decision.confirmation_id,
        VisualInsertionAction.REJECT,
    )

    history = load_confirmation_history(paths.confirmations)
    assert recovered.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert history.applied_ids == frozenset({decision.confirmation_id})
