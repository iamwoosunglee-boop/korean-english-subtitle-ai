# Copyright 2026 Transcription Module contributors

"""Typed confirmation for provisional visual-speech insertions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.e2e.canonical_workflow_finalization_support import (
    make_provisional_confirmation_run,
)
from transcription_module.confirmation import (
    apply_confirmation_answer,
    apply_visual_insertion_confirmation,
    get_next_confirmation,
)
from transcription_module.confirmation_log import load_confirmation_history
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationItemKind,
    VisualInsertionAction,
)
from transcription_module.models import Uncertainty, WorkflowStatus
from transcription_module.source_finalization import (
    SourceFinalizationBlockedError,
    assert_source_finalizable,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_typed_visual_insertion_acceptance_retains_audio_bound_cue(
    tmp_path: Path,
) -> None:
    """Acceptance is an explicit action rather than a magic speech value."""
    store, run_id, cue_id = make_provisional_confirmation_run(tmp_path)
    with pytest.raises(SourceFinalizationBlockedError):
        assert_source_finalizable(store.load_transcript(run_id))

    item = get_next_confirmation(store, run_id)
    result = apply_visual_insertion_confirmation(
        store,
        run_id,
        item.confirmation_id,
        VisualInsertionAction.ACCEPT,
    )

    transcript = store.load_transcript(run_id)
    history = load_confirmation_history(store.paths(run_id).confirmations)
    assert item.kind is ConfirmationItemKind.VISUAL_INSERTION
    assert item.actions == (
        VisualInsertionAction.ACCEPT,
        VisualInsertionAction.REJECT,
    )
    assert result.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert tuple(cue.cue_id for cue in transcript.cues) == (cue_id,)
    assert transcript.cues[0].uncertainty is Uncertainty.CERTAIN
    assert tuple(record.kind for record in history.records[-2:]) == (
        "visual_insertion_answer",
        "visual_insertion_applied",
    )


def test_speakerless_acceptance_queues_speaker_after_insertion_decision(
    tmp_path: Path,
) -> None:
    """The insertion decision precedes its conditional speaker follow-up."""
    store, run_id, cue_id = make_provisional_confirmation_run(
        tmp_path,
        speaker=None,
    )
    before = load_confirmation_history(store.paths(run_id).confirmations)

    decision = get_next_confirmation(store, run_id)
    accepted = apply_visual_insertion_confirmation(
        store,
        run_id,
        decision.confirmation_id,
        VisualInsertionAction.ACCEPT,
    )

    history = load_confirmation_history(store.paths(run_id).confirmations)
    speaker_item = get_next_confirmation(store, run_id)
    assert len(before.requests) == 1
    assert tuple(item.kind for item in history.requests) == (
        ConfirmationItemKind.VISUAL_INSERTION,
        ConfirmationItemKind.FIELD,
    )
    assert accepted.workflow_status is WorkflowStatus.NEEDS_CONFIRMATION
    assert speaker_item.field is ConfirmationField.SPEAKER
    assert speaker_item.cue_id == cue_id
    assert store.load_transcript(run_id).cues[0].uncertainty is (
        Uncertainty.NEEDS_CONFIRMATION
    )
    with pytest.raises(SourceFinalizationBlockedError):
        assert_source_finalizable(store.load_transcript(run_id))

    resolved = apply_confirmation_answer(
        store,
        run_id,
        speaker_item.confirmation_id,
        "한지아",
    )

    assert resolved.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert store.load_transcript(run_id).cues[0].speaker == "한지아"
