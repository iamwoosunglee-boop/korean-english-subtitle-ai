# Copyright 2026 Transcription Module contributors

"""Deterministic construction and replay validation of correction receipts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, assert_never

from transcription_module.correction_receipts import CorrectionReceipt
from transcription_module.provenance_errors import CueProvenanceError
from transcription_module.visual_correction import (
    CorrectionValidationError,
    TextSlice,
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import CorrectionAction

if TYPE_CHECKING:
    from transcription_module.visual_correction_models import ValidatedCorrection


def text_digest(text: str) -> str:
    """Return the exact UTF-8 SHA-256 identity of one text value."""
    return hashlib.sha256(text.encode()).hexdigest()


def correction_input_digest(request: VisualCorrectionRequest) -> str:
    """Digest every exact correction input with canonical JSON key ordering."""
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def build_correction_receipt(
    source_digest: str,
    request: VisualCorrectionRequest,
    validated: ValidatedCorrection,
) -> CorrectionReceipt:
    """Build one replay-stable receipt after independently reproducing the outcome."""
    try:
        expected = validate_visual_correction(request)
    except CorrectionValidationError as error:
        message = "correction request cannot produce a valid receipt"
        raise CueProvenanceError(message) from error
    if expected != validated:
        message = "validated correction does not match deterministic replay"
        raise CueProvenanceError(message)
    canonical_request = _canonical_request(request, validated)
    input_sha = correction_input_digest(canonical_request)
    before_sha = text_digest(canonical_request.before_text)
    result_sha = text_digest(validated.result_text)
    candidate = validated.candidate_text
    candidate_sha = None if candidate is None else text_digest(candidate)
    receipt_id = _receipt_id(
        source_digest,
        input_sha,
        before_sha,
        result_sha,
        validated.action,
    )
    return CorrectionReceipt(
        receipt_id=receipt_id,
        era=request.observation.era,
        policy_digest=request.policy_digest,
        action=validated.action,
        source_digest=source_digest,
        input_digest=input_sha,
        before_text=canonical_request.before_text,
        result_text=validated.result_text,
        before_digest=before_sha,
        result_digest=result_sha,
        observation_ids=(request.observation.observation_id,),
        candidate_text=candidate,
        candidate_digest=candidate_sha,
        source_slice=canonical_request.source_slice,
        candidate_slice=canonical_request.candidate_slice,
    )


def _canonical_request(
    request: VisualCorrectionRequest,
    validated: ValidatedCorrection,
) -> VisualCorrectionRequest:
    match validated.action:
        case CorrectionAction.INSERT_CUE:
            candidate = validated.candidate_text
            if candidate is None:
                message = "insertion correction lacks canonical candidate text"
                raise CueProvenanceError(message)
            canonical_slice = TextSlice(start=0, end=len(candidate))
            if (
                request.candidate_slice is not None
                and request.candidate_slice != canonical_slice
            ):
                message = "insertion request has a non-canonical insertion slice"
                raise CueProvenanceError(message)
            return request.model_copy(
                update={"candidate_slice": canonical_slice}
            )
        case (
            CorrectionAction.KEEP
            | CorrectionAction.REPLACE_SPAN
            | CorrectionAction.HOLD_FOR_CONFIRMATION
        ):
            return request
    assert_never(validated.action)


def validate_correction_receipt(
    receipt: CorrectionReceipt,
    source_digest: str,
    request: VisualCorrectionRequest,
) -> None:
    """Reproduce one exact receipt and reject any stale or forged field."""
    try:
        expected = build_correction_receipt(
            source_digest,
            request,
            validate_visual_correction(request),
        )
    except (CorrectionValidationError, CueProvenanceError) as error:
        message = "stale correction receipt cannot be replayed"
        raise CueProvenanceError(message) from error
    if receipt != expected:
        message = "stale correction receipt does not match current inputs"
        raise CueProvenanceError(message)


def _receipt_id(
    source_digest: str,
    input_digest: str,
    before_digest: str,
    result_digest: str,
    action: CorrectionAction,
) -> str:
    payload = json.dumps(
        (
            source_digest,
            input_digest,
            before_digest,
            result_digest,
            action.value,
        ),
        separators=(",", ":"),
    ).encode()
    return f"correction-{hashlib.sha256(payload).hexdigest()[:32]}"


__all__ = [
    "build_correction_receipt",
    "correction_input_digest",
    "text_digest",
    "validate_correction_receipt",
]
