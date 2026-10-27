# Copyright 2026 Transcription Module contributors

"""Resolve exact-source reconciliation groups without rewriting cue text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._drafting_models import UnresolvedItem, UnresolvedReasonCode
from transcription_module._drafting_reconciliation_models import (
    ReconciledCue,
    ReconciliationCandidate,
)
from transcription_module.correction_provenance import build_correction_receipt
from transcription_module.visual_correction import (
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.models import FrameId


def reconcile_group(
    members: tuple[ReconciliationCandidate, ...],
) -> ReconciledCue:
    """Choose one exact claim, or preserve source text when receipts disagree."""
    ordered = tuple(sorted(members, key=_candidate_key))
    insertions = tuple(
        item
        for item in ordered
        if item.allocated.receipt.action is CorrectionAction.INSERT_CUE
    )
    if insertions:
        results = {item.allocated.receipt.result_text for item in insertions}
        if len(results) != 1 or len(insertions) != len(ordered):
            fail_window_evidence("visual insertion identity has conflicting receipts")
        return _selected(insertions[0], ordered)
    mutations = tuple(
        item
        for item in ordered
        if item.allocated.receipt.action is CorrectionAction.REPLACE_SPAN
    )
    results = {item.allocated.receipt.result_text for item in mutations}
    if len(results) > 1:
        return _conflicted(ordered, mutations)
    holds = tuple(
        item
        for item in ordered
        if item.allocated.receipt.action is CorrectionAction.HOLD_FOR_CONFIRMATION
    )
    if holds:
        return _selected(holds[0], ordered)
    if mutations:
        return _selected(mutations[0], ordered)
    return _selected(ordered[0], ordered)


def output_key(item: ReconciledCue) -> tuple[int, int, tuple[str, ...], str, str]:
    """Return the total ordering key for persisted transcript cues."""
    return (
        item.cue.start_tc_ms,
        item.cue.end_tc_ms,
        item.identity_refs,
        item.cue.speaker or "",
        item.cue.speech,
    )


def _selected(
    chosen: ReconciliationCandidate,
    members: tuple[ReconciliationCandidate, ...],
) -> ReconciledCue:
    return ReconciledCue(
        cue=chosen.allocated.cue,
        receipt=chosen.allocated.receipt,
        identity_refs=_identity_refs(chosen),
        unresolved=_unique_unresolved(
            tuple(item for member in members for item in member.unresolved)
        ),
        frame_points=_frame_points(members),
    )


def _conflicted(
    members: tuple[ReconciliationCandidate, ...],
    mutations: tuple[ReconciliationCandidate, ...],
) -> ReconciledCue:
    before_values = {item.allocated.receipt.before_text for item in mutations}
    if len(before_values) != 1 or None in before_values or "" in before_values:
        fail_window_evidence("conflicting corrections lack one exact source text")
    before = next(value for value in before_values if value)
    chosen = members[0]
    cue = chosen.allocated.cue.model_copy(update={"speech": before})
    request = VisualCorrectionRequest(
        policy_digest=chosen.allocated.receipt.policy_digest or "",
        requested_action=CorrectionAction.HOLD_FOR_CONFIRMATION,
        observation=chosen.allocated.observation,
        before_text=before,
        submitted_result=before,
        speaker=cue.speaker,
    )
    receipt = build_correction_receipt(
        chosen.allocated.receipt.source_digest,
        request,
        validate_visual_correction(request),
    )
    candidates: dict[str, CorrectionReceipt] = {}
    for item in mutations:
        result = item.allocated.receipt.result_text
        if result is None:
            fail_window_evidence("conflicting correction receipt lacks result text")
        _ = candidates.setdefault(result, item.allocated.receipt)
    ordered_candidates = tuple(sorted(candidates))
    source_refs = tuple(
        sorted(
            {
                value
                for member in members
                for value in member.allocated.record.source_evidence_ids
            }
        )
    )
    unresolved = UnresolvedItem(
        at_ms=cue.start_tc_ms,
        field="speech",
        reason="valid correction receipts disagree",
        candidates=ordered_candidates,
        reason_code=UnresolvedReasonCode.VISUAL_EVIDENCE_CONFLICT,
        target_cue_index=0,
        source_evidence_ids=source_refs,
        candidate_receipt_ids=tuple(
            candidates[value].receipt_id for value in ordered_candidates
        ),
    )
    return ReconciledCue(
        cue=cue,
        receipt=receipt,
        identity_refs=(
            *_identity_refs(chosen),
            *(f"candidate:{item}" for item in unresolved.candidate_receipt_ids),
        ),
        unresolved=(unresolved,),
        frame_points=_frame_points(members),
    )


def _identity_refs(candidate: ReconciliationCandidate) -> tuple[str, ...]:
    record = candidate.allocated.record
    return tuple(
        sorted(
            {
                *record.source_evidence_ids,
                *(f"verified-turn:{item}" for item in record.turn_ids),
            }
        )
    )


def canonical_frame_points(
    points: Iterable[tuple[int, FrameId]],
) -> tuple[tuple[int, FrameId], ...]:
    """Deduplicate frame references under one total persisted order."""
    return tuple(sorted(set(points), key=lambda point: (point[0], str(point[1]))))


def _frame_points(
    members: tuple[ReconciliationCandidate, ...],
) -> tuple[tuple[int, FrameId], ...]:
    return canonical_frame_points(
        point
        for member in members
        for point in zip(
            member.allocated.observation.frame_pts_ms,
            member.allocated.observation.frame_ids,
            strict=True,
        )
    )


def _unique_unresolved(
    items: tuple[UnresolvedItem, ...],
) -> tuple[UnresolvedItem, ...]:
    by_payload = {item.model_dump_json(): item for item in items}
    return tuple(by_payload[key] for key in sorted(by_payload))


def _candidate_key(
    candidate: ReconciliationCandidate,
) -> tuple[int, int, str, tuple[str, ...], str, str]:
    allocated = candidate.allocated
    return (
        allocated.cue.start_tc_ms,
        allocated.cue.end_tc_ms,
        allocated.receipt.action.value,
        tuple(sorted(allocated.record.source_evidence_ids)),
        allocated.receipt.receipt_id,
        allocated.cue.model_dump_json(),
    )


__all__ = ["canonical_frame_points", "output_key", "reconcile_group"]
