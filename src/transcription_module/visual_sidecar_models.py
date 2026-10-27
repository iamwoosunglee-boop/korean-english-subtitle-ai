# Copyright 2026 Transcription Module contributors

"""Complete visual sidecar bundle and deterministic publication receipt."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.correction_receipts import (  # noqa: TC001
    CorrectionReceiptSet,
)
from transcription_module.cue_provenance_models import (  # noqa: TC001
    CueProvenanceSet,
)
from transcription_module.models import FrozenModel, RunId
from transcription_module.subtitle_inventory import SubtitleInventory  # noqa: TC001
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    VisualEvidencePolicy,
    policy_digest,
)
from transcription_module.visual_observations import (  # noqa: TC001
    VisualObservationSet,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class VisualSidecarBundle(FrozenModel):
    """All semantic evidence required before any sidecar path may mutate."""

    schema_version: Literal["visual-sidecar-bundle.v1"] = (
        "visual-sidecar-bundle.v1"
    )
    run_id: RunId
    source_digest: Sha256Digest
    policy: VisualEvidencePolicy
    inventory: SubtitleInventory
    observations: VisualObservationSet
    corrections: CorrectionReceiptSet
    provenance: CueProvenanceSet

    @model_validator(mode="after")
    def all_authority_bindings_match(self) -> Self:
        """Reject mixed run, era, policy, or source identities."""
        policy_sha = policy_digest(self.policy)
        run_ids = {
            self.run_id,
            self.observations.run_id,
            self.corrections.run_id,
            self.provenance.run_id,
        }
        if len(run_ids) != 1:
            message = "visual sidecar bundle has a foreign run identity"
            raise ValueError(message)
        eras = {
            self.inventory.era,
            self.observations.era,
            self.corrections.era,
            self.provenance.era,
        }
        if eras != {EvidenceEra.POLICY_BOUND}:
            message = "visual sidecar bundle requires policy-bound evidence"
            raise ValueError(message)
        digests = {
            self.inventory.policy_digest,
            self.observations.policy_digest,
            self.corrections.policy_digest,
            self.provenance.policy_digest,
        }
        if digests != {policy_sha}:
            message = "visual sidecar bundle has a foreign policy digest"
            raise ValueError(message)
        if any(
            receipt.source_digest != self.source_digest
            for receipt in self.corrections.receipts
        ) or any(
            record.source_digest != self.source_digest
            for record in self.provenance.records
        ):
            message = "visual sidecar bundle has a stale source digest"
            raise ValueError(message)
        return self


class VisualSidecarArtifactReceipt(FrozenModel):
    """One fixed run-relative sidecar path and exact committed SHA-256."""

    path: NonBlank
    sha256: Sha256Digest


class VisualSidecarPublicationReceipt(FrozenModel):
    """Deterministic receipt for one exact semantic sidecar bundle."""

    schema_version: Literal["visual-sidecar-publication.v1"] = (
        "visual-sidecar-publication.v1"
    )
    run_id: RunId
    policy_digest: Sha256Digest
    source_digest: Sha256Digest
    artifacts: tuple[VisualSidecarArtifactReceipt, ...] = Field(min_length=1)


__all__ = [
    "VisualSidecarArtifactReceipt",
    "VisualSidecarBundle",
    "VisualSidecarPublicationReceipt",
]
