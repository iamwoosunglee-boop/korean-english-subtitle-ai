# Copyright 2026 Transcription Module contributors

"""Legitimate REPLACE_SPAN and INSERT_CUE policy-window claims."""

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


def replacement_draft(
    data: WindowDraftInput,
    *,
    origin: CandidateOrigin = CandidateOrigin.KO_ORIG,
) -> WindowDraft:
    """Build one exact ASR-before replacement from an admitted candidate."""
    before = "서울에서 다시 만났어"
    result = "서울에서 다시 만났어요"
    cue = DraftCue(
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        speech=result,
        speaker="화자A",
    )
    observation = make_observation(
        data,
        ObservationSpec(
            "obs-replace",
            ("frame-1",),
            (1_200,),
            result,
            "vad:1",
            None,
        ),
    )
    request = VisualCorrectionRequest(
        policy_digest=policy_digest(data),
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=observation,
        candidate_origin=origin,
        before_text=before,
        submitted_result=result,
        source_slice=TextSlice(start=len(before) - 1, end=len(before)),
        candidate_slice=TextSlice(start=len(result) - 2, end=len(result)),
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
            ("asr:1", "subtitle:automatic-ko-orig:1", "vision:obs-replace")
            if origin is CandidateOrigin.KO_ORIG
            else ("asr:1", "vision:obs-replace")
        ),
        asr_ordinals=(1,),
        subtitle_track_ids=(
            ("automatic-ko-orig",)
            if origin is CandidateOrigin.KO_ORIG
            else ()
        ),
        subtitle_cue_ids=(
            ("automatic-ko-orig:1",)
            if origin is CandidateOrigin.KO_ORIG
            else ()
        ),
        observation_ids=(observation.observation_id,),
        vad_interval_ids=("vad:1",),
        turn_ids=(),
        requested_action=CorrectionAction.REPLACE_SPAN,
        candidate_origin=origin,
        correction_receipt_id=receipt.receipt_id,
    )
    claim = ClaimBundle(cue, observation, receipt, evidence)
    return make_draft(data, (claim,))


def insertion_draft(data: WindowDraftInput) -> WindowDraft:
    """Build one slice-free visual-only insertion through the public builder."""
    speech = "화면으로만 복원되는 새 발화"
    cue = DraftCue(
        start_tc_ms=3_000,
        end_tc_ms=3_800,
        speech=speech,
        speaker="화자B",
    )
    observation = make_observation(
        data,
        ObservationSpec(
            "obs-insert",
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
        source_evidence_ids=("vision:obs-insert",),
        observation_ids=(observation.observation_id,),
        vad_interval_ids=("vad:2",),
        turn_ids=(),
        requested_action=CorrectionAction.INSERT_CUE,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        correction_receipt_id=receipt.receipt_id,
    )
    claim = ClaimBundle(cue, observation, receipt, evidence)
    return make_draft(data, (claim,))


__all__ = ["insertion_draft", "replacement_draft"]
