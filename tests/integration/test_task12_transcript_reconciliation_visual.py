# Copyright 2026 Transcription Module contributors

"""Visual and unresolved identity rules for policy reconciliation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from tests.integration.task12_assembly_support import (
    assemble_without_agent,
    persist_policy_windows,
    policy_assembly_fixture,
)
from tests.integration.task12_correction_support import insertion_variant
from tests.integration.window_keep_claim_support import (
    automatic_keep_draft,
    simultaneous_unresolved_draft,
)
from transcription_module.models import Uncertainty, WorkflowStatus
from transcription_module.speaker_inference import (
    SpeakerInferenceDecision,
    apply_speaker_inference,
)
from transcription_module.visual_evidence_policy import CorrectionAction

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_INSERTIONS: Final = 2


def test_visual_insertion_dedupes_only_same_observation_and_turn(
    tmp_path: Path,
) -> None:
    """Given three insertions, only the exact observation/turn copy collapses."""
    fixture = policy_assembly_fixture(tmp_path)
    same = insertion_variant(
        fixture.window_input,
        observation_id="obs-insert-a",
        turn_id="turn:2",
        speaker="화자A",
    )
    distinct = insertion_variant(
        fixture.window_input,
        observation_id="obs-insert-b",
        turn_id="turn:3",
        speaker="화자B",
    )
    _ = persist_policy_windows(fixture, (same, distinct, same))

    artifact = assemble_without_agent(fixture)

    transcript = fixture.store.load_transcript(fixture.run_id)
    assert tuple(cue.speaker for cue in transcript.cues) == ("화자A", "화자B")
    assert len(artifact.applied_corrections) == EXPECTED_INSERTIONS
    assert all(
        receipt.action is CorrectionAction.INSERT_CUE
        for receipt in artifact.applied_corrections
    )


def test_hold_uncertainty_remaps_to_final_sorted_cue(tmp_path: Path) -> None:
    """Given a later HOLD window first, its unresolved target follows final order."""
    fixture = policy_assembly_fixture(tmp_path)
    held = simultaneous_unresolved_draft(fixture.window_input)
    unchanged = automatic_keep_draft(fixture.window_input)
    _ = persist_policy_windows(fixture, (held, unchanged))

    artifact = assemble_without_agent(fixture)

    transcript = fixture.store.load_transcript(fixture.run_id)
    assert artifact.unresolved[0].target_cue_index == 1
    assert tuple(cue.uncertainty for cue in transcript.cues) == (
        Uncertainty.CERTAIN,
        Uncertainty.NEEDS_CONFIRMATION,
    )
    assert fixture.store.load_state(fixture.run_id).workflow_status is (
        WorkflowStatus.PROCESSING
    )
    decisions = tuple(
        (
            SpeakerInferenceDecision(
                cue_id=cue.cue_id,
                speaker=cue.speaker,
                reason="기존 독립 화자 근거를 보존함",
            )
            if cue.speaker is not None
            else SpeakerInferenceDecision(
                cue_id=cue.cue_id,
                reason="동시 발화 소유권이 불명확함",
                candidates=("화자A", "화자B"),
            )
        )
        for cue in transcript.cues
        if cue.cue_id is not None
    )
    _ = apply_speaker_inference(
        fixture.store,
        fixture.run_id,
        decisions,
    )
    assert fixture.store.load_state(fixture.run_id).workflow_status is (
        WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    )
