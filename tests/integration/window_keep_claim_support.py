# Copyright 2026 Transcription Module contributors

"""Legitimate ASR, ko-orig, and simultaneous KEEP window claims."""

from __future__ import annotations

from dataclasses import dataclass

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
from transcription_module.drafting import (
    DraftCue,
    UnresolvedItem,
    UnresolvedReasonCode,
    WindowDraft,
    WindowDraftInput,
)
from transcription_module.visual_correction import (
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_evidence import window_cue_identity
from transcription_module.window_evidence_models import (
    WindowCueEvidence,
    WindowCueResolution,
)


@dataclass(frozen=True, slots=True)
class _KeepSpec:
    index: int
    start_ms: int
    end_ms: int
    speech: str
    speaker: str | None
    observation: ObservationSpec
    asr_ordinal: int | None = None
    subtitle_cue_id: str | None = None


def automatic_keep_draft(data: WindowDraftInput) -> WindowDraft:
    """Keep exact admitted automatic-ko-orig speech."""
    claim = _keep_claim(
        data,
        _KeepSpec(
            index=0,
            start_ms=1_000,
            end_ms=1_800,
            speech="서울에서 다시 만났어요",
            speaker="화자A",
            observation=ObservationSpec(
                "obs-subtitle-keep",
                ("frame-1",),
                (1_200,),
                "서울에서 다시 만났어요",
                "vad:1",
                None,
            ),
            subtitle_cue_id="automatic-ko-orig:1",
        ),
    )
    return make_draft(data, (claim,))


def asr_keep_draft(data: WindowDraftInput) -> WindowDraft:
    """Keep exact stable-ordinal ASR speech."""
    claim = _keep_claim(
        data,
        _KeepSpec(
            index=0,
            start_ms=1_000,
            end_ms=1_800,
            speech="서울에서 다시 만났어",
            speaker="화자A",
            observation=ObservationSpec(
                "obs-asr-keep",
                ("frame-1",),
                (1_200,),
                "서울에서 다시 만났어",
                "vad:1",
                None,
            ),
            asr_ordinal=1,
        ),
    )
    return make_draft(data, (claim,))


def speakerless_keep_draft(data: WindowDraftInput) -> WindowDraft:
    """Keep exact ASR speech for later whole-transcript speaker inference."""
    claim = _keep_claim(
        data,
        _KeepSpec(
            index=0,
            start_ms=1_000,
            end_ms=1_800,
            speech="서울에서 다시 만났어",
            speaker=None,
            observation=ObservationSpec(
                "obs-speakerless-keep",
                ("frame-1",),
                (1_200,),
                "서울에서 다시 만났어",
                "vad:1",
                None,
            ),
            asr_ordinal=1,
        ),
    )
    return make_draft(data, (claim,))


def simultaneous_keep_draft(data: WindowDraftInput) -> WindowDraft:
    """Keep two same-time/same-text speakers with disjoint authority."""
    first = _simultaneous_claim(
        data,
        _KeepSpec(
            index=0,
            start_ms=3_000,
            end_ms=3_800,
            speech="동시에 대답해요",
            speaker="화자A",
            observation=ObservationSpec(
                "obs-same-a",
                ("frame-2",),
                (3_200,),
                "동시에 대답해요",
                "vad:2",
                None,
            ),
            asr_ordinal=3,
            subtitle_cue_id="automatic-ko-orig:2",
        ),
    )
    second = _simultaneous_claim(
        data,
        _KeepSpec(
            index=1,
            start_ms=3_000,
            end_ms=3_800,
            speech="동시에 대답해요",
            speaker="화자B",
            observation=ObservationSpec(
                "obs-same-b",
                ("frame-3",),
                (3_400,),
                "동시에 대답해요",
                "vad:2",
                None,
            ),
            asr_ordinal=4,
            subtitle_cue_id="automatic-ko-orig:3",
        ),
    )
    return make_draft(data, (first, second))


def simultaneous_unresolved_draft(data: WindowDraftInput) -> WindowDraft:
    """Hold same-time duplicate speech as one unresolved ownership event."""
    base = _keep_claim(
        data,
        _KeepSpec(
            index=0,
            start_ms=3_000,
            end_ms=3_800,
            speech="동시에 대답해요",
            speaker=None,
            observation=ObservationSpec(
                "obs-unresolved",
                ("frame-2", "frame-3"),
                (3_200, 3_400),
                "동시에 대답해요",
                "vad:2",
                None,
            ),
            asr_ordinal=3,
            subtitle_cue_id="automatic-ko-orig:2",
        ),
        resolution=WindowCueResolution.UNRESOLVED,
        action=CorrectionAction.HOLD_FOR_CONFIRMATION,
    )
    evidence = base.evidence.model_copy(
        update={
            "asr_ordinals": (3, 4),
            "subtitle_cue_ids": (
                "automatic-ko-orig:2",
                "automatic-ko-orig:3",
            ),
            "source_evidence_ids": (
                "asr:3",
                "asr:4",
                "subtitle:automatic-ko-orig:2",
                "subtitle:automatic-ko-orig:3",
                "vision:obs-unresolved",
            ),
        }
    )
    claim = ClaimBundle(base.cue, base.observation, base.receipt, evidence)
    draft = make_draft(data, (claim,))
    unresolved = UnresolvedItem(
        at_ms=claim.cue.start_tc_ms,
        field="speaker",
        reason="동시 발화의 화자 소유권 확인 필요",
        candidates=("화자A", "화자B"),
        reason_code=UnresolvedReasonCode.SAME_START_TURN_AMBIGUITY,
        target_cue_index=0,
        source_evidence_ids=claim.evidence.source_evidence_ids,
    )
    return draft.model_copy(update={"unresolved": (unresolved,)})


def _simultaneous_claim(
    data: WindowDraftInput,
    spec: _KeepSpec,
) -> ClaimBundle:
    return _keep_claim(data, spec)


def _keep_claim(
    data: WindowDraftInput,
    spec: _KeepSpec,
    *,
    resolution: WindowCueResolution = WindowCueResolution.AUTHORIZED,
    action: CorrectionAction = CorrectionAction.KEEP,
) -> ClaimBundle:
    cue = DraftCue(
        start_tc_ms=spec.start_ms,
        end_tc_ms=spec.end_ms,
        speech=spec.speech,
        speaker=spec.speaker,
    )
    observation = make_observation(data, spec.observation)
    request = VisualCorrectionRequest(
        policy_digest=policy_digest(data),
        requested_action=action,
        observation=observation,
        before_text=spec.speech,
        submitted_result=spec.speech,
        speaker=spec.speaker,
    )
    receipt = build_correction_receipt(
        source_digest(data), request, validate_visual_correction(request)
    )
    asr = () if spec.asr_ordinal is None else (spec.asr_ordinal,)
    subtitles = () if spec.subtitle_cue_id is None else (spec.subtitle_cue_id,)
    source_ids = (
        *(f"asr:{item}" for item in asr),
        *(f"subtitle:{item}" for item in subtitles),
        f"vision:{spec.observation.observation_id}",
    )
    evidence = WindowCueEvidence(
        cue_index=spec.index,
        cue_identity=window_cue_identity(cue),
        resolution=resolution,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        speech_digest=text_digest(cue.speech),
        speaker=cue.speaker,
        source_evidence_ids=source_ids,
        asr_ordinals=asr,
        subtitle_track_ids=("automatic-ko-orig",) if subtitles else (),
        subtitle_cue_ids=subtitles,
        observation_ids=(observation.observation_id,),
        vad_interval_ids=(spec.observation.vad_id,),
        turn_ids=(
            () if spec.observation.turn_id is None else (spec.observation.turn_id,)
        ),
        requested_action=action,
        correction_receipt_id=receipt.receipt_id,
    )
    return ClaimBundle(cue, observation, receipt, evidence)


__all__ = [
    "asr_keep_draft",
    "automatic_keep_draft",
    "simultaneous_keep_draft",
    "simultaneous_unresolved_draft",
]
