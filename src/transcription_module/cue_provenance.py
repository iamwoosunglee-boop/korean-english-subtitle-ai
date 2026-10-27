# Copyright 2026 Transcription Module contributors

"""Stable cue identity assignment and complete provenance validation."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from transcription_module.correction_provenance import text_digest
from transcription_module.cue_provenance_models import (
    CueIdentityInput,
    CueProvenanceRecord,
    CueProvenanceSet,
    CueProvenanceValidationInput,
)
from transcription_module.models import CueId, RunId
from transcription_module.provenance_errors import CueProvenanceError
from transcription_module.visual_evidence_policy import EvidenceEra

if TYPE_CHECKING:
    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.models import Cue
    from transcription_module.visual_observations import VisualObservation

CueIdFactory = Callable[[bytes], CueId]


def assign_cue_ids(
    run_id: RunId,
    policy_digest: str,
    inputs: tuple[CueIdentityInput, ...],
    *,
    id_factory: CueIdFactory | None = None,
) -> tuple[CueId, ...]:
    """Preserve existing IDs and derive deterministic IDs only for new-era cues."""
    factory = _default_id_factory if id_factory is None else id_factory
    assigned = tuple(
        _assign_one_cue_id(run_id, policy_digest, item, factory)
        for item in inputs
    )
    if len(assigned) != len(set(assigned)):
        message = "cue ID collision detected"
        raise CueProvenanceError(message)
    return assigned


def validate_cue_provenance(inputs: CueProvenanceValidationInput) -> None:
    """Verify exact cue order and every source, observation, and receipt reference."""
    _validate_top_level_bindings(inputs)
    cues = inputs.transcript.cues
    records = inputs.provenance.records
    if len(cues) != len(records):
        message = "every transcript cue requires exactly one provenance record"
        raise CueProvenanceError(message)
    receipts = {
        receipt.receipt_id: receipt for receipt in inputs.corrections.receipts
    }
    observations = {
        item.observation_id: item for item in inputs.observations.observations
    }
    for cue, record in zip(cues, records, strict=True):
        _validate_record(
            cue,
            record,
            source_digest=inputs.source_digest,
            receipts=receipts,
            observations=observations,
        )


def _assign_one_cue_id(
    run_id: RunId,
    policy_digest: str,
    item: CueIdentityInput,
    factory: CueIdFactory,
) -> CueId:
    if item.existing_cue_id is not None:
        return item.existing_cue_id
    if item.era is EvidenceEra.LEGACY:
        message = "legacy cue ID must be preserved"
        raise CueProvenanceError(message)
    payload = json.dumps(
        {
            "run_id": str(run_id),
            "policy_digest": policy_digest,
            "source_evidence_ids": item.source_evidence_ids,
            "start_tc_ms": item.start_tc_ms,
            "end_tc_ms": item.end_tc_ms,
            "normalized_text": normalize_identity_text(item.speech),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return factory(payload)


def _default_id_factory(payload: bytes) -> CueId:
    return CueId(str(uuid5(NAMESPACE_URL, payload.decode())))


def normalize_identity_text(text: str) -> str:
    """Normalize cue identity text with NFC and whitespace collapse only."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _validate_top_level_bindings(inputs: CueProvenanceValidationInput) -> None:
    run_ids = {
        inputs.transcript.run_id,
        inputs.provenance.run_id,
        inputs.corrections.run_id,
        inputs.observations.run_id,
    }
    if len(run_ids) != 1:
        message = "cue provenance authority has a foreign run identity"
        raise CueProvenanceError(message)
    era = inputs.provenance.era
    if inputs.corrections.era is not era or inputs.observations.era is not era:
        message = "cue provenance authority mixes evidence eras"
        raise CueProvenanceError(message)
    if era is EvidenceEra.POLICY_BOUND:
        expected = inputs.policy_digest
        if expected is None:
            message = "policy-bound cue provenance requires current policy digest"
            raise CueProvenanceError(message)
        digests = {
            inputs.provenance.policy_digest,
            inputs.corrections.policy_digest,
            inputs.observations.policy_digest,
        }
        if digests != {expected}:
            message = "cue provenance authority has a foreign policy digest"
            raise CueProvenanceError(message)


def _validate_record(
    cue: Cue,
    record: CueProvenanceRecord,
    *,
    source_digest: str,
    receipts: dict[str, CorrectionReceipt],
    observations: dict[str, VisualObservation],
) -> None:
    speech_sha = _validate_cue_binding(cue, record, source_digest)
    receipt = _require_receipt(record, receipts)
    _validate_receipt_binding(receipt, record, source_digest, speech_sha)
    _validate_observation_references(record, observations)
    _validate_source_evidence_references(record)


def _validate_cue_binding(
    cue: Cue,
    record: CueProvenanceRecord,
    source_digest: str,
) -> str:
    if cue.cue_id is None or cue.cue_id != record.cue_id:
        message = "cue provenance would rewrite an existing cue ID"
        raise CueProvenanceError(message)
    if (
        int(cue.start_tc_ms) != record.start_tc_ms
        or int(cue.end_tc_ms) != record.end_tc_ms
        or cue.speaker != record.speaker
    ):
        message = "cue provenance timing or speaker binding is stale"
        raise CueProvenanceError(message)
    if record.source_digest != source_digest:
        message = "cue provenance source digest is stale"
        raise CueProvenanceError(message)
    speech_sha = text_digest(cue.speech)
    if record.speech_digest != speech_sha:
        message = "cue provenance speech digest is stale"
        raise CueProvenanceError(message)
    return speech_sha


def _require_receipt(
    record: CueProvenanceRecord,
    receipts: dict[str, CorrectionReceipt],
) -> CorrectionReceipt:
    receipt = receipts.get(record.correction_receipt_id)
    if receipt is None:
        message = "cue provenance correction receipt is missing"
        raise CueProvenanceError(message)
    return receipt


def _validate_receipt_binding(
    receipt: CorrectionReceipt,
    record: CueProvenanceRecord,
    source_digest: str,
    speech_sha: str,
) -> None:
    if receipt.source_digest != source_digest or receipt.result_digest != speech_sha:
        message = "cue provenance correction receipt is stale"
        raise CueProvenanceError(message)
    if tuple(receipt.observation_ids) != record.observation_ids:
        message = "cue provenance observation references differ from receipt"
        raise CueProvenanceError(message)


def _validate_observation_references(
    record: CueProvenanceRecord,
    observations: dict[str, VisualObservation],
) -> None:
    missing = tuple(
        observation_id
        for observation_id in record.observation_ids
        if observation_id not in observations
    )
    if missing:
        message = "cue provenance observation reference is missing"
        raise CueProvenanceError(message)


def _validate_source_evidence_references(record: CueProvenanceRecord) -> None:
    required_source_ids = _required_source_evidence_ids(record)
    if not required_source_ids <= set(record.source_evidence_ids):
        message = "cue provenance source evidence references are incomplete"
        raise CueProvenanceError(message)


def _required_source_evidence_ids(record: CueProvenanceRecord) -> set[str]:
    return {
        *(f"asr:{ordinal}" for ordinal in record.asr_ordinals),
        *(f"subtitle:{cue_id}" for cue_id in record.subtitle_cue_ids),
        *(f"vision:{item}" for item in record.observation_ids),
    }


__all__ = [
    "CueIdentityInput",
    "CueProvenanceError",
    "CueProvenanceRecord",
    "CueProvenanceSet",
    "CueProvenanceValidationInput",
    "assign_cue_ids",
    "normalize_identity_text",
    "validate_cue_provenance",
]
