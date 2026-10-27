# Copyright 2026 Transcription Module contributors

"""Audio-bounded visual speech can await speaker confirmation."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
from transcription_module.window_evidence import validate_window_draft

if TYPE_CHECKING:
    from pathlib import Path


def test_audio_bounded_visual_insertion_can_await_speaker(
    tmp_path: Path,
) -> None:
    """One exact VAD interval supplies timing without person authority."""
    original = load_policy_window_input(tmp_path)
    vad = original.vad_intervals[0]
    first = original.frames[0].model_copy(
        update={"pts_ms": vad.start_tc_ms + 100}
    )
    second = first.model_copy(
        update={
            "frame_id": FrameId("frame-speakerless-2"),
            "frame_index": first.frame_index + 1,
            "pts_ms": vad.end_tc_ms - 100,
            "raw_pts": vad.end_tc_ms - 100,
            "raw_pts_time": f"{(vad.end_tc_ms - 100) / 1000:.3f}",
            "best_effort_timestamp": vad.end_tc_ms - 100,
            "best_effort_timestamp_time": (
                f"{(vad.end_tc_ms - 100) / 1000:.3f}"
            ),
        }
    )
    frames = (first, second, *original.frames[1:])
    data = original.model_copy(
        update={
            "asr_segments": (),
            "subtitle_cues": (),
            "frames": frames,
            "verified_turns": (),
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
                            ("화자 확인 전 발화",)
                            if frame.frame_id in {first.frame_id, second.frame_id}
                            else ()
                        ),
                    )
                    for frame in frames
                ),
                candidates=(
                    RawVisualCandidate(
                        candidate_id="candidate-speakerless",
                        complete_text="화자 확인 전 발화",
                        proposed_role=RawVisualRole.SPEECH_CAPTION,
                        supporting_frame_ids=(first.frame_id, second.frame_id),
                        evidence_for=("완전한 발화 자막이 보인다",),
                    ),
                ),
            ),
        }
    )
    observation = make_observation(
        data,
        ObservationSpec(
            observation_id="obs-speakerless",
            frame_ids=(str(first.frame_id), str(second.frame_id)),
            frame_pts_ms=(first.pts_ms, second.pts_ms),
            text="화자 확인 전 발화",
            vad_id=vad.interval_id,
            turn_id=None,
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
                candidate_id="candidate-speakerless",
                kind=CandidateDispositionKind.PROVISIONAL_INSERTION,
                evidence_ids=("vision:obs-speakerless", vad.interval_id),
            ),
        ),
    )

    result = reconcile_post_drafter_window(data, draft)

    provisional = result.provisional_visual_insertions[0]
    assert (
        provisional.start_tc_ms,
        provisional.end_tc_ms,
        provisional.speaker,
        provisional.turn_id,
    ) == (vad.start_tc_ms, vad.end_tc_ms, None, None)
    validate_window_draft(data, result)
