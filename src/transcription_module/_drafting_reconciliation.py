# Copyright 2026 Transcription Module contributors

"""Deterministic reconciliation of validated policy-window cue claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._drafting_reconciliation_models import (
    PolicyReconciliation,
    ReconciledCue,
    ReconciledProvisionalInsertion,
    ReconciliationCandidate,
)
from transcription_module._drafting_reconciliation_resolution import (
    canonical_frame_points,
    output_key,
    reconcile_group,
)
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_authority_allocation import allocate_window_authority
from transcription_module.window_authority_sources import (
    WindowAuthority,
    build_window_authority,
    is_grouped_asr_claim,
)
from transcription_module.window_evidence import validate_window_draft_artifact
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from pydantic import BaseModel

    from transcription_module._drafting_models import (
        UnresolvedItem,
        WindowDraft,
        WindowDraftArtifact,
    )


def reconcile_policy_windows(
    artifact: WindowDraftArtifact,
) -> PolicyReconciliation:
    """Replay Task 11 authority, collapse source copies, and abstain on edits."""
    validate_window_draft_artifact(artifact, expected_run_id=artifact.run_id)
    policy_sha = require_shared_digest(
        tuple(item.policy_digest for item in artifact.inputs), "policy"
    )
    source_sha = require_shared_digest(
        tuple(item.source_digest for item in artifact.inputs), "source"
    )
    identities: dict[tuple[str, str], str] = {}
    grouped: dict[tuple[str, ...], list[ReconciliationCandidate]] = {}
    candidate_authorities: list[
        tuple[ReconciliationCandidate, WindowAuthority]
    ] = []
    for window_input, draft in zip(artifact.inputs, artifact.drafts, strict=True):
        authority = build_window_authority(window_input, draft)
        _register_authority(identities, authority, draft)
        unresolved = _unresolved_by_cue(draft)
        for allocated in allocate_window_authority(authority, draft):
            candidate = ReconciliationCandidate(
                allocated=allocated,
                unresolved=unresolved.get(allocated.record.cue_index, ()),
            )
            grouped.setdefault(source_group_key(candidate), []).append(candidate)
            candidate_authorities.append((candidate, authority))
    _reject_partial_lexical_source_sets(tuple(candidate_authorities))
    reconciled = tuple(
        reconcile_group(tuple(values)) for _, values in sorted(grouped.items())
    )
    ordered = tuple(sorted(reconciled, key=output_key))
    remapped = tuple(
        ReconciledCue(
            cue=item.cue,
            receipt=item.receipt,
            identity_refs=item.identity_refs,
            unresolved=tuple(
                unresolved.model_copy(update={"target_cue_index": index})
                for unresolved in item.unresolved
            ),
            frame_points=item.frame_points,
        )
        for index, item in enumerate(ordered)
    )
    provisional = _reconcile_provisional_insertions(artifact)
    frame_points = canonical_frame_points(
        point
        for item in (*remapped, *provisional)
        for point in item.frame_points
    )
    return PolicyReconciliation(
        policy_digest=policy_sha,
        source_digest=source_sha,
        cues=remapped,
        inspected_frame_ids=tuple(frame_id for _, frame_id in frame_points),
        inspected_frame_points=tuple(frame_points),
        provisional_insertions=provisional,
    )


def _reconcile_provisional_insertions(
    artifact: WindowDraftArtifact,
) -> tuple[ReconciledProvisionalInsertion, ...]:
    by_id: dict[str, ReconciledProvisionalInsertion] = {}
    for window_input, draft in zip(artifact.inputs, artifact.drafts, strict=True):
        observations = {
            item.observation_id: item for item in draft.observations
        }
        frame_points = {
            frame.frame_id: (frame.pts_ms, frame.frame_id)
            for frame in window_input.frames
        }
        for insertion in draft.provisional_visual_insertions:
            observation = observations[insertion.observation_id]
            reconciled = ReconciledProvisionalInsertion(
                insertion=insertion,
                frame_points=canonical_frame_points(
                    frame_points[frame_id]
                    for frame_id in observation.frame_ids
                ),
            )
            previous = by_id.setdefault(insertion.provisional_id, reconciled)
            if previous != reconciled:
                fail_window_evidence(
                    "policy assembly provisional insertion identity conflicts"
                )
    return tuple(
        sorted(
            by_id.values(),
            key=lambda item: (
                item.insertion.start_tc_ms,
                item.insertion.end_tc_ms,
                item.insertion.provisional_id,
            ),
        )
    )


def require_shared_digest(values: tuple[str | None, ...], label: str) -> str:
    known = {value for value in values if value is not None}
    if len(known) != 1 or len(known) != len(set(values)):
        fail_window_evidence(f"policy assembly has mixed or missing {label} identity")
    return next(iter(known))


def _register_authority(
    identities: dict[tuple[str, str], str],
    authority: WindowAuthority,
    draft: WindowDraft,
) -> None:
    for ordinal, item in authority.asr.items():
        register_evidence_identity(identities, ("asr", str(ordinal)), item)
    for identity, item in authority.subtitles.items():
        register_evidence_identity(identities, ("subtitle", identity), item)
    for identity, item in authority.turns.items():
        register_evidence_identity(identities, ("turn", identity), item)
    for identity, item in authority.frames.items():
        register_evidence_identity(identities, ("frame", str(identity)), item)
    for item in draft.observations:
        register_evidence_identity(
            identities, ("observation", item.observation_id), item
        )
    for item in draft.corrections:
        register_evidence_identity(identities, ("receipt", item.receipt_id), item)


def register_evidence_identity(
    identities: dict[tuple[str, str], str],
    key: tuple[str, str],
    item: BaseModel,
) -> None:
    payload = item.model_dump_json()
    previous = identities.setdefault(key, payload)
    if previous != payload:
        fail_window_evidence(
            f"policy assembly {key[0]} identity {key[1]} has conflicting evidence"
        )


def _unresolved_by_cue(draft: WindowDraft) -> dict[int, tuple[UnresolvedItem, ...]]:
    return {
        index: tuple(
            item for item in draft.unresolved if item.target_cue_index == index
        )
        for index in range(len(draft.cues))
    }


def _reject_partial_lexical_source_sets(
    candidates: tuple[tuple[ReconciliationCandidate, WindowAuthority], ...],
) -> None:
    for index, (left_candidate, left_authority) in enumerate(candidates):
        left = _lexical_source_set(left_candidate)
        if not left:
            continue
        for right_candidate, right_authority in candidates[index + 1 :]:
            right = _lexical_source_set(right_candidate)
            if (
                left != right
                and left & right
                and (
                    _is_grouped_action(left_authority, left_candidate)
                    or _is_grouped_action(right_authority, right_candidate)
                )
            ):
                fail_window_evidence(
                    "policy assembly has partially overlapping lexical source sets"
                )


def _is_grouped_action(
    authority: WindowAuthority,
    candidate: ReconciliationCandidate,
) -> bool:
    record = candidate.allocated.record
    return is_grouped_asr_claim(
        authority, record.requested_action, record.asr_ordinals
    )


def _lexical_source_set(
    candidate: ReconciliationCandidate,
) -> frozenset[tuple[str, str]]:
    record = candidate.allocated.record
    if record.asr_ordinals:
        return frozenset(("asr", str(item)) for item in record.asr_ordinals)
    return frozenset(("subtitle", item) for item in record.subtitle_cue_ids)


def source_group_key(candidate: ReconciliationCandidate) -> tuple[str, ...]:
    record = candidate.allocated.record
    action: object = record.requested_action
    if action is CorrectionAction.INSERT_CUE:
        return (
            "insertion",
            *(f"observation:{item}" for item in sorted(record.observation_ids)),
            *(f"turn:{item}" for item in sorted(record.turn_ids)),
        )
    preserving_or_replacing = {
        CorrectionAction.KEEP,
        CorrectionAction.REPLACE_SPAN,
        CorrectionAction.HOLD_FOR_CONFIRMATION,
    }
    if action not in preserving_or_replacing:
        fail_window_evidence("policy assembly has an unsupported correction action")
    lexical = (
        tuple(f"asr:{item}" for item in sorted(record.asr_ordinals))
        if record.asr_ordinals
        else tuple(f"subtitle:{item}" for item in sorted(record.subtitle_cue_ids))
    )
    if not lexical:
        fail_window_evidence("policy assembly cue lacks a source identity")
    return (
        "source",
        *lexical,
        *(f"turn:{item}" for item in sorted(record.turn_ids)),
    )


__all__ = ["PolicyReconciliation", "ReconciledCue", "reconcile_policy_windows"]
