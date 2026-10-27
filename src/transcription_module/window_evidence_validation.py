# Copyright 2026 Transcription Module contributors

"""Temporal, policy, and unresolved validation after global allocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module.visual_correction import CandidateOrigin
from transcription_module.visual_evidence_policy import CorrectionAction, EvidenceEra
from transcription_module.window_authority_allocation import (
    AllocatedCue,
    allocate_window_authority,
    validate_core_contiguous_asr_claim,
)
from transcription_module.window_authority_sources import (
    WindowAuthority,
    build_window_authority,
    is_grouped_asr_claim,
)
from transcription_module.window_evidence_errors import fail_window_evidence
from transcription_module.window_evidence_models import WindowCueResolution
from transcription_module.window_evidence_replay import validate_allocated_replay

if TYPE_CHECKING:
    from transcription_module._drafting_models import WindowDraft, WindowDraftInput


def validate_policy_window_draft(
    window_input: WindowDraftInput,
    draft: WindowDraft,
) -> None:
    """Validate one policy draft only after all authority has one owner."""
    _validate_policy_bindings(window_input, draft)
    authority = build_window_authority(window_input, draft)
    allocated = allocate_window_authority(authority, draft)
    _validate_output_authority(authority, draft, allocated)
    _validate_resolution_contract(draft, allocated)
    _validate_unresolved(draft)


def _validate_policy_bindings(
    window_input: WindowDraftInput,
    draft: WindowDraft,
) -> None:
    if window_input.run_id is None:
        fail_window_evidence("policy-bound window input lacks run authority")
    if any(
        value is None
        for value in (
            window_input.policy_digest,
            window_input.inventory_digest,
            window_input.source_digest,
        )
    ):
        fail_window_evidence("policy-bound window input lacks digest authority")
    if (
        draft.era is not EvidenceEra.POLICY_BOUND
        or draft.policy_digest != window_input.policy_digest
    ):
        fail_window_evidence("window draft has a foreign policy identity")
    if draft.inventory_digest != window_input.inventory_digest:
        fail_window_evidence("window draft has a foreign inventory identity")
    if draft.source_digest != window_input.source_digest:
        fail_window_evidence("window draft has a foreign source identity")


def _validate_output_authority(
    authority: WindowAuthority,
    draft: WindowDraft,
    allocated: tuple[AllocatedCue, ...],
) -> None:
    policy_sha = authority.window_input.policy_digest
    source_sha = authority.window_input.source_digest
    for observation in draft.observations:
        if (
            observation.era is not EvidenceEra.POLICY_BOUND
            or observation.policy_digest != policy_sha
        ):
            fail_window_evidence("window observation has a foreign policy")
        if (
            observation.start_tc_ms < authority.window_input.context_start_ms
            or observation.end_tc_ms > authority.window_input.context_end_ms
        ):
            fail_window_evidence("window observation leaves its context interval")
    for receipt in draft.corrections:
        if (
            receipt.era is not EvidenceEra.POLICY_BOUND
            or receipt.policy_digest != policy_sha
            or receipt.source_digest != source_sha
        ):
            fail_window_evidence("window correction receipt is stale")
    for item in allocated:
        _validate_allocated_time(authority, item)
        validate_core_contiguous_asr_claim(authority, item.record)
        validate_allocated_replay(authority, item)
        _validate_grouped_verified_turns(authority, item)


def _validate_allocated_time(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    _validate_observation_time(authority, allocated)
    _validate_lexical_times(authority, allocated)
    _validate_speech_times(authority, allocated)


def _validate_observation_time(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    cue = allocated.cue
    observation = allocated.observation
    if not _overlaps(
        observation.start_tc_ms,
        observation.end_tc_ms,
        cue.start_tc_ms,
        cue.end_tc_ms,
    ):
        fail_window_evidence(
            "window correction observation time does not overlap its cue"
        )
    for frame_id, pts_ms in zip(
        observation.frame_ids,
        observation.frame_pts_ms,
        strict=True,
    ):
        frame = authority.frames.get(str(frame_id))
        if frame is None or frame.pts_ms != pts_ms:
            fail_window_evidence("window observation references a missing frame")
        if not cue.start_tc_ms <= pts_ms < cue.end_tc_ms:
            fail_window_evidence("window observation frame time does not belong to cue")
        if not observation.start_tc_ms <= pts_ms < observation.end_tc_ms:
            fail_window_evidence(
                "window observation frame time does not belong to observation"
            )


def _validate_lexical_times(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    cue = allocated.cue
    admitted_bounds: list[tuple[int, int]] = []
    for ordinal in allocated.record.asr_ordinals:
        source = authority.asr[ordinal]
        admitted_bounds.append((source.start_ms, source.end_ms))
        if not _overlaps(
            source.start_ms,
            source.end_ms,
            cue.start_tc_ms,
            cue.end_tc_ms,
        ):
            fail_window_evidence("window ASR time does not overlap its cue")
    for cue_id in allocated.record.subtitle_cue_ids:
        source = authority.subtitles[cue_id]
        admitted_bounds.append((source.start_tc_ms, source.end_tc_ms))
        if not _overlaps(
            source.start_tc_ms,
            source.end_tc_ms,
            cue.start_tc_ms,
            cue.end_tc_ms,
        ):
            fail_window_evidence("window subtitle time does not overlap its cue")
    source_bounds = tuple(
        (authority.asr[ordinal].start_ms, authority.asr[ordinal].end_ms)
        for ordinal in allocated.record.asr_ordinals
    )
    grouped_action = is_grouped_asr_claim(
        authority,
        allocated.record.requested_action,
        allocated.record.asr_ordinals,
    )
    if grouped_action and (cue.start_tc_ms, cue.end_tc_ms) != (
        min(start for start, _ in source_bounds),
        max(end for _, end in source_bounds),
    ):
        fail_window_evidence(
            "window correction must preserve exact admitted lexical bounds"
        )
    if (
        not grouped_action
        and allocated.record.requested_action is CorrectionAction.REPLACE_SPAN
        and (
            not admitted_bounds
            or (cue.start_tc_ms, cue.end_tc_ms)
            != (
                min(start for start, _ in admitted_bounds),
                max(end for _, end in admitted_bounds),
            )
        )
    ):
        fail_window_evidence(
            "window correction must preserve exact admitted lexical bounds"
        )


def _validate_grouped_verified_turns(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    """Require one complete, compatible verified owner for a semantic group."""
    record = allocated.record
    if not is_grouped_asr_claim(
        authority, record.requested_action, record.asr_ordinals
    ):
        return
    if not authority.turns:
        fail_window_evidence("window grouped cue requires verified turn ownership")
    sources = tuple(authority.asr[ordinal] for ordinal in record.asr_ordinals)
    relevant = tuple(
        turn
        for turn in authority.turns.values()
        if any(
            _overlaps(
                turn.start_tc_ms,
                turn.end_tc_ms,
                source.start_ms,
                source.end_ms,
            )
            for source in sources
        )
    )
    if not relevant or any(
        not any(
            turn.start_tc_ms <= source.start_ms and turn.end_tc_ms >= source.end_ms
            for turn in relevant
        )
        for source in sources
    ):
        fail_window_evidence(
            "window grouped cue has insufficient verified turn ownership"
        )
    speakers = {turn.speaker for turn in relevant}
    if len(speakers) != 1:
        fail_window_evidence(
            "window grouped cue crosses incompatible verified turn speakers"
        )
    speaker = next(iter(speakers))
    if record.resolution is WindowCueResolution.UNRESOLVED:
        return
    relevant_ids = tuple(turn.turn_id for turn in relevant)
    if allocated.cue.speaker != speaker or set(record.turn_ids) != set(relevant_ids):
        fail_window_evidence(
            "window grouped cue differs from compatible verified turn ownership"
        )


def _validate_speech_times(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    cue = allocated.cue
    record = allocated.record
    observation = allocated.observation
    for interval_id in record.vad_interval_ids:
        source = authority.vad[interval_id]
        if not _overlaps(
            source.start_tc_ms,
            source.end_tc_ms,
            cue.start_tc_ms,
            cue.end_tc_ms,
        ):
            fail_window_evidence("window VAD time does not overlap its cue")
    for turn_id in record.turn_ids:
        source = authority.turns[turn_id]
        if not _overlaps(
            source.start_tc_ms,
            source.end_tc_ms,
            cue.start_tc_ms,
            cue.end_tc_ms,
        ):
            fail_window_evidence("window turn time does not overlap its cue")
    if set(observation.vad_interval_ids) != set(record.vad_interval_ids):
        fail_window_evidence("window cue VAD coverage differs from observation")
    if set(observation.turn_ids) != set(record.turn_ids):
        fail_window_evidence("window cue turn coverage differs from observation")
    if (
        record.requested_action is CorrectionAction.INSERT_CUE
        and record.candidate_origin is CandidateOrigin.VISUAL_ONLY
        and record.turn_ids
    ):
        _validate_audio_bound_insertion(authority, allocated)
    if _is_subtitle_gap_claim(allocated):
        _validate_subtitle_gap_insertion(authority, allocated)


def _validate_audio_bound_insertion(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    cue = allocated.cue
    record = allocated.record
    if len(record.turn_ids) != 1:
        fail_window_evidence(
            "visual insertion requires exactly one verified audio turn"
        )
    turn = authority.turns[record.turn_ids[0]]
    if cue.start_tc_ms != turn.start_tc_ms or cue.end_tc_ms != turn.end_tc_ms:
        fail_window_evidence("visual insertion must use exact verified turn bounds")
    if cue.speaker != turn.speaker or record.speaker != turn.speaker:
        fail_window_evidence(
            "visual insertion speaker differs from verified turn authority"
        )


def _is_subtitle_gap_claim(allocated: AllocatedCue) -> bool:
    record = allocated.record
    return (
        record.requested_action is CorrectionAction.KEEP
        and record.candidate_origin is CandidateOrigin.KO_ORIG
        and not record.asr_ordinals
        and len(record.subtitle_cue_ids) == 1
        and len(record.vad_interval_ids) == 1
        and len(record.turn_ids) == 1
        and allocated.observation.complete_text is None
        and allocated.receipt.candidate_text is None
    )


def _validate_subtitle_gap_insertion(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    cue = allocated.cue
    record = allocated.record
    if (
        record.asr_ordinals
        or len(record.subtitle_cue_ids) != 1
        or len(record.vad_interval_ids) != 1
        or len(record.turn_ids) != 1
    ):
        fail_window_evidence(
            "subtitle gap requires one subtitle, VAD, and verified turn"
        )
    subtitle = authority.subtitles[record.subtitle_cue_ids[0]]
    vad = authority.vad[record.vad_interval_ids[0]]
    turn = authority.turns[record.turn_ids[0]]
    overlapping_vads = tuple(
        item
        for item in authority.vad.values()
        if _overlaps(
            item.start_tc_ms,
            item.end_tc_ms,
            subtitle.start_tc_ms,
            subtitle.end_tc_ms,
        )
    )
    relevant_turns = tuple(
        item
        for item in authority.turns.values()
        if _overlaps(
            item.start_tc_ms,
            item.end_tc_ms,
            subtitle.start_tc_ms,
            subtitle.end_tc_ms,
        )
        and any(
            _overlaps(
                item.start_tc_ms,
                item.end_tc_ms,
                interval.start_tc_ms,
                interval.end_tc_ms,
            )
            for interval in overlapping_vads
        )
    )
    if (
        overlapping_vads != (vad,)
        or relevant_turns != (turn,)
        or not (
            subtitle.start_tc_ms <= turn.start_tc_ms
            and turn.end_tc_ms <= subtitle.end_tc_ms
            and vad.start_tc_ms <= turn.start_tc_ms
            and turn.end_tc_ms <= vad.end_tc_ms
        )
        or (cue.start_tc_ms, cue.end_tc_ms) != (turn.start_tc_ms, turn.end_tc_ms)
        or cue.speaker != turn.speaker
        or record.speaker != turn.speaker
    ):
        fail_window_evidence(
            "subtitle gap differs from its unique verified turn envelope"
        )


def _validate_resolution_contract(
    draft: WindowDraft,
    allocated: tuple[AllocatedCue, ...],
) -> None:
    unresolved_by_target: dict[int, int] = {}
    for item in draft.unresolved:
        if item.target_cue_index is not None:
            unresolved_by_target[item.target_cue_index] = (
                unresolved_by_target.get(item.target_cue_index, 0) + 1
            )
    for item in allocated:
        record = item.record
        unresolved_count = unresolved_by_target.get(record.cue_index, 0)
        if record.resolution is WindowCueResolution.UNRESOLVED:
            if unresolved_count != 1:
                fail_window_evidence(
                    "unresolved window cue requires exactly one typed record"
                )
            if record.requested_action is not CorrectionAction.HOLD_FOR_CONFIRMATION:
                fail_window_evidence("unresolved window cue requires a HOLD action")
            if record.turn_ids or item.cue.speaker is not None:
                fail_window_evidence(
                    "unresolved window cue cannot carry speaker authority"
                )
            continue
        if unresolved_count:
            fail_window_evidence(
                "authorized window cue cannot retain unresolved ambiguity"
            )
        if record.requested_action is CorrectionAction.HOLD_FOR_CONFIRMATION:
            fail_window_evidence("HOLD window cue cannot claim authorization")


def _validate_unresolved(draft: WindowDraft) -> None:
    for item in draft.unresolved:
        target = item.target_cue_index
        if target is None:
            fail_window_evidence(
                "policy-bound unresolved item requires an exact target cue index"
            )
        if item.reason_code is None or not item.source_evidence_ids:
            fail_window_evidence(
                "policy-bound unresolved item requires reason code and source refs"
            )
        if target >= len(draft.cues):
            fail_window_evidence("unresolved target cue index is outside the draft")
        cue = draft.cues[target]
        if not cue.start_tc_ms <= item.at_ms < cue.end_tc_ms:
            fail_window_evidence("unresolved timestamp does not belong to target cue")
        record = draft.cue_evidence[target]
        if item.source_evidence_ids != record.source_evidence_ids:
            fail_window_evidence("unresolved source refs differ from target coverage")


def _overlaps(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and end > other_start


__all__ = ["validate_policy_window_draft"]
