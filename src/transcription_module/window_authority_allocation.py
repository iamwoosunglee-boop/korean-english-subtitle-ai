# Copyright 2026 Transcription Module contributors

"""Whole-window one-to-one authority allocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcription_module.correction_provenance import text_digest
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_authority_sources import (
    core_owned_asr,
    is_grouped_asr_claim,
)
from transcription_module.window_cue_identity import window_cue_identity
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from transcription_module._drafting_models import DraftCue, WindowDraft
    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.visual_observations import VisualObservation
    from transcription_module.window_authority_sources import WindowAuthority
    from transcription_module.window_evidence_models import WindowCueEvidence


@dataclass(frozen=True, slots=True)
class AllocatedCue:
    """One output cue after every exclusive identity has one owner."""

    cue: DraftCue
    record: WindowCueEvidence
    observation: VisualObservation
    receipt: CorrectionReceipt


def allocate_window_authority(
    authority: WindowAuthority,
    draft: WindowDraft,
) -> tuple[AllocatedCue, ...]:
    """Allocate every receipt, observation, turn, ASR, and subtitle at most once."""
    records = draft.cue_evidence
    expected_indices = tuple(range(len(draft.cues)))
    if tuple(item.cue_index for item in records) != expected_indices:
        fail_window_evidence(
            "every window cue requires one ordered evidence coverage record"
        )
    allocated = tuple(
        _bind_cue(authority, cue, record)
        for cue, record in zip(draft.cues, records, strict=True)
    )
    claims = _collect_claims(allocated)
    _reject_reused_claims(claims)
    _require_exhaustive_claims(authority, allocated, draft)
    for item in allocated:
        _validate_claim_references(authority, item)
    return allocated


def _collect_claims(
    allocated: tuple[AllocatedCue, ...],
) -> dict[tuple[str, str], list[str]]:
    claims: dict[tuple[str, str], list[str]] = {}
    for item in allocated:
        record = item.record
        owner = record.cue_identity
        _claim(claims, ("cue", owner), owner)
        _claim(claims, ("receipt", record.correction_receipt_id), owner)
        for value in record.observation_ids:
            _claim(claims, ("observation", value), owner)
        for value in record.turn_ids:
            _claim(claims, ("turn", value), owner)
        for value in record.asr_ordinals:
            _claim(claims, ("ASR", str(value)), owner)
        for value in record.subtitle_cue_ids:
            _claim(claims, ("subtitle", value), owner)
    return claims


def _reject_reused_claims(
    claims: dict[tuple[str, str], list[str]],
) -> None:
    reused = tuple(key for key, owners in claims.items() if len(owners) != 1)
    if not reused:
        return
    kind, identity = min(reused)
    if kind == "receipt":
        fail_window_evidence(
            "window correction receipts must be used exactly once by cue coverage"
        )
    fail_window_evidence(
        f"window {kind} authority {identity} is reused across cue claims"
    )


def _require_exhaustive_claims(
    authority: WindowAuthority,
    allocated: tuple[AllocatedCue, ...],
    draft: WindowDraft,
) -> None:
    used_receipts = tuple(item.record.correction_receipt_id for item in allocated)
    if set(used_receipts) != set(authority.receipts):
        fail_window_evidence(
            "window correction receipts must be used exactly once by cue coverage"
        )
    cue_observations = tuple(
        identity for item in allocated for identity in item.record.observation_ids
    )
    provisional_observations = tuple(
        item.observation_id for item in draft.provisional_visual_insertions
    )
    used_observations = (*cue_observations, *provisional_observations)
    if len(used_observations) != len(set(used_observations)):
        fail_window_evidence(
            "window observation authority is reused across cue and provisional claims"
        )
    if set(used_observations) != set(authority.observations):
        fail_window_evidence(
            "window observations must be used exactly once by cue coverage"
        )


def _bind_cue(
    authority: WindowAuthority,
    cue: DraftCue,
    record: WindowCueEvidence,
) -> AllocatedCue:
    if (
        record.start_tc_ms != cue.start_tc_ms
        or record.end_tc_ms != cue.end_tc_ms
        or record.speaker != cue.speaker
        or record.speech_digest != text_digest(cue.speech)
        or record.cue_identity != window_cue_identity(cue)
    ):
        fail_window_evidence("window cue evidence binding is stale")
    receipt = authority.receipts.get(record.correction_receipt_id)
    if receipt is None:
        fail_window_evidence("window cue correction receipt is missing")
    if len(record.observation_ids) != 1:
        fail_window_evidence("window cue requires one complete visual observation")
    observation = authority.observations.get(record.observation_ids[0])
    if observation is None:
        fail_window_evidence("window cue references an unknown observation")
    return AllocatedCue(cue, record, observation, receipt)


def _validate_claim_references(
    authority: WindowAuthority,
    allocated: AllocatedCue,
) -> None:
    if tuple(allocated.receipt.observation_ids) != allocated.record.observation_ids:
        fail_window_evidence(
            "window cue observation references differ from its receipt"
        )
    _require_known_sources(authority, allocated.record)


def validate_core_contiguous_asr_claim(
    authority: WindowAuthority,
    record: WindowCueEvidence,
) -> None:
    """Require one ordered contiguous slice owned by this half-open core."""
    claimed = record.asr_ordinals
    grouped_action = record.requested_action in {
        CorrectionAction.KEEP,
        CorrectionAction.HOLD_FOR_CONFIRMATION,
        CorrectionAction.REPLACE_SPAN,
    }
    if not grouped_action or not is_grouped_asr_claim(
        authority, record.requested_action, claimed
    ):
        return
    owned = tuple(
        item.source_ordinal
        for item in core_owned_asr(authority)
        if item.source_ordinal is not None
    )
    claim_length = len(claimed)
    if not any(
        owned[index : index + claim_length] == claimed
        for index in range(len(owned) - claim_length + 1)
    ):
        fail_window_evidence(
            "window cue ASR claim is not one contiguous ordered core-owned slice"
        )


def _require_known_sources(
    authority: WindowAuthority,
    record: WindowCueEvidence,
) -> None:
    if not set(record.asr_ordinals) <= set(authority.asr):
        fail_window_evidence("window cue references an unknown ASR ordinal")
    if not set(record.subtitle_track_ids) <= set(authority.tracks):
        fail_window_evidence("window cue references an unknown subtitle track")
    if not set(record.subtitle_cue_ids) <= set(authority.subtitles):
        fail_window_evidence("window cue references an unknown subtitle cue")
    if not set(record.vad_interval_ids) <= set(authority.vad):
        fail_window_evidence("window cue references unknown VAD evidence")
    subtitle_tracks = tuple(
        dict.fromkeys(
            authority.subtitles[item].track_id for item in record.subtitle_cue_ids
        )
    )
    if subtitle_tracks != record.subtitle_track_ids:
        fail_window_evidence("window subtitle cue is bound to another track")
    expected_sources = (
        *(f"asr:{item}" for item in record.asr_ordinals),
        *(f"subtitle:{item}" for item in record.subtitle_cue_ids),
        *(f"vision:{item}" for item in record.observation_ids),
    )
    if expected_sources != record.source_evidence_ids:
        fail_window_evidence("window cue source evidence references are not exact")
    if not record.vad_interval_ids:
        fail_window_evidence("window cue lacks timed speech authority")


def _claim(
    claims: dict[tuple[str, str], list[str]],
    key: tuple[str, str],
    owner: str,
) -> None:
    claims.setdefault(key, []).append(owner)


__all__ = [
    "AllocatedCue",
    "allocate_window_authority",
    "validate_core_contiguous_asr_claim",
]
