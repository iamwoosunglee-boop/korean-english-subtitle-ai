# Copyright 2026 Transcription Module contributors

"""Conservative non-reference recovery between drafting and validation."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcription_module._drafting_models import (
    DraftCue,
    WindowDraft,
    WindowDraftInput,
)
from transcription_module.correction_provenance import (
    build_correction_receipt,
    text_digest,
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
)
from transcription_module.visual_observations import VisualObservation
from transcription_module.visual_review import CandidateDispositionKind
from transcription_module.window_cue_identity import window_cue_identity
from transcription_module.window_evidence_models import (
    ProvisionalVisualInsertion,
    WindowCueEvidence,
    WindowCueResolution,
    WindowSubtitleCue,
    WindowVadInterval,
)

if TYPE_CHECKING:
    from transcription_module.asr import AsrSegment
    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.visual_review import RawVisualCandidate
    from transcription_module.window_evidence_models import WindowVerifiedTurn


MINIMUM_STABLE_COPIES = 2
MINIMUM_VISUAL_INSERTION_FRAMES = 2


@dataclass(frozen=True, slots=True)
class _Claim:
    cue: DraftCue
    observation: VisualObservation
    receipt: CorrectionReceipt
    evidence: WindowCueEvidence


@dataclass(frozen=True, slots=True)
class _EvidenceSpec:
    vad_ids: tuple[str, ...]
    action: CorrectionAction
    origin: CandidateOrigin | None = None
    asr_ordinals: tuple[int, ...] = ()
    subtitle: WindowSubtitleCue | None = None
    turn_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _InsertionContext:
    data: WindowDraftInput
    draft: WindowDraft
    claims: list[_Claim]


def reconcile_post_drafter_window(
    data: WindowDraftInput, draft: WindowDraft
) -> WindowDraft:
    """Recover only independently replayable exact speech before validation."""
    if (
        data.era is not EvidenceEra.POLICY_BOUND
        or draft.era is not EvidenceEra.POLICY_BOUND
    ):
        return draft
    if draft.unresolved or len(draft.cues) != len(draft.cue_evidence):
        return draft
    claims = _initial_claims(draft)
    if claims is None:
        return draft
    repaired = tuple(_restore_visual_wording(data, claim) for claim in claims)
    augmented = _add_supported_insertions(data, draft, list(repaired))
    provisional = _provisional_visual_insertions(
        _InsertionContext(data, draft, list(augmented))
    )
    if augmented == claims and provisional == draft.provisional_visual_insertions:
        return draft
    materialized = _materialize(draft, augmented)
    provisional_observation_ids = {item.observation_id for item in provisional}
    cue_observation_ids = {item.observation_id for item in materialized.observations}
    provisional_observations = tuple(
        item
        for item in draft.observations
        if item.observation_id in provisional_observation_ids
        and item.observation_id not in cue_observation_ids
    )
    return materialized.model_copy(
        update={
            "observations": (
                *materialized.observations,
                *provisional_observations,
            ),
            "provisional_visual_insertions": provisional,
        }
    )


def _initial_claims(draft: WindowDraft) -> tuple[_Claim, ...] | None:
    observations = {item.observation_id: item for item in draft.observations}
    receipts = {item.receipt_id: item for item in draft.corrections}
    claims: list[_Claim] = []
    for cue, evidence in zip(draft.cues, draft.cue_evidence, strict=True):
        if len(evidence.observation_ids) != 1:
            return None
        observation = observations.get(evidence.observation_ids[0])
        receipt = receipts.get(evidence.correction_receipt_id)
        if observation is None or receipt is None:
            return None
        claims.append(_Claim(cue, observation, receipt, evidence))
    return tuple(claims)


def _add_supported_insertions(
    data: WindowDraftInput,
    draft: WindowDraft,
    claims: list[_Claim],
) -> tuple[_Claim, ...]:
    context = _InsertionContext(data, draft, claims)
    used = {item.observation.observation_id for item in claims}
    for observation in draft.observations:
        if observation.observation_id not in used:
            inserted = _visual_insertion(context, observation)
            if inserted is not None:
                claims.append(inserted)
                used.add(observation.observation_id)
    for subtitle in _stable_subtitles(data):
        inserted = _subtitle_gap_claim(data, draft, subtitle, claims)
        if inserted is not None:
            claims.append(inserted)
    return tuple(claims)


def _materialize(draft: WindowDraft, claims: tuple[_Claim, ...]) -> WindowDraft:
    ordered = sorted(
        claims,
        key=lambda item: (item.cue.start_tc_ms, item.cue.end_tc_ms, item.cue.speech),
    )
    indexed = tuple(
        _Claim(
            item.cue,
            item.observation,
            item.receipt,
            item.evidence.model_copy(update={"cue_index": index}),
        )
        for index, item in enumerate(ordered)
    )
    return draft.model_copy(
        update={
            "cues": tuple(item.cue for item in indexed),
            "observations": tuple(item.observation for item in indexed),
            "corrections": tuple(item.receipt for item in indexed),
            "cue_evidence": tuple(item.evidence for item in indexed),
        }
    )


def _restore_visual_wording(data: WindowDraftInput, claim: _Claim) -> _Claim:
    observation = claim.observation
    candidate = observation.complete_text
    if (
        not _strong(observation)
        or candidate is None
        or _same(candidate, claim.cue.speech)
        or _lexical(claim.cue.speech) in _lexical(candidate)
    ):
        return claim
    record = claim.evidence
    if (
        record.resolution is not WindowCueResolution.AUTHORIZED
        or not record.asr_ordinals
    ):
        return claim
    subtitles = _overlapping_subtitles(data, claim.cue.start_tc_ms, claim.cue.end_tc_ms)
    exact_subtitle = next(
        (item for item in subtitles if _same(item.text, candidate)), None
    )
    asr = _asr_by_ordinals(data, record.asr_ordinals)
    if exact_subtitle is None and not _sources_support(
        candidate, tuple(item.speech for item in asr)
    ):
        return claim
    origin = (
        CandidateOrigin.KO_ORIG
        if exact_subtitle is not None
        else CandidateOrigin.VISUAL_ONLY
    )
    return _replacement_claim(data, claim, candidate, origin, exact_subtitle)


def _visual_insertion(
    context: _InsertionContext,
    observation: VisualObservation,
) -> _Claim | None:
    data = context.data
    candidate = observation.complete_text
    if (
        not _strong(observation)
        or candidate is None
        or len(observation.frame_pts_ms) < MINIMUM_VISUAL_INSERTION_FRAMES
    ):
        return None
    vad_ids = tuple(
        item for item in observation.vad_interval_ids if _vad(data, item) is not None
    )
    if not vad_ids:
        return None
    turn = _authorized_visual_turn(context, observation)
    if turn is None:
        return None
    start_tc_ms = turn.start_tc_ms
    end_tc_ms = turn.end_tc_ms
    speaker = turn.speaker
    turn_ids = (turn.turn_id,)
    if _covered(candidate, start_tc_ms, end_tc_ms, context.claims):
        return None
    cue = DraftCue(
        start_tc_ms=start_tc_ms,
        end_tc_ms=end_tc_ms,
        speech=_display(candidate),
        speaker=speaker,
    )
    request = VisualCorrectionRequest(
        policy_digest=_required(data.policy_digest),
        requested_action=CorrectionAction.INSERT_CUE,
        observation=observation,
        candidate_origin=CandidateOrigin.VISUAL_ONLY,
        before_text="",
        submitted_result=_display(candidate),
        speaker=speaker,
    )
    receipt = build_correction_receipt(
        _required(data.source_digest), request, validate_visual_correction(request)
    )
    evidence = _evidence(
        cue,
        observation,
        receipt,
        _EvidenceSpec(
            vad_ids=vad_ids,
            action=CorrectionAction.INSERT_CUE,
            origin=CandidateOrigin.VISUAL_ONLY,
            turn_ids=turn_ids,
        ),
    )
    return _Claim(cue, observation, receipt, evidence)


def _authorized_visual_turn(
    context: _InsertionContext,
    observation: VisualObservation,
) -> WindowVerifiedTurn | None:
    if (
        _raw_candidate_for_disposition(
            context,
            observation,
            CandidateDispositionKind.AUTHORIZED_INSERTION,
        )
        is None
    ):
        return None
    return _matching_verified_turn(context, observation)


def _provisional_visual_insertions(
    context: _InsertionContext,
) -> tuple[ProvisionalVisualInsertion, ...]:
    result: list[ProvisionalVisualInsertion] = []
    for observation in context.draft.observations:
        candidate = _raw_candidate_for_disposition(
            context,
            observation,
            CandidateDispositionKind.PROVISIONAL_INSERTION,
        )
        if candidate is None:
            continue
        disposition = next(
            item
            for item in context.draft.candidate_dispositions
            if item.candidate_id == candidate.candidate_id
        )
        vad_ids = tuple(
            interval_id
            for interval_id in observation.vad_interval_ids
            if _vad(context.data, interval_id) is not None
        )
        if not vad_ids:
            continue
        turn = _matching_verified_turn(context, observation)
        if turn is not None:
            start_tc_ms = turn.start_tc_ms
            end_tc_ms = turn.end_tc_ms
            speaker = turn.speaker
            turn_id = turn.turn_id
            authority_id = turn.turn_id
        elif len(vad_ids) == 1 and not observation.turn_ids:
            vad = _vad(context.data, vad_ids[0])
            if vad is None:
                continue
            start_tc_ms = vad.start_tc_ms
            end_tc_ms = vad.end_tc_ms
            speaker = None
            turn_id = None
            authority_id = vad.interval_id
        else:
            continue
        identity = hashlib.sha256(
            (
                f"{candidate.candidate_id}\0{observation.observation_id}"
                f"\0{authority_id}"
            ).encode()
        ).hexdigest()
        result.append(
            ProvisionalVisualInsertion(
                provisional_id=f"provisional:{identity}",
                candidate_id=candidate.candidate_id,
                observation_id=observation.observation_id,
                start_tc_ms=start_tc_ms,
                end_tc_ms=end_tc_ms,
                speech=_display(candidate.complete_text),
                speaker=speaker,
                vad_interval_ids=vad_ids,
                turn_id=turn_id,
                disposition_evidence_ids=disposition.evidence_ids,
            )
        )
    return tuple(result)


def _raw_candidate_for_disposition(
    context: _InsertionContext,
    observation: VisualObservation,
    kind: CandidateDispositionKind,
) -> RawVisualCandidate | None:
    visual_review = context.data.visual_review
    if visual_review is None:
        return None
    candidate = next(
        (
            item
            for item in visual_review.candidates
            if _same(item.complete_text, observation.complete_text or "")
            and set(item.supporting_frame_ids) == set(observation.frame_ids)
        ),
        None,
    )
    if candidate is None:
        return None
    disposition = next(
        (
            item
            for item in context.draft.candidate_dispositions
            if item.candidate_id == candidate.candidate_id
        ),
        None,
    )
    if disposition is None or disposition.kind is not kind:
        return None
    return candidate


def _matching_verified_turn(
    context: _InsertionContext,
    observation: VisualObservation,
) -> WindowVerifiedTurn | None:
    if len(observation.turn_ids) != 1:
        return None
    turn = next(
        (
            item
            for item in context.data.verified_turns
            if item.turn_id == observation.turn_ids[0]
        ),
        None,
    )
    if turn is None:
        return None
    if not any(
        _overlap(
            interval.start_tc_ms,
            interval.end_tc_ms,
            turn.start_tc_ms,
            turn.end_tc_ms,
        )
        for interval in context.data.vad_intervals
        if interval.interval_id in observation.vad_interval_ids
    ):
        return None
    return turn


def _subtitle_gap_authority(
    data: WindowDraftInput,
    subtitle: WindowSubtitleCue,
    speech: str,
    claims: list[_Claim],
) -> tuple[WindowVadInterval, WindowVerifiedTurn] | None:
    vads = tuple(
        item
        for item in data.vad_intervals
        if _overlap(
            item.start_tc_ms,
            item.end_tc_ms,
            subtitle.start_tc_ms,
            subtitle.end_tc_ms,
        )
    )
    if len(vads) != 1:
        return None
    vad = vads[0]
    if _covered(speech, vad.start_tc_ms, vad.end_tc_ms, claims):
        return None
    turns = tuple(
        turn
        for turn in data.verified_turns
        if _overlap(
            turn.start_tc_ms,
            turn.end_tc_ms,
            subtitle.start_tc_ms,
            subtitle.end_tc_ms,
        )
        and _overlap(
            turn.start_tc_ms,
            turn.end_tc_ms,
            vad.start_tc_ms,
            vad.end_tc_ms,
        )
    )
    if len(turns) != 1:
        return None
    turn = turns[0]
    if not (
        subtitle.start_tc_ms <= turn.start_tc_ms
        and turn.end_tc_ms <= subtitle.end_tc_ms
        and vad.start_tc_ms <= turn.start_tc_ms
        and turn.end_tc_ms <= vad.end_tc_ms
    ):
        return None
    return vad, turn


def _subtitle_gap_claim(
    data: WindowDraftInput,
    draft: WindowDraft,
    subtitle: WindowSubtitleCue,
    claims: list[_Claim],
) -> _Claim | None:
    midpoint = subtitle.start_tc_ms + (subtitle.end_tc_ms - subtitle.start_tc_ms) // 2
    if not data.core_start_ms <= midpoint < data.core_end_ms:
        return None
    speech = subtitle.text
    authority = _subtitle_gap_authority(
        data,
        subtitle,
        speech,
        claims,
    )
    if authority is None:
        return None
    vad, turn = authority
    asr = _overlapping_asr(data, subtitle.start_tc_ms, subtitle.end_tc_ms)
    asr_conflicts = bool(asr) and not _sources_support(
        speech,
        tuple(item.speech for item in asr),
    )
    strong = tuple(
        item
        for item in draft.observations
        if _strong(item)
        and _overlap(
            item.start_tc_ms, item.end_tc_ms, subtitle.start_tc_ms, subtitle.end_tc_ms
        )
    )
    visual_conflicts = bool(strong) and not _sources_support(
        speech,
        tuple(item.complete_text or "" for item in strong),
    )
    if asr_conflicts or visual_conflicts:
        return None
    frame = next(
        (
            item
            for item in data.frames
            if subtitle.start_tc_ms <= item.pts_ms < subtitle.end_tc_ms
            and vad.start_tc_ms <= item.pts_ms < vad.end_tc_ms
            and turn.start_tc_ms <= item.pts_ms < turn.end_tc_ms
        ),
        None,
    )
    if frame is None:
        return None
    start = turn.start_tc_ms
    end = turn.end_tc_ms
    cue = DraftCue(
        start_tc_ms=start,
        end_tc_ms=end,
        speech=speech,
        speaker=turn.speaker,
    )
    identity = hashlib.sha256(
        f"{subtitle.cue_id}:{vad.interval_id}:{turn.turn_id}:{speech}".encode()
    ).hexdigest()[:24]
    observation = VisualObservation(
        observation_id=f"post-reconcile-{identity}",
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=data.policy_digest,
        role=VisualRole.UNKNOWN,
        state=EvidenceState.INSUFFICIENT,
        semantic_relation=SemanticRelation.UNKNOWN,
        frame_ids=(frame.frame_id,),
        frame_pts_ms=(frame.pts_ms,),
        start_tc_ms=start,
        end_tc_ms=end,
        vad_interval_ids=(vad.interval_id,),
        turn_ids=(turn.turn_id,),
        missing_evidence=("no strong speech-following visual caption",),
    )
    request = VisualCorrectionRequest(
        policy_digest=_required(data.policy_digest),
        requested_action=CorrectionAction.KEEP,
        observation=observation,
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text=speech,
        submitted_result=speech,
        speaker=turn.speaker,
    )
    receipt = build_correction_receipt(
        _required(data.source_digest), request, validate_visual_correction(request)
    )
    evidence = _evidence(
        cue,
        observation,
        receipt,
        _EvidenceSpec(
            vad_ids=(vad.interval_id,),
            action=CorrectionAction.KEEP,
            origin=CandidateOrigin.KO_ORIG,
            subtitle=subtitle,
            turn_ids=(turn.turn_id,),
        ),
    )
    return _Claim(cue, observation, receipt, evidence)


def _replacement_claim(
    data: WindowDraftInput,
    claim: _Claim,
    result: str,
    origin: CandidateOrigin,
    subtitle: WindowSubtitleCue | None,
) -> _Claim:
    before = claim.cue.speech
    request = VisualCorrectionRequest(
        policy_digest=_required(data.policy_digest),
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=claim.observation,
        candidate_origin=origin,
        before_text=before,
        submitted_result=result,
        source_slice=TextSlice(start=0, end=len(before)),
        candidate_slice=TextSlice(start=0, end=len(result)),
        speaker=claim.cue.speaker,
    )
    receipt = build_correction_receipt(
        _required(data.source_digest), request, validate_visual_correction(request)
    )
    cue = claim.cue.model_copy(update={"speech": result})
    evidence = _evidence(
        cue,
        claim.observation,
        receipt,
        _EvidenceSpec(
            vad_ids=claim.evidence.vad_interval_ids,
            action=CorrectionAction.REPLACE_SPAN,
            origin=origin,
            asr_ordinals=claim.evidence.asr_ordinals,
            subtitle=subtitle,
        ),
    )
    return _Claim(cue, claim.observation, receipt, evidence)


def _evidence(
    cue: DraftCue,
    observation: VisualObservation,
    receipt: CorrectionReceipt,
    spec: _EvidenceSpec,
) -> WindowCueEvidence:
    subtitle_ids = () if spec.subtitle is None else (spec.subtitle.cue_id,)
    track_ids = () if spec.subtitle is None else (spec.subtitle.track_id,)
    sources = (
        *(f"asr:{item}" for item in spec.asr_ordinals),
        *(f"subtitle:{item}" for item in subtitle_ids),
        f"vision:{observation.observation_id}",
    )
    return WindowCueEvidence(
        cue_index=0,
        cue_identity=window_cue_identity(cue),
        resolution=WindowCueResolution.AUTHORIZED,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        speech_digest=text_digest(cue.speech),
        speaker=cue.speaker,
        source_evidence_ids=sources,
        asr_ordinals=spec.asr_ordinals,
        subtitle_track_ids=track_ids,
        subtitle_cue_ids=subtitle_ids,
        observation_ids=(observation.observation_id,),
        vad_interval_ids=spec.vad_ids,
        turn_ids=spec.turn_ids,
        requested_action=spec.action,
        candidate_origin=spec.origin,
        correction_receipt_id=receipt.receipt_id,
    )


def _stable_subtitles(data: WindowDraftInput) -> tuple[WindowSubtitleCue, ...]:
    intervals: dict[tuple[str, str], set[tuple[int, int]]] = {}
    for item in data.subtitle_cues:
        intervals.setdefault((item.track_id, item.text), set()).add(
            (item.start_tc_ms, item.end_tc_ms)
        )
    eligible = {
        item.track_id
        for item in data.subtitle_tracks
        if item.variant == "ko-orig"
        and item.eligible_for_speech
        and item.track_id is not None
    }
    seen: set[tuple[str, str]] = set()
    result: list[WindowSubtitleCue] = []
    for item in data.subtitle_cues:
        key = (item.track_id, item.text)
        if (
            key not in seen
            and item.track_id in eligible
            and len(intervals[key]) >= MINIMUM_STABLE_COPIES
            and _eligible_dialogue(item.text)
        ):
            seen.add(key)
            result.append(item)
    return tuple(result)


def _covered(text: str, start: int, end: int, claims: list[_Claim]) -> bool:
    joined = "".join(
        _lexical(item.cue.speech)
        for item in claims
        if _overlap(item.cue.start_tc_ms, item.cue.end_tc_ms, start, end)
    )
    return bool(_lexical(text)) and _lexical(text) in joined


def _sources_support(text: str, sources: tuple[str, ...]) -> bool:
    wanted = _lexical(text)
    return bool(wanted) and any(
        wanted in _lexical(item) or _lexical(item) in wanted
        for item in sources
        if _lexical(item)
    )


def _strong(item: VisualObservation) -> bool:
    return (
        item.role is VisualRole.SPEECH_SUPPORTING
        and item.state is EvidenceState.SUPPORTED
        and item.semantic_relation is SemanticRelation.EXACT
        and item.caption_regime_supported
        and item.temporal_alignment_supported
        and item.speech_presence_supported
        and not item.conflicts
    )


def _overlapping_asr(
    data: WindowDraftInput, start: int, end: int
) -> tuple[AsrSegment, ...]:
    return tuple(
        item
        for item in data.asr_segments
        if _overlap(item.start_ms, item.end_ms, start, end)
    )


def _overlapping_subtitles(
    data: WindowDraftInput, start: int, end: int
) -> tuple[WindowSubtitleCue, ...]:
    return tuple(
        item
        for item in data.subtitle_cues
        if _overlap(item.start_tc_ms, item.end_tc_ms, start, end)
    )


def _asr_by_ordinals(
    data: WindowDraftInput, ordinals: tuple[int, ...]
) -> tuple[AsrSegment, ...]:
    wanted = set(ordinals)
    return tuple(item for item in data.asr_segments if item.source_ordinal in wanted)


def _vad(data: WindowDraftInput, identity: str) -> WindowVadInterval | None:
    return next(
        (item for item in data.vad_intervals if item.interval_id == identity), None
    )


def _same(left: str, right: str) -> bool:
    return _lexical(left) == _lexical(right)


def _display(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _eligible_dialogue(text: str) -> bool:
    return (
        ">>" not in text
        and "[" not in text
        and "]" not in text
        and any("가" <= character <= "힣" for character in text)
    )


def _lexical(text: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFC", text)
        if character.isalnum()
    )


def _overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and end > other_start


def _required(value: str | None) -> str:
    if value is None:
        message = "policy reconciliation requires complete digest identity"
        raise ValueError(message)
    return value


__all__ = ["reconcile_post_drafter_window"]
