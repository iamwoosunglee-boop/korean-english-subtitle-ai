# Copyright 2026 Transcription Module contributors

"""Valid Task 11 correction claims composed for Task 12 tests."""

from __future__ import annotations

from tests.integration.window_claim_support import (
    ClaimBundle,
    ObservationSpec,
    make_draft,
    make_observation,
    policy_digest,
    source_digest,
)
from transcription_module.correction_provenance import (
    build_correction_receipt,
    text_digest,
)
from transcription_module.drafting import DraftCue, WindowDraft, WindowDraftInput
from transcription_module.visual_correction import (
    CandidateOrigin,
    TextSlice,
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_evidence import window_cue_identity
from transcription_module.window_evidence_models import (
    WindowCueEvidence,
    WindowCueResolution,
)


def replacement_variant(
    data: WindowDraftInput,
    candidate_slice: TextSlice,
    *,
    observation_id: str = "obs-shared-correction",
) -> WindowDraft:
    """Build one valid correction of ASR source 1 from a shared observation."""
    before = "서울에서 다시 만났어"
    candidate = "서울에서 다시 만났어요"
    source_slice = TextSlice(start=len(before) - 1, end=len(before))
    replacement = candidate[candidate_slice.start : candidate_slice.end]
    result = before[: source_slice.start] + replacement + before[source_slice.end :]
    cue = DraftCue(
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        speech=result,
        speaker="화자A",
    )
    observation = make_observation(
        data,
        ObservationSpec(
            observation_id,
            ("frame-1",),
            (1_200,),
            candidate,
            "vad:1",
            None,
        ),
    )
    request = VisualCorrectionRequest(
        policy_digest=policy_digest(data),
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=observation,
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text=before,
        submitted_result=result,
        source_slice=source_slice,
        candidate_slice=candidate_slice,
        speaker=cue.speaker,
    )
    receipt = build_correction_receipt(
        source_digest(data), request, validate_visual_correction(request)
    )
    evidence = WindowCueEvidence(
        cue_index=0,
        cue_identity=window_cue_identity(cue),
        resolution=WindowCueResolution.AUTHORIZED,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        speech_digest=text_digest(cue.speech),
        speaker=cue.speaker,
        source_evidence_ids=(
            "asr:1",
            "subtitle:automatic-ko-orig:1",
            f"vision:{observation_id}",
        ),
        asr_ordinals=(1,),
        subtitle_track_ids=("automatic-ko-orig",),
        subtitle_cue_ids=("automatic-ko-orig:1",),
        observation_ids=(observation.observation_id,),
        vad_interval_ids=("vad:1",),
        turn_ids=(),
        requested_action=CorrectionAction.REPLACE_SPAN,
        candidate_origin=CandidateOrigin.KO_ORIG,
        correction_receipt_id=receipt.receipt_id,
    )
    return make_draft(data, (ClaimBundle(cue, observation, receipt, evidence),))


def insertion_variant(
    data: WindowDraftInput,
    *,
    observation_id: str,
    turn_id: str,
    speaker: str,
) -> WindowDraft:
    """Build one visual-only insertion with explicit observation and turn identity."""
    del turn_id
    speech = "화면으로만 복원되는 새 발화"
    cue = DraftCue(
        start_tc_ms=3_000,
        end_tc_ms=3_800,
        speech=speech,
        speaker=speaker,
    )
    observation = make_observation(
        data,
        ObservationSpec(
            observation_id,
            ("frame-2", "frame-3"),
            (3_200, 3_400),
            speech,
            "vad:2",
            None,
        ),
    )
    request = VisualCorrectionRequest(
        policy_digest=policy_digest(data),
        requested_action=CorrectionAction.INSERT_CUE,
        observation=observation,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text="",
        submitted_result=speech,
        speaker=cue.speaker,
    )
    receipt = build_correction_receipt(
        source_digest(data), request, validate_visual_correction(request)
    )
    evidence = WindowCueEvidence(
        cue_index=0,
        cue_identity=window_cue_identity(cue),
        resolution=WindowCueResolution.AUTHORIZED,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        speech_digest=text_digest(cue.speech),
        speaker=cue.speaker,
        source_evidence_ids=(f"vision:{observation_id}",),
        observation_ids=(observation_id,),
        vad_interval_ids=("vad:2",),
        turn_ids=(),
        requested_action=CorrectionAction.INSERT_CUE,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        correction_receipt_id=receipt.receipt_id,
    )
    return make_draft(data, (ClaimBundle(cue, observation, receipt, evidence),))


__all__ = ["insertion_variant", "replacement_variant"]
