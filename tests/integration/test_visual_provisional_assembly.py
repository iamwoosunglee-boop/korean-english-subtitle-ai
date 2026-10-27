# Copyright 2026 Transcription Module contributors

"""Policy assembly projects provisional evidence into typed confirmation."""

from transcription_module._drafting_policy_assembly import (
    build_policy_confirmation_projection,
    confirmation_items,
)
from transcription_module._drafting_reconciliation_models import (
    PolicyReconciliation,
    ReconciledProvisionalInsertion,
)
from transcription_module.confirmation_models import (
    ConfirmationItemKind,
    VisualInsertionAction,
)
from transcription_module.models import FrameId, RunId, Uncertainty
from transcription_module.window_evidence_models import (
    ProvisionalVisualInsertion,
)


def test_policy_assembly_projects_provisional_insertion_before_speaker() -> None:
    """A known-speaker provisional cue gets only the insertion decision."""
    provisional = ProvisionalVisualInsertion(
        provisional_id="provisional:assembly",
        candidate_id="candidate:assembly",
        observation_id="observation:assembly",
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        speech="화면으로 복원한 발화",
        speaker="한지아",
        vad_interval_ids=("vad:assembly",),
        turn_id="turn:assembly",
        disposition_evidence_ids=("vision:assembly", "turn:assembly"),
    )
    reconciliation = PolicyReconciliation(
        policy_digest="1" * 64,
        source_digest="2" * 64,
        cues=(),
        provisional_insertions=(
            ReconciledProvisionalInsertion(
                insertion=provisional,
                frame_points=((1_200, FrameId("frame:assembly")),),
            ),
        ),
        inspected_frame_ids=(FrameId("frame:assembly"),),
        inspected_frame_points=((1_200, FrameId("frame:assembly")),),
    )

    projection = build_policy_confirmation_projection(
        RunId("visual-provisional-assembly"),
        reconciliation,
        duration_ms=2_000,
    )
    field_items = confirmation_items(
        projection.transcript,
        projection.unresolved,
        deferred_speaker_cue_ids=projection.provisional_cue_ids,
    )

    assert len(projection.transcript.cues) == 1
    assert projection.transcript.cues[0].uncertainty is (
        Uncertainty.NEEDS_CONFIRMATION
    )
    assert field_items == ()
    assert len(projection.visual_confirmation_items) == 1
    decision = projection.visual_confirmation_items[0]
    assert decision.kind is ConfirmationItemKind.VISUAL_INSERTION
    assert decision.actions == (
        VisualInsertionAction.ACCEPT,
        VisualInsertionAction.REJECT,
    )
    assert decision.provisional_id == provisional.provisional_id
