# Copyright 2026 Transcription Module contributors

"""Deterministic visual-correction receipts and complete sidecar bundles."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import FrozenModel, RunId
from transcription_module.subtitle_inventory import (  # noqa: TC001 - Pydantic runtime
    SubtitleInventory,
)
from transcription_module.visual_correction_models import (  # noqa: TC001
    TextSlice,
)
from transcription_module.visual_evidence_policy import (
    CorrectionAction,
    EvidenceEra,
    VisualEvidencePolicy,
    policy_digest,
)
from transcription_module.visual_observations import (  # noqa: TC001 - Pydantic runtime
    VisualObservationSet,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CorrectionReceipt(FrozenModel):
    """One action bound to input, before, result, candidate, and observations."""

    schema_version: Literal["correction-receipt.v1"] = "correction-receipt.v1"
    receipt_id: NonBlank
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    action: CorrectionAction
    source_digest: Sha256Digest
    input_digest: Sha256Digest
    before_text: str | None = None
    result_text: str | None = None
    before_digest: Sha256Digest
    result_digest: Sha256Digest
    observation_ids: tuple[NonBlank, ...]
    candidate_text: str | None = None
    candidate_digest: Sha256Digest | None = None
    source_slice: TextSlice | None = None
    candidate_slice: TextSlice | None = None

    @model_validator(mode="after")
    def receipt_contract_is_consistent(self) -> Self:
        """Require policy identity and bounded candidate-bearing mutations."""
        _validate_receipt_identity(self)
        _validate_receipt_text_digests(self)
        _validate_candidate_binding(self)
        _validate_action_trace(self)
        return self


def _validate_receipt_identity(receipt: CorrectionReceipt) -> None:
    if len(receipt.observation_ids) != len(set(receipt.observation_ids)):
        message = "correction receipt observation IDs must be unique"
        raise ValueError(message)
    if receipt.era is EvidenceEra.POLICY_BOUND and receipt.policy_digest is None:
        message = "policy-bound correction receipt requires policy digest"
        raise ValueError(message)


def _validate_receipt_text_digests(receipt: CorrectionReceipt) -> None:
    if receipt.era is EvidenceEra.POLICY_BOUND and (
        receipt.before_text is None or receipt.result_text is None
    ):
        message = "policy-bound correction receipt requires before and result text"
        raise ValueError(message)
    for text, expected, label in (
        (receipt.before_text, receipt.before_digest, "before"),
        (receipt.result_text, receipt.result_digest, "result"),
    ):
        if text is not None and hashlib.sha256(text.encode()).hexdigest() != expected:
            message = f"{label} digest does not match {label} text"
            raise ValueError(message)


def _validate_candidate_binding(receipt: CorrectionReceipt) -> None:
    mutation = receipt.action in {
        CorrectionAction.REPLACE_SPAN,
        CorrectionAction.INSERT_CUE,
    }
    if mutation and (
        receipt.candidate_text is None or not receipt.candidate_text.strip()
    ):
        message = "speech-changing correction requires candidate text"
        raise ValueError(message)
    if mutation and receipt.candidate_digest is None:
        message = "speech-changing correction requires candidate digest"
        raise ValueError(message)
    if receipt.candidate_text is not None and receipt.candidate_digest is not None:
        observed = hashlib.sha256(receipt.candidate_text.encode()).hexdigest()
        if observed != receipt.candidate_digest:
            message = "candidate digest does not match candidate text"
            raise ValueError(message)


def _validate_action_trace(receipt: CorrectionReceipt) -> None:
    validators = {
        CorrectionAction.REPLACE_SPAN: _validate_replacement_trace,
        CorrectionAction.INSERT_CUE: _validate_insertion_trace,
    }
    if validator := validators.get(receipt.action):
        validator(receipt)
        return
    if receipt.before_digest != receipt.result_digest:
        message = "source-preserving receipt cannot change the result digest"
        raise ValueError(message)


def _validate_replacement_trace(receipt: CorrectionReceipt) -> None:
    if receipt.source_slice is None or receipt.candidate_slice is None:
        message = "replacement receipt requires exact source and candidate slices"
        raise ValueError(message)
    if (
        receipt.before_text is None
        or receipt.result_text is None
        or receipt.candidate_text is None
    ):
        return
    _require_slice_bounds(receipt.before_text, receipt.source_slice, "source")
    _require_slice_bounds(receipt.candidate_text, receipt.candidate_slice, "candidate")
    expected = (
        receipt.before_text[: receipt.source_slice.start]
        + receipt.candidate_text[
            receipt.candidate_slice.start : receipt.candidate_slice.end
        ]
        + receipt.before_text[receipt.source_slice.end :]
    )
    if expected != receipt.result_text:
        message = "replacement receipt cannot reconstruct exact result text"
        raise ValueError(message)


def _validate_insertion_trace(receipt: CorrectionReceipt) -> None:
    if receipt.candidate_slice is None:
        message = "insertion receipt requires exact candidate slice"
        raise ValueError(message)
    if receipt.candidate_text is None or receipt.result_text is None:
        return
    _require_slice_bounds(receipt.candidate_text, receipt.candidate_slice, "candidate")
    expected = receipt.candidate_text[
        receipt.candidate_slice.start : receipt.candidate_slice.end
    ]
    if receipt.before_text != "" or receipt.result_text != expected:
        message = "insertion receipt cannot reconstruct exact candidate text"
        raise ValueError(message)


def _require_slice_bounds(text: str, selected: TextSlice, label: str) -> None:
    if selected.end > len(text):
        message = f"{label} slice exceeds correction receipt text"
        raise ValueError(message)


class CorrectionReceiptSet(FrozenModel):
    """All correction outcomes for one run under one policy identity."""

    schema_version: Literal["correction-receipt-set.v1"] = (
        "correction-receipt-set.v1"
    )
    run_id: RunId
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    receipts: tuple[CorrectionReceipt, ...]

    @model_validator(mode="after")
    def receipt_bindings_match_set(self) -> Self:
        """Reject duplicate IDs, mixed eras, and cross-policy receipts."""
        receipt_ids = tuple(item.receipt_id for item in self.receipts)
        if len(receipt_ids) != len(set(receipt_ids)):
            message = "correction receipt IDs must be unique"
            raise ValueError(message)
        if any(item.era is not self.era for item in self.receipts):
            message = "correction receipt set cannot mix eras"
            raise ValueError(message)
        if self.era is EvidenceEra.POLICY_BOUND:
            if self.policy_digest is None:
                message = "policy-bound correction set requires policy digest"
                raise ValueError(message)
            if any(
                item.policy_digest != self.policy_digest for item in self.receipts
            ):
                message = "correction receipt has a foreign policy digest"
                raise ValueError(message)
        return self


class EvidenceSidecarBundle(FrozenModel):
    """Legacy-compatible aggregate that fails closed for new-era omissions."""

    schema_version: Literal["visual-evidence-sidecars.v1"] = (
        "visual-evidence-sidecars.v1"
    )
    era: EvidenceEra
    policy: VisualEvidencePolicy | None = None
    inventory: SubtitleInventory | None = None
    observations: VisualObservationSet | None = None
    corrections: CorrectionReceiptSet | None = None

    @property
    def policy_digest(self) -> str | None:
        """Return the computed policy identity when a policy is present."""
        return policy_digest(self.policy) if self.policy is not None else None

    @model_validator(mode="after")
    def bundle_is_complete_and_digest_consistent(self) -> Self:
        """Allow legacy reads but require all matching new-era sidecars."""
        sidecars = (self.inventory, self.observations, self.corrections)
        if self.era is EvidenceEra.LEGACY:
            if any(
                item is not None and item.era is not EvidenceEra.LEGACY
                for item in sidecars
            ):
                message = "legacy bundle cannot contain policy-bound sidecars"
                raise ValueError(message)
            return self
        if self.policy is None or any(item is None for item in sidecars):
            message = "policy-bound bundle requires policy and every sidecar"
            raise ValueError(message)
        expected = policy_digest(self.policy)
        bound_sidecars = tuple(item for item in sidecars if item is not None)
        if any(item.era is not EvidenceEra.POLICY_BOUND for item in bound_sidecars):
            message = "policy-bound bundle cannot contain legacy sidecars"
            raise ValueError(message)
        if any(item.policy_digest != expected for item in bound_sidecars):
            message = "policy-bound sidecar digest does not match policy"
            raise ValueError(message)
        return self


__all__ = [
    "CorrectionReceipt",
    "CorrectionReceiptSet",
    "EvidenceSidecarBundle",
]
