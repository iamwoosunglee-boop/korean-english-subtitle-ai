# Copyright 2026 Transcription Module contributors

"""Categorical visual evidence authorizes only bounded source-preserving edits."""

from __future__ import annotations

from typing import Final, cast

import pytest
from pydantic import ValidationError

from transcription_module.models import FrameId
from transcription_module.visual_correction import (
    CandidateOrigin,
    CorrectionValidationError,
    TextSlice,
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import (
    ConflictRule,
    CorrectionAction,
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_observations import VisualObservation

POLICY_SHA: Final = policy_digest(default_evidence_policy())
ONE_FRAME: Final = (FrameId("f-local"),)
ONE_PTS: Final = (3_200,)
TWO_FRAMES: Final = (FrameId("f-insert-a"), FrameId("f-insert-b"))
TWO_PTS: Final = (5_000, 5_300)


def _observation(**updates: object) -> VisualObservation:
    payload: dict[str, object] = {
        "observation_id": "observation-local",
        "era": EvidenceEra.POLICY_BOUND,
        "policy_digest": POLICY_SHA,
        "role": VisualRole.SPEECH_SUPPORTING,
        "state": EvidenceState.SUPPORTED,
        "semantic_relation": SemanticRelation.EXACT,
        "frame_ids": ONE_FRAME,
        "frame_pts_ms": ONE_PTS,
        "start_tc_ms": 3_000,
        "end_tc_ms": 3_600,
        "complete_text": "서울에서 다시 만났어요",
        "vad_interval_ids": ("vad-local",),
        "turn_ids": ("turn-local",),
        "caption_regime_supported": True,
        "temporal_alignment_supported": True,
        "speech_presence_supported": True,
        "conflicts": (),
        "evidence_for": (
            "caption_regime",
            "temporal_alignment",
            "speech_presence",
            "verified_turn",
        ),
        "evidence_against": (),
        "missing_evidence": (),
    }
    payload.update(updates)
    state = cast("EvidenceState", payload["state"])
    if state is not EvidenceState.SUPPORTED:
        payload["evidence_for"] = ()
    if state is EvidenceState.CONFLICTED and not payload["evidence_against"]:
        payload["evidence_against"] = ("conflicting_evidence",)
    if state is EvidenceState.INSUFFICIENT and not payload["missing_evidence"]:
        payload["missing_evidence"] = ("missing_evidence",)
    return VisualObservation.model_validate(payload)


def test_exact_ko_orig_candidate_can_repair_one_local_span() -> None:
    """A local replacement is reconstructed only from an exact candidate slice."""
    before = "서울에서 다시 만났어"
    candidate = "서울에서 다시 만났어요"
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(complete_text=candidate),
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text=before,
        submitted_result=candidate,
        source_slice=TextSlice(start=len(before) - 1, end=len(before)),
        candidate_slice=TextSlice(start=len(candidate) - 2, end=len(candidate)),
        speaker="루미",
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.REPLACE_SPAN
    assert validated.result_text == candidate
    assert validated.candidate_text == candidate


def test_exact_speech_following_visual_caption_can_repair_one_local_span() -> None:
    """Strong complete visual speech evidence can correct an existing ASR cue."""
    before = "서울에서 다시 만났어"
    candidate = "서울에서 다시 만났어요"
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(complete_text=candidate),
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text=before,
        submitted_result=candidate,
        source_slice=TextSlice(start=len(before) - 1, end=len(before)),
        candidate_slice=TextSlice(start=len(candidate) - 2, end=len(candidate)),
        speaker="루미",
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.REPLACE_SPAN
    assert validated.result_text == candidate
    assert validated.candidate_text == candidate


def test_exact_candidate_can_replace_a_complete_cue() -> None:
    """Full-cue repair uses the complete admitted candidate and no free rewrite."""
    before = "브룸 타키 불러볼게"
    candidate = "브룸 타키를 불러볼게요"
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(complete_text=candidate),
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text=before,
        submitted_result=candidate,
        source_slice=TextSlice(start=0, end=len(before)),
        candidate_slice=TextSlice(start=0, end=len(candidate)),
        speaker="한지아",
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.REPLACE_SPAN
    assert validated.result_text == candidate


def test_strong_visual_only_insertion_allows_only_fixed_normalizations() -> None:
    """NFC, line join, and whitespace collapse are the entire insertion surface."""
    observation = _observation(
        complete_text="화면으로만\n   복원되는 발화",
        frame_ids=TWO_FRAMES,
        frame_pts_ms=TWO_PTS,
        vad_interval_ids=("vad-insert",),
        turn_ids=("turn-insert",),
    )
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.INSERT_CUE,
        observation=observation,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text="",
        submitted_result="화면으로만 복원되는 발화",
        speaker="루미",
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.INSERT_CUE
    assert validated.result_text == "화면으로만 복원되는 발화"


def test_forged_character_outside_candidate_slice_is_rejected() -> None:
    """A fluent extra character cannot be smuggled into an approved insertion."""
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.INSERT_CUE,
        observation=_observation(
            complete_text="화면으로만 복원되는 발화",
            frame_ids=TWO_FRAMES,
            frame_pts_ms=TWO_PTS,
        ),
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text="",
        submitted_result="화면으로만 복원되는 발화!",
        speaker="루미",
    )

    with pytest.raises(CorrectionValidationError, match="candidate"):
        _ = validate_visual_correction(request)


@pytest.mark.parametrize(
    ("role", "relation", "expected_action"),
    [
        (VisualRole.NON_SPEECH, SemanticRelation.EXACT, CorrectionAction.KEEP),
        (
            VisualRole.SPEECH_SUPPORTING,
            SemanticRelation.CONDENSED,
            CorrectionAction.KEEP,
        ),
        (
            VisualRole.SPEECH_SUPPORTING,
            SemanticRelation.PARAPHRASE,
            CorrectionAction.KEEP,
        ),
    ],
)
def test_editorial_or_non_exact_text_preserves_source(
    role: VisualRole,
    relation: SemanticRelation,
    expected_action: CorrectionAction,
) -> None:
    """Verbatim telops and condensed captions never become replacement authority."""
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(role=role, semantic_relation=relation),
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text="원문 발화",
        submitted_result="서울에서 다시 만났어요",
        source_slice=TextSlice(start=0, end=5),
        candidate_slice=TextSlice(start=0, end=5),
        speaker="루미",
    )

    validated = validate_visual_correction(request)

    assert validated.action is expected_action
    assert validated.result_text == "원문 발화"


@pytest.mark.parametrize(
    ("observation", "speaker"),
    [
        (
            _observation(
                role=VisualRole.UNKNOWN,
                state=EvidenceState.CONFLICTED,
                semantic_relation=SemanticRelation.UNRELATED,
                speech_presence_supported=False,
                evidence_against=("vad_negative",),
            ),
            "루미",
        ),
        (
            _observation(conflicts=(ConflictRule.CUT_CHANGE,)),
            "루미",
        ),
        (
            _observation(conflicts=(ConflictRule.STYLE_CHANGE,)),
            "루미",
        ),
        (
            _observation(conflicts=(ConflictRule.OWNERSHIP_CONFLICT,)),
            "루미",
        ),
        (
            _observation(conflicts=(ConflictRule.OVERLAP_AMBIGUITY,)),
            "루미",
        ),
        (
            _observation(
                state=EvidenceState.INSUFFICIENT,
                missing_evidence=("unreadable_frame",),
            ),
            "루미",
        ),
        (_observation(frame_ids=ONE_FRAME, frame_pts_ms=ONE_PTS), None),
    ],
)
def test_conflicted_incomplete_or_unknown_turn_holds_without_mutation(
    observation: VisualObservation,
    speaker: str | None,
) -> None:
    """Unsafe visual-only insertion is converted to HOLD with unchanged speech."""
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.INSERT_CUE,
        observation=observation,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text="",
        submitted_result=observation.complete_text or "",
        speaker=speaker,
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.HOLD_FOR_CONFIRMATION
    assert validated.result_text == ""


@pytest.mark.parametrize(
    "updates",
    [
        {"caption_regime_supported": False},
        {"temporal_alignment_supported": False},
        {"speech_presence_supported": False},
        {"vad_interval_ids": ()},
    ],
)
def test_missing_regime_or_vad_holds(
    updates: dict[str, object],
) -> None:
    """Every text-evidence precondition is independently required."""
    observation = _observation(**updates)
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=observation,
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text="원문 발화",
        submitted_result="서울에서 다시 만났어요",
        source_slice=TextSlice(start=0, end=5),
        candidate_slice=TextSlice(start=0, end=5),
        speaker="루미",
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.HOLD_FOR_CONFIRMATION
    assert validated.result_text == "원문 발화"


def test_foreign_policy_observation_is_rejected_before_action_routing() -> None:
    """A stale observation cannot authorize KEEP, HOLD, or a speech mutation."""
    foreign = _observation(policy_digest="b" * 64)
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.KEEP,
        observation=foreign,
        before_text="원문 발화",
        submitted_result="원문 발화",
    )

    with pytest.raises(CorrectionValidationError, match="current policy"):
        _ = validate_visual_correction(request)


def test_non_admitted_candidate_origin_holds_without_slice_use() -> None:
    """A replacement without an admitted evidence origin remains held."""
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(),
        candidate_origin=None,
        before_text="원문 발화",
        submitted_result="서울에서 다시 만났어요",
        source_slice=TextSlice(start=0, end=5),
        candidate_slice=TextSlice(start=0, end=5),
        speaker="루미",
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.HOLD_FOR_CONFIRMATION
    assert validated.result_text == "원문 발화"


def test_visual_observation_has_no_numeric_confidence_escape_hatch() -> None:
    """A raw confidence value is not part of the categorical authority contract."""
    payload = _observation().model_dump(mode="json")
    payload["confidence"] = 1.0

    with pytest.raises(ValidationError):
        _ = VisualObservation.model_validate(payload)


def test_confirmed_field_conflict_always_holds() -> None:
    """Visual evidence cannot overwrite a field already confirmed by a person."""
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=_observation(),
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text="사람이 확정한 발화",
        submitted_result="서울에서 다시 만났어요",
        source_slice=TextSlice(start=0, end=9),
        candidate_slice=TextSlice(start=0, end=9),
        speaker="루미",
        confirmed_field=True,
    )

    validated = validate_visual_correction(request)

    assert validated.action is CorrectionAction.HOLD_FOR_CONFIRMATION
    assert validated.result_text == "사람이 확정한 발화"


@pytest.mark.parametrize(
    "frame_pts_ms",
    [(3_200,), (3_300, 3_200)],
)
def test_policy_bound_observation_requires_exact_ordered_frame_pts(
    frame_pts_ms: tuple[int, ...],
) -> None:
    """Frame identity and PTS order cannot be omitted, duplicated, or reordered."""
    with pytest.raises(ValidationError):
        _ = _observation(
            frame_ids=TWO_FRAMES,
            frame_pts_ms=frame_pts_ms,
        )
