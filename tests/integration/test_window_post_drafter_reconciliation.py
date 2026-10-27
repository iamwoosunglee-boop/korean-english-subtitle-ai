# Copyright 2026 Transcription Module contributors

"""Deterministic non-reference recovery after window drafting."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.window_claim_support import (
    ObservationSpec,
    load_policy_window_input,
    make_observation,
    policy_digest,
    source_digest,
)
from tests.integration.window_keep_claim_support import automatic_keep_draft
from transcription_module._drafting_post_reconciliation import (
    reconcile_post_drafter_window,
)
from transcription_module.asr import AsrSegment
from transcription_module.correction_provenance import (
    build_correction_receipt,
    text_digest,
)
from transcription_module.drafting import DraftCue, WindowDraft, WindowDraftInput
from transcription_module.models import FrameId
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
)
from transcription_module.visual_observations import VisualObservation
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
    window_cue_identity,
)
from transcription_module.window_evidence_models import (
    WindowCueEvidence,
    WindowCueResolution,
    WindowSubtitleCue,
    WindowVadInterval,
    WindowVerifiedTurn,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "speech",
    [
        "너와의 모든 지금이",
        "네",
        "내려가서 쳐주다가",
        "좀 올라와서 쳐주다가",
        "맞아",
        "너무 재밌어요",
        "단독 공연",
        "아",
        "그러니까",
        "맞아요",
        "예",
    ],
)
def test_exact_ko_orig_vad_gap_pattern(tmp_path: Path, speech: str) -> None:
    """Insert normalized exact ko-orig speech with positive VAD."""
    data = _gap_input(tmp_path, speech)
    result = reconcile_post_drafter_window(data, _empty(data))
    assert tuple(cue.speech for cue in result.cues) == (speech,)
    assert result.cues[0].speaker == "화자A"
    assert result.cue_evidence[0].turn_ids == ("turn:gap",)
    assert (
        result.cues[0].start_tc_ms,
        result.cues[0].end_tc_ms,
    ) == (1_000, 1_800)
    assert result.cue_evidence[0].requested_action is CorrectionAction.KEEP
    validate_window_draft(data, result)


def test_gap_without_verified_turn_stays_uninserted(tmp_path: Path) -> None:
    """VAD and repeated ko-orig cannot create speakerless speech."""
    data = _gap_input(tmp_path, "취미").model_copy(update={"verified_turns": ()})

    result = reconcile_post_drafter_window(data, _empty(data))

    assert result.cues == ()
    assert result.corrections == ()


def test_gap_with_multiple_verified_turns_stays_uninserted(
    tmp_path: Path,
) -> None:
    """Competing turn authority cannot be assigned to one subtitle gap."""
    data = _gap_input(tmp_path, "취미")
    first = data.verified_turns[0].model_copy(update={"end_tc_ms": 1_250})
    data = data.model_copy(
        update={
            "vad_intervals": (
                WindowVadInterval(
                    interval_id="vad:1",
                    start_tc_ms=900,
                    end_tc_ms=1_300,
                ),
                WindowVadInterval(
                    interval_id="vad:2",
                    start_tc_ms=1_300,
                    end_tc_ms=1_900,
                ),
            ),
            "verified_turns": (
                first,
                first.model_copy(
                    update={
                        "turn_id": "turn:gap-other",
                        "start_tc_ms": 1_350,
                        "end_tc_ms": 1_800,
                        "cluster": "SPEAKER_01",
                        "speaker": "화자B",
                        "authority_digest": "b" * 64,
                        "verification_evidence_ids": ("evidence:gap-other",),
                        "verification_decision_ids": ("decision:gap-other",),
                    }
                ),
            ),
        }
    )

    result = reconcile_post_drafter_window(data, _empty(data))

    assert result.cues == ()
    assert result.corrections == ()


def test_gap_with_turn_outside_evidence_bounds_stays_uninserted(
    tmp_path: Path,
) -> None:
    """Overlap alone cannot authorize timing outside subtitle evidence."""
    data = _gap_input(tmp_path, "취미")
    data = data.model_copy(
        update={
            "verified_turns": (
                data.verified_turns[0].model_copy(
                    update={"start_tc_ms": 500, "end_tc_ms": 1_250}
                ),
            )
        }
    )

    result = reconcile_post_drafter_window(data, _empty(data))

    assert result.cues == ()
    assert result.corrections == ()


@pytest.mark.parametrize("invalid", ["split_vad", "broad_turn"])
def test_subtitle_gap_validator_rejects_forged_turn_envelope(
    tmp_path: Path,
    invalid: str,
) -> None:
    """Persisted subtitle-gap claims must replay one bounded turn."""
    data = _gap_input(tmp_path, "경계 검증")
    result = reconcile_post_drafter_window(data, _empty(data))
    if invalid == "split_vad":
        data = data.model_copy(
            update={
                "vad_intervals": (
                    data.vad_intervals[0].model_copy(update={"end_tc_ms": 1_300}),
                    WindowVadInterval(
                        interval_id="vad:2",
                        start_tc_ms=1_300,
                        end_tc_ms=1_900,
                    ),
                )
            }
        )
    else:
        data = data.model_copy(
            update={
                "verified_turns": (
                    data.verified_turns[0].model_copy(
                        update={"start_tc_ms": 500, "end_tc_ms": 1_250}
                    ),
                )
            }
        )

    with pytest.raises(WindowEvidenceError, match="subtitle gap"):
        validate_window_draft(data, result)


def test_adjacent_candidate_spans_prevent_duplicate(tmp_path: Path) -> None:
    """Treat adjacent cue text as coverage before declaring a gap."""
    data = load_policy_window_input(tmp_path)
    data = data.model_copy(
        update={
            "subtitle_cues": (
                data.subtitle_cues[0],
                data.subtitle_cues[0].model_copy(
                    update={"cue_id": "automatic-ko-orig:copy"}
                ),
            )
        }
    )
    draft = automatic_keep_draft(data)
    assert reconcile_post_drafter_window(data, draft) == draft


@pytest.mark.parametrize("missing", ["vad", "stability", "asr", "visual"])
def test_gap_abstains_without_safe_evidence(tmp_path: Path, missing: str) -> None:
    """Abstain when any required omission guard is absent or contradictory."""
    data = _gap_input(tmp_path, "복원하면 안 되는 말")
    draft = _empty(data)
    if missing == "vad":
        data = data.model_copy(update={"vad_intervals": ()})
    elif missing == "stability":
        data = data.model_copy(update={"subtitle_cues": data.subtitle_cues[:1]})
    elif missing == "asr":
        data = data.model_copy(
            update={
                "asr_segments": (
                    AsrSegment(
                        source_ordinal=99,
                        start_ms=1_000,
                        end_ms=1_800,
                        speech="서로 다른 발화",
                    ),
                )
            }
        )
    else:
        draft = draft.model_copy(
            update={"observations": (_observation(data, "서로 다른 화면 발화"),)}
        )
    result = reconcile_post_drafter_window(data, draft)
    assert result.cues == ()
    assert result.corrections == ()


def test_visual_and_ko_repair_malformed_519(tmp_path: Path) -> None:
    """Apply exact visual wording when independently corroborated by ko-orig."""
    malformed, restored = "아들아 나왔는데 요래.", "아들을 낳았는데 요래!"
    data = _source_input(tmp_path, malformed, restored)
    result = reconcile_post_drafter_window(
        data, _claim(data, malformed, restored, CorrectionAction.KEEP)
    )
    assert result.cues[0].speech == restored
    assert result.cue_evidence[0].candidate_origin is CandidateOrigin.KO_ORIG
    validate_window_draft(data, result)


def test_visual_supported_omission_without_speaker_stays_uninserted(
    tmp_path: Path,
) -> None:
    """Lexical agreement cannot replace verified speaker authority."""
    speech = "너무 좋다!"
    data = load_policy_window_input(tmp_path).model_copy(
        update={
            "asr_segments": (
                AsrSegment(
                    source_ordinal=3, start_ms=3_000, end_ms=3_800, speech=speech
                ),
            ),
            "subtitle_cues": (
                WindowSubtitleCue(
                    cue_id="automatic-ko-orig:visual",
                    track_id="automatic-ko-orig",
                    start_tc_ms=3_000,
                    end_tc_ms=3_800,
                    text=speech,
                ),
            ),
        }
    )
    observation = make_observation(
        data,
        ObservationSpec(
            "obs-insert", ("frame-2", "frame-3"), (3_200, 3_400), speech, "vad:2", None
        ),
    )
    result = reconcile_post_drafter_window(
        data, _empty(data).model_copy(update={"observations": (observation,)})
    )
    assert result.cues == ()


def test_visual_only_without_lexical_support_does_not_add(tmp_path: Path) -> None:
    """Do not elevate an uncorroborated visual string into speech."""
    data = load_policy_window_input(tmp_path)
    observation = make_observation(
        data,
        ObservationSpec(
            "obs-no-add",
            ("frame-2", "frame-3"),
            (3_200, 3_400),
            "화면에만 있는 문구",
            "vad:2",
            None,
        ),
    )
    result = reconcile_post_drafter_window(
        data, _empty(data).model_copy(update={"observations": (observation,)})
    )
    assert result.cues == ()
    assert result.corrections == ()


def test_strong_visual_correction_survives_shared_asr_subtitle_error(
    tmp_path: Path,
) -> None:
    """Do not restore matching weak sources over an exact strong caption."""
    data = _source_input(tmp_path, "유가", "유가")
    draft = _claim(data, "유가", "육아", CorrectionAction.REPLACE_SPAN)

    result = reconcile_post_drafter_window(data, draft)

    assert result.cues[0].speech == "육아"
    assert result.cue_evidence[0].requested_action is CorrectionAction.REPLACE_SPAN


def test_existing_cue_correction_rejects_shifted_source_bounds(
    tmp_path: Path,
) -> None:
    """A forged but overlapping replacement cannot move source timing."""
    data = _source_input(tmp_path, "유가", "유가")
    result = reconcile_post_drafter_window(
        data,
        _claim(data, "유가", "육아", CorrectionAction.REPLACE_SPAN),
    )
    cue = result.cues[0].model_copy(
        update={
            "start_tc_ms": result.cues[0].start_tc_ms + 50,
            "end_tc_ms": result.cues[0].end_tc_ms + 50,
        }
    )
    evidence = result.cue_evidence[0].model_copy(
        update={
            "cue_identity": window_cue_identity(cue),
            "start_tc_ms": cue.start_tc_ms,
            "end_tc_ms": cue.end_tc_ms,
        }
    )
    forged = result.model_copy(update={"cues": (cue,), "cue_evidence": (evidence,)})

    with pytest.raises(WindowEvidenceError, match="exact admitted lexical"):
        validate_window_draft(data, forged)


def test_complete_visual_caption_does_not_expand_retained_partial_cue(
    tmp_path: Path,
) -> None:
    """Do not duplicate one complete caption into each retained lyric fragment."""
    retained = "You're the one I want"
    complete = "You're the one I want, You're the one I need"
    data = _source_input(tmp_path, retained, complete)
    draft = _claim(data, retained, complete, CorrectionAction.KEEP)

    result = reconcile_post_drafter_window(data, draft)

    assert result.cues[0].speech == retained
    assert result.cue_evidence[0].requested_action is CorrectionAction.KEEP


@pytest.mark.parametrize(
    "speech",
    [">> [음악]", "[웃음]", "네. [웃음]", "burn up every time"],
)
def test_non_dialogue_repeated_subtitle_is_not_inserted(
    tmp_path: Path,
    speech: str,
) -> None:
    """Do not turn repeated music markers or unsupported lyrics into dialogue."""
    data = _gap_input(tmp_path, speech)

    result = reconcile_post_drafter_window(data, _empty(data))

    assert result.cues == ()
    assert result.corrections == ()


def test_context_only_subtitle_is_not_inserted(tmp_path: Path) -> None:
    """Do not duplicate a neighboring window's cue from overlap context."""
    data = _gap_input(tmp_path, "이전 구간 발화").model_copy(
        update={"core_start_ms": 2_000}
    )

    result = reconcile_post_drafter_window(data, _empty(data))

    assert result.cues == ()


def test_rolling_ko_orig_substring_requires_drafter_adjudication(
    tmp_path: Path,
) -> None:
    """Do not promote a substring that lacks an exact replayable source claim."""
    data = load_policy_window_input(tmp_path).model_copy(
        update={
            "asr_segments": (
                AsrSegment(
                    source_ordinal=1,
                    start_ms=1_000,
                    end_ms=1_800,
                    speech="그래서 에너지가",
                ),
            ),
            "subtitle_cues": (
                WindowSubtitleCue(
                    cue_id="automatic-ko-orig:215",
                    track_id="automatic-ko-orig",
                    start_tc_ms=900,
                    end_tc_ms=1_400,
                    text=(
                        ">> 아 제스비가 엄청 엄청 터졌어요. 너와의 모든 지금이 그래서"
                    ),
                ),
                WindowSubtitleCue(
                    cue_id="automatic-ko-orig:216",
                    track_id="automatic-ko-orig",
                    start_tc_ms=1_400,
                    end_tc_ms=1_410,
                    text="너와의 모든 지금이 그래서",
                ),
                WindowSubtitleCue(
                    cue_id="automatic-ko-orig:217",
                    track_id="automatic-ko-orig",
                    start_tc_ms=1_410,
                    end_tc_ms=1_900,
                    text="너와의 모든 지금이 그래서 >> 에너지가",
                ),
            ),
            "vad_intervals": (
                WindowVadInterval(
                    interval_id="vad:1",
                    start_tc_ms=800,
                    end_tc_ms=2_000,
                ),
            ),
        }
    )
    draft = _claim(
        data,
        "그래서 에너지가",
        "그래서 에너지가",
        CorrectionAction.KEEP,
    )

    result = reconcile_post_drafter_window(data, draft)

    assert [cue.speech for cue in result.cues] == ["그래서 에너지가"]
    validate_window_draft(data, result)


def _gap_input(tmp_path: Path, speech: str) -> WindowDraftInput:
    data = load_policy_window_input(tmp_path)
    return data.model_copy(
        update={
            "asr_segments": (),
            "subtitle_cues": tuple(
                WindowSubtitleCue(
                    cue_id=f"automatic-ko-orig:gap-{tag}",
                    track_id="automatic-ko-orig",
                    start_tc_ms=start,
                    end_tc_ms=end,
                    text=speech,
                )
                for tag, start, end in (("a", 1_000, 1_800), ("b", 1_010, 1_790))
            ),
            "vad_intervals": (
                WindowVadInterval(
                    interval_id="vad:1", start_tc_ms=900, end_tc_ms=1_900
                ),
            ),
            "verified_turns": (
                WindowVerifiedTurn(
                    turn_id="turn:gap",
                    start_tc_ms=1_000,
                    end_tc_ms=1_800,
                    cluster="SPEAKER_00",
                    speaker="화자A",
                    authority_digest="a" * 64,
                    verification_evidence_ids=("evidence:gap",),
                    verification_decision_ids=("decision:gap",),
                ),
            ),
        }
    )


def _source_input(tmp_path: Path, asr: str, subtitle: str) -> WindowDraftInput:
    return load_policy_window_input(tmp_path).model_copy(
        update={
            "asr_segments": (
                AsrSegment(source_ordinal=1, start_ms=1_000, end_ms=1_800, speech=asr),
            ),
            "subtitle_cues": (
                WindowSubtitleCue(
                    cue_id="automatic-ko-orig:source",
                    track_id="automatic-ko-orig",
                    start_tc_ms=1_000,
                    end_tc_ms=1_800,
                    text=subtitle,
                ),
            ),
        }
    )


def _empty(data: WindowDraftInput) -> WindowDraft:
    return WindowDraft(
        window_index=data.window_index,
        cues=(),
        unresolved=(),
        era=data.era,
        policy_digest=data.policy_digest,
        inventory_digest=data.inventory_digest,
        source_digest=data.source_digest,
    )


def _observation(data: WindowDraftInput, text: str) -> VisualObservation:
    return VisualObservation(
        observation_id="obs-strong",
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_digest(data),
        role=VisualRole.SPEECH_SUPPORTING,
        state=EvidenceState.SUPPORTED,
        semantic_relation=SemanticRelation.EXACT,
        frame_ids=(FrameId("frame-1"),),
        frame_pts_ms=(1_200,),
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        complete_text=text,
        vad_interval_ids=("vad:1",),
        caption_regime_supported=True,
        temporal_alignment_supported=True,
        speech_presence_supported=True,
        evidence_for=("exact speech-following caption",),
    )


def _claim(
    data: WindowDraftInput, before: str, visual: str, action: CorrectionAction
) -> WindowDraft:
    observation = _observation(data, visual)
    replacing = action is CorrectionAction.REPLACE_SPAN
    request = VisualCorrectionRequest(
        policy_digest=policy_digest(data),
        requested_action=action,
        observation=observation,
        candidate_origin=CandidateOrigin.VISUAL_ONLY if replacing else None,
        before_text=before,
        submitted_result=visual if replacing else before,
        source_slice=TextSlice(start=0, end=len(before)) if replacing else None,
        candidate_slice=TextSlice(start=0, end=len(visual)) if replacing else None,
    )
    validated = validate_visual_correction(request)
    receipt = build_correction_receipt(source_digest(data), request, validated)
    cue = DraftCue(start_tc_ms=1_000, end_tc_ms=1_800, speech=validated.result_text)
    evidence = WindowCueEvidence(
        cue_index=0,
        cue_identity=window_cue_identity(cue),
        resolution=WindowCueResolution.AUTHORIZED,
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        speech_digest=text_digest(cue.speech),
        source_evidence_ids=("asr:1", "vision:obs-strong"),
        asr_ordinals=(1,),
        observation_ids=("obs-strong",),
        vad_interval_ids=("vad:1",),
        requested_action=validated.action,
        candidate_origin=CandidateOrigin.VISUAL_ONLY if replacing else None,
        correction_receipt_id=receipt.receipt_id,
    )
    return WindowDraft(
        window_index=data.window_index,
        cues=(cue,),
        unresolved=(),
        era=data.era,
        policy_digest=data.policy_digest,
        inventory_digest=data.inventory_digest,
        source_digest=data.source_digest,
        observations=(observation,),
        corrections=(receipt,),
        cue_evidence=(evidence,),
    )
