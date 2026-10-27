# Copyright 2026 Transcription Module contributors

"""Cue-less visual speech uses audio timing and verified speaker authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.window_claim_support import (
    ObservationSpec,
    load_policy_window_input,
    make_observation,
)
from transcription_module._drafting_models import WindowDraft
from transcription_module._drafting_post_reconciliation import (
    reconcile_post_drafter_window,
)
from transcription_module.drafting import (
    CandidateDispositionKind,
    FrameVisualObservation,
    RawCandidateDisposition,
    RawVisualCandidate,
    RawVisualRole,
    WindowVisualReviewResult,
)
from transcription_module.models import FrameId
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.window_cue_identity import window_cue_identity
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
)
from transcription_module.window_evidence_models import WindowVerifiedTurn

if TYPE_CHECKING:
    from pathlib import Path


def test_cueless_visual_speech_uses_verified_audio_turn(
    tmp_path: Path,
) -> None:
    """A complete visual candidate inherits one verified audio turn."""
    original = load_policy_window_input(tmp_path)
    turn = WindowVerifiedTurn(
        turn_id="turn:verified:1",
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        cluster="SPEAKER_00",
        speaker="한지아",
        authority_digest="3" * 64,
        verification_evidence_ids=("speaker-evidence:1",),
        verification_decision_ids=("speaker-decision:1",),
    )
    extra_frame = original.frames[0].model_copy(
        update={
            "frame_id": FrameId("frame-1b"),
            "frame_index": 1,
            "pts_ms": 1_400,
            "raw_pts": 1_400,
            "raw_pts_time": "1.400",
            "best_effort_timestamp": 1_400,
            "best_effort_timestamp_time": "1.400",
        }
    )
    frames = (original.frames[0], extra_frame, *original.frames[1:])
    visual_review = WindowVisualReviewResult(
        window_index=original.window_index,
        reviewer_input_digest=(
            original.visual_review.reviewer_input_digest
            if original.visual_review is not None
            else "4" * 64
        ),
        frame_observations=tuple(
            FrameVisualObservation(
                frame_id=frame.frame_id,
                visible_screen_text=(
                    ("새로 복원한 발화",)
                    if str(frame.frame_id) == "frame-1"
                    else ()
                ),
            )
            for frame in frames
        ),
        candidates=(
            RawVisualCandidate(
                candidate_id="candidate-cueless",
                complete_text="새로 복원한 발화",
                proposed_role=RawVisualRole.SPEECH_CAPTION,
                supporting_frame_ids=(
                    original.frames[0].frame_id,
                    extra_frame.frame_id,
                ),
                evidence_for=("완전한 발화 자막이 보인다",),
                missing_evidence=("ASR과 자막에는 같은 문구가 없다",),
            ),
        ),
    )
    data = original.model_copy(
        update={
            "asr_segments": (),
            "subtitle_cues": (),
            "frames": frames,
            "verified_turns": (turn,),
            "visual_review": visual_review,
        }
    )
    observation = make_observation(
        data,
        ObservationSpec(
            observation_id="obs-cueless",
            frame_ids=("frame-1", "frame-1b"),
            frame_pts_ms=(1_200, 1_400),
            text="새로 복원한 발화",
            vad_id="vad:1",
            turn_id=turn.turn_id,
        ),
    )
    draft = WindowDraft(
        window_index=data.window_index,
        cues=(),
        unresolved=(),
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=data.policy_digest,
        inventory_digest=data.inventory_digest,
        source_digest=data.source_digest,
        observations=(observation,),
        candidate_dispositions=(
            RawCandidateDisposition(
                candidate_id="candidate-cueless",
                kind=CandidateDispositionKind.AUTHORIZED_INSERTION,
                evidence_ids=("vision:obs-cueless", turn.turn_id),
            ),
        ),
    )

    result = reconcile_post_drafter_window(data, draft)

    assert len(result.cues) == 1
    assert (
        result.cues[0].start_tc_ms,
        result.cues[0].end_tc_ms,
        result.cues[0].speaker,
    ) == (turn.start_tc_ms, turn.end_tc_ms, turn.speaker)
    assert result.cue_evidence[0].turn_ids == (turn.turn_id,)

    forged_cue = result.cues[0].model_copy(
        update={"start_tc_ms": 1_100, "end_tc_ms": 1_600}
    )
    forged_evidence = result.cue_evidence[0].model_copy(
        update={
            "cue_identity": window_cue_identity(forged_cue),
            "start_tc_ms": forged_cue.start_tc_ms,
            "end_tc_ms": forged_cue.end_tc_ms,
        }
    )
    forged = result.model_copy(
        update={"cues": (forged_cue,), "cue_evidence": (forged_evidence,)}
    )
    with pytest.raises(
        WindowEvidenceError,
        match="exact verified turn bounds",
    ):
        validate_window_draft(data, forged)
