# Copyright 2026 Transcription Module contributors

"""Correction receipts and cue identities are deterministic and traceable."""

from __future__ import annotations

from typing import Final

import pytest
from pydantic import ValidationError

from transcription_module.correction_provenance import (
    build_correction_receipt,
    text_digest,
    validate_correction_receipt,
)
from transcription_module.correction_receipts import (
    CorrectionReceipt,
    CorrectionReceiptSet,
)
from transcription_module.cue_provenance import (
    CueIdentityInput,
    CueProvenanceError,
    CueProvenanceRecord,
    CueProvenanceSet,
    CueProvenanceValidationInput,
    assign_cue_ids,
    validate_cue_provenance,
)
from transcription_module.models import (
    Cue,
    CueId,
    FrameId,
    Milliseconds,
    RunId,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.visual_correction import (
    CandidateOrigin,
    TextSlice,
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import (
    CorrectionAction,
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_observations import (
    VisualObservation,
    VisualObservationSet,
)

RUN_ID: Final = RunId("deterministic-provenance-run")
POLICY_SHA: Final = policy_digest(default_evidence_policy())
SOURCE_SHA: Final = "a" * 64
BEFORE = "서울에서 다시 만났어"
RESULT = "서울에서 다시 만났어요"


def _observation() -> VisualObservation:
    return VisualObservation(
        observation_id="observation-local",
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        role=VisualRole.SPEECH_SUPPORTING,
        state=EvidenceState.SUPPORTED,
        semantic_relation=SemanticRelation.EXACT,
        frame_ids=(FrameId("f001"),),
        frame_pts_ms=(1_200,),
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        complete_text=RESULT,
        vad_interval_ids=("vad-1",),
        turn_ids=("turn-1",),
        caption_regime_supported=True,
        temporal_alignment_supported=True,
        speech_presence_supported=True,
        evidence_for=("exact_ko_orig", "vad_speech", "verified_turn"),
    )


def _request(before: str = BEFORE) -> VisualCorrectionRequest:
    return VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(),
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text=before,
        submitted_result=RESULT,
        source_slice=TextSlice(start=len(before) - 1, end=len(before)),
        candidate_slice=TextSlice(start=len(RESULT) - 2, end=len(RESULT)),
        speaker="루미",
    )


def _receipt() -> CorrectionReceipt:
    request = _request()
    validated = validate_visual_correction(request)
    return build_correction_receipt(SOURCE_SHA, request, validated)


def _transcript(cue_id: CueId, speech: str = RESULT) -> Transcript:
    return Transcript(
        run_id=RUN_ID,
        workflow_status=WorkflowStatus.PROCESSING,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(2_000),
        cues=(
            Cue(
                sequence=1,
                cue_id=cue_id,
                start_tc_ms=Milliseconds(1_000),
                end_tc_ms=Milliseconds(1_800),
                speech=speech,
                speaker="루미",
                uncertainty=Uncertainty.CERTAIN,
            ),
        ),
        vision_evidence=(),
    )


def _provenance(cue_id: CueId, receipt_id: str) -> CueProvenanceSet:
    return CueProvenanceSet(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        records=(
            CueProvenanceRecord(
                cue_id=cue_id,
                era=EvidenceEra.POLICY_BOUND,
                policy_digest=POLICY_SHA,
                source_digest=SOURCE_SHA,
                source_evidence_ids=(
                    "asr:3",
                    "subtitle:sub-orig-2",
                    "vision:observation-local",
                ),
                asr_ordinals=(3,),
                subtitle_track_ids=("automatic-ko-orig",),
                subtitle_cue_ids=("sub-orig-2",),
                observation_ids=("observation-local",),
                correction_receipt_id=receipt_id,
                start_tc_ms=1_000,
                end_tc_ms=1_800,
                speech_digest=text_digest(RESULT),
                speaker="루미",
                existing_cue_preserved=False,
            ),
        ),
    )


def _validation_input(
    *,
    cue_id: CueId,
    provenance: CueProvenanceSet,
    receipts: CorrectionReceiptSet,
    transcript: Transcript | None = None,
) -> CueProvenanceValidationInput:
    return CueProvenanceValidationInput(
        transcript=_transcript(cue_id) if transcript is None else transcript,
        provenance=provenance,
        corrections=receipts,
        observations=VisualObservationSet(
            run_id=RUN_ID,
            era=EvidenceEra.POLICY_BOUND,
            policy_digest=POLICY_SHA,
            observations=(_observation(),),
        ),
        source_digest=SOURCE_SHA,
        policy_digest=POLICY_SHA,
    )


def test_receipt_replay_is_byte_identical_and_all_digests_recompute() -> None:
    """The same request produces one exact receipt identity and JSON payload."""
    first = _receipt()
    second = _receipt()

    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)
    assert first.before_digest == text_digest(BEFORE)
    assert first.result_digest == text_digest(RESULT)
    assert first.candidate_digest == text_digest(RESULT)
    assert first.source_slice == TextSlice(start=len(BEFORE) - 1, end=len(BEFORE))
    assert first.candidate_slice == TextSlice(start=len(RESULT) - 2, end=len(RESULT))
    validate_correction_receipt(first, SOURCE_SHA, _request())


def test_slice_free_insert_receipt_replays_with_canonical_slice_identity() -> None:
    """Builder-derived insertion slices participate in one canonical digest."""
    candidate = "화면으로만 복원되는 발화"
    observation = VisualObservation(
        observation_id="observation-insert",
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        role=VisualRole.SPEECH_SUPPORTING,
        state=EvidenceState.SUPPORTED,
        semantic_relation=SemanticRelation.EXACT,
        frame_ids=(FrameId("f010"), FrameId("f011")),
        frame_pts_ms=(2_000, 2_200),
        start_tc_ms=2_000,
        end_tc_ms=2_600,
        complete_text=candidate,
        vad_interval_ids=("vad-insert",),
        turn_ids=("turn-insert",),
        caption_regime_supported=True,
        temporal_alignment_supported=True,
        speech_presence_supported=True,
        evidence_for=("two_pts", "vad_speech", "verified_turn"),
    )
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.INSERT_CUE,
        observation=observation,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text="",
        submitted_result=candidate,
        speaker="루미",
    )
    receipt = build_correction_receipt(
        SOURCE_SHA,
        request,
        validate_visual_correction(request),
    )

    replay_request = request.model_copy(
        update={"candidate_slice": receipt.candidate_slice}
    )
    validate_correction_receipt(receipt, SOURCE_SHA, replay_request)

    forged_request = request.model_copy(
        update={"candidate_slice": TextSlice(start=0, end=2)}
    )
    with pytest.raises(CueProvenanceError, match="canonical insertion slice"):
        _ = build_correction_receipt(
            SOURCE_SHA,
            forged_request,
            validate_visual_correction(forged_request),
        )


def test_keep_receipt_self_proves_unchanged_source() -> None:
    """An unchanged cue still carries one deterministic KEEP receipt."""
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.KEEP,
        observation=_observation(),
        before_text=BEFORE,
        submitted_result=BEFORE,
        speaker="루미",
    )
    receipt = build_correction_receipt(
        SOURCE_SHA,
        request,
        validate_visual_correction(request),
    )

    assert receipt.action is CorrectionAction.KEEP
    assert receipt.before_text == receipt.result_text == BEFORE
    assert receipt.before_digest == receipt.result_digest == text_digest(BEFORE)
    validate_correction_receipt(receipt, SOURCE_SHA, request)


def test_changed_before_text_makes_a_receipt_stale() -> None:
    """A receipt cannot be replayed over another exact before-text digest."""
    receipt = _receipt()
    changed = _request(before="서울에서 또 만났어")

    with pytest.raises(CueProvenanceError, match="stale correction receipt"):
        validate_correction_receipt(receipt, SOURCE_SHA, changed)


def test_candidate_digest_or_slice_forgery_is_rejected() -> None:
    """Changing candidate bytes or their exact slice fails at model or validation."""
    receipt = _receipt()
    payload = receipt.model_dump(mode="json")
    payload["candidate_digest"] = "b" * 64

    with pytest.raises(ValidationError, match="candidate digest"):
        _ = type(receipt).model_validate(payload)

    forged = receipt.model_copy(
        update={"candidate_slice": TextSlice(start=0, end=2)}
    )
    with pytest.raises(CueProvenanceError, match="stale correction receipt"):
        validate_correction_receipt(forged, SOURCE_SHA, _request())


def test_new_cue_ids_are_stable_and_existing_ids_are_preserved() -> None:
    """New evidence derives one stable UUID while existing IDs remain unchanged."""
    new_input = CueIdentityInput(
        era=EvidenceEra.POLICY_BOUND,
        source_evidence_ids=(
            "asr:3",
            "subtitle:sub-orig-2",
            "vision:observation-local",
        ),
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        speech="서울에서   다시 만났어요",
    )
    existing = CueIdentityInput(
        era=EvidenceEra.POLICY_BOUND,
        existing_cue_id=CueId("existing-cue-id"),
        source_evidence_ids=("asr:4",),
        start_tc_ms=2_000,
        end_tc_ms=2_800,
        speech="기존 큐",
    )

    first = assign_cue_ids(RUN_ID, POLICY_SHA, (new_input, existing))
    second = assign_cue_ids(RUN_ID, POLICY_SHA, (new_input, existing))

    assert first == second
    assert first[1] == CueId("existing-cue-id")
    assert first[0] != first[1]


def test_duplicate_or_injected_id_collision_is_rejected() -> None:
    """Two output cues can never share an identity, even under factory failure."""
    inputs = tuple(
        CueIdentityInput(
            era=EvidenceEra.POLICY_BOUND,
            source_evidence_ids=(f"asr:{ordinal}",),
            start_tc_ms=ordinal * 1_000,
            end_tc_ms=ordinal * 1_000 + 500,
            speech=f"발화 {ordinal}",
        )
        for ordinal in (1, 2)
    )

    with pytest.raises(CueProvenanceError, match="collision"):
        _ = assign_cue_ids(
            RUN_ID,
            POLICY_SHA,
            inputs,
            id_factory=lambda _payload: CueId("forced-collision"),
        )


def test_legacy_identity_must_be_preserved_and_cannot_be_synthesized() -> None:
    """Legacy cues require their exact existing ID and reject silent rewriting."""
    existing = CueIdentityInput(
        era=EvidenceEra.LEGACY,
        existing_cue_id=CueId("legacy-cue"),
        source_evidence_ids=("legacy-source",),
        start_tc_ms=0,
        end_tc_ms=500,
        speech="기존 발화",
    )
    missing = existing.model_copy(update={"existing_cue_id": None})

    assert assign_cue_ids(RUN_ID, POLICY_SHA, (existing,)) == (
        CueId("legacy-cue"),
    )
    with pytest.raises(CueProvenanceError, match="legacy cue ID"):
        _ = assign_cue_ids(RUN_ID, POLICY_SHA, (missing,))


def test_legacy_provenance_cannot_rewrite_an_existing_transcript_id() -> None:
    """Legacy provenance with a different cue ID is rejected as a rewrite."""
    speech = "기존 발화"
    receipt = CorrectionReceipt(
        receipt_id="legacy-keep",
        era=EvidenceEra.LEGACY,
        action=CorrectionAction.KEEP,
        source_digest=SOURCE_SHA,
        input_digest="b" * 64,
        before_text=speech,
        result_text=speech,
        before_digest=text_digest(speech),
        result_digest=text_digest(speech),
        observation_ids=(),
    )
    validation = CueProvenanceValidationInput(
        transcript=_transcript(CueId("legacy-existing-id"), speech=speech),
        provenance=CueProvenanceSet(
            run_id=RUN_ID,
            era=EvidenceEra.LEGACY,
            records=(
                CueProvenanceRecord(
                    cue_id=CueId("rewritten-legacy-id"),
                    era=EvidenceEra.LEGACY,
                    source_digest=SOURCE_SHA,
                    source_evidence_ids=("legacy-source",),
                    correction_receipt_id=receipt.receipt_id,
                    start_tc_ms=1_000,
                    end_tc_ms=1_800,
                    speech_digest=text_digest(speech),
                    speaker="루미",
                    existing_cue_preserved=True,
                ),
            ),
        ),
        corrections=CorrectionReceiptSet(
            run_id=RUN_ID,
            era=EvidenceEra.LEGACY,
            receipts=(receipt,),
        ),
        observations=VisualObservationSet(
            run_id=RUN_ID,
            era=EvidenceEra.LEGACY,
            observations=(),
        ),
        source_digest=SOURCE_SHA,
    )

    with pytest.raises(CueProvenanceError, match="rewrite"):
        validate_cue_provenance(validation)


def test_complete_provenance_validates_every_cue_receipt_and_observation() -> None:
    """One cue maps to its exact source, observation, receipt, and speech digest."""
    receipt = _receipt()
    cue_id = assign_cue_ids(
        RUN_ID,
        POLICY_SHA,
        (
            CueIdentityInput(
                era=EvidenceEra.POLICY_BOUND,
                source_evidence_ids=(
                    "asr:3",
                    "subtitle:sub-orig-2",
                    "vision:observation-local",
                ),
                start_tc_ms=1_000,
                end_tc_ms=1_800,
                speech=RESULT,
            ),
        ),
    )[0]
    receipts = CorrectionReceiptSet(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        receipts=(receipt,),
    )
    provenance = _provenance(cue_id, receipt.receipt_id)

    validate_cue_provenance(
        _validation_input(
            cue_id=cue_id,
            provenance=provenance,
            receipts=receipts,
        )
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing_receipt", "foreign_policy", "stale_source", "rewritten_id"],
)
def test_missing_cross_policy_stale_or_rewritten_provenance_fails(
    mutation: str,
) -> None:
    """All stale reference classes fail before a transcript mutation is possible."""
    receipt = _receipt()
    cue_id = CueId("current-cue-id")
    receipts = CorrectionReceiptSet(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        receipts=() if mutation == "missing_receipt" else (receipt,),
    )
    provenance = _provenance(cue_id, receipt.receipt_id)
    source_digest = SOURCE_SHA
    policy_sha = POLICY_SHA
    transcript = _transcript(cue_id)
    if mutation == "foreign_policy":
        policy_sha = "b" * 64
    elif mutation == "stale_source":
        source_digest = "b" * 64
    elif mutation == "rewritten_id":
        transcript = _transcript(CueId("legacy-existing-id"))

    validation = _validation_input(
        cue_id=cue_id,
        provenance=provenance,
        receipts=receipts,
        transcript=transcript,
    ).model_copy(
        update={"source_digest": source_digest, "policy_digest": policy_sha}
    )

    with pytest.raises(CueProvenanceError):
        validate_cue_provenance(validation)
