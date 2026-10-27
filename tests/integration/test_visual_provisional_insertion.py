# Copyright 2026 Transcription Module contributors

"""Unaccepted cue-less visual speech remains non-exportable evidence."""

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
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
)
from transcription_module.window_evidence_models import WindowVerifiedTurn

if TYPE_CHECKING:
    from pathlib import Path


def test_unaccepted_visual_insertion_remains_non_exportable(
    tmp_path: Path,
) -> None:
    """One audio turn is preserved without creating an exportable cue."""
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
    data = original.model_copy(
        update={
            "asr_segments": (),
            "subtitle_cues": (),
            "frames": frames,
            "verified_turns": (turn,),
            "visual_review": WindowVisualReviewResult(
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
                            ("확인 전 복원 발화",)
                            if str(frame.frame_id) in {"frame-1", "frame-1b"}
                            else ()
                        ),
                    )
                    for frame in frames
                ),
                candidates=(
                    RawVisualCandidate(
                        candidate_id="candidate-provisional",
                        complete_text="확인 전 복원 발화",
                        proposed_role=RawVisualRole.SPEECH_CAPTION,
                        supporting_frame_ids=(
                            original.frames[0].frame_id,
                            extra_frame.frame_id,
                        ),
                        evidence_for=("완전한 발화 자막이 보인다",),
                    ),
                ),
            ),
        }
    )
    observation = make_observation(
        data,
        ObservationSpec(
            observation_id="obs-provisional",
            frame_ids=("frame-1", "frame-1b"),
            frame_pts_ms=(1_200, 1_400),
            text="확인 전 복원 발화",
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
                candidate_id="candidate-provisional",
                kind=CandidateDispositionKind.PROVISIONAL_INSERTION,
                evidence_ids=("vision:obs-provisional", turn.turn_id),
            ),
        ),
    )

    result = reconcile_post_drafter_window(data, draft)

    assert result.cues == ()
    assert len(result.provisional_visual_insertions) == 1
    provisional = result.provisional_visual_insertions[0]
    assert (
        provisional.candidate_id,
        provisional.start_tc_ms,
        provisional.end_tc_ms,
        provisional.speaker,
    ) == (
        "candidate-provisional",
        turn.start_tc_ms,
        turn.end_tc_ms,
        turn.speaker,
    )
    assert provisional.exportable is False
    validate_window_draft(data, result)

    forged = result.model_copy(
        update={
            "provisional_visual_insertions": (
                provisional.model_copy(update={"start_tc_ms": 1_100}),
            )
        }
    )
    with pytest.raises(
        WindowEvidenceError,
        match="exact verified turn bounds",
    ):
        validate_window_draft(data, forged)
