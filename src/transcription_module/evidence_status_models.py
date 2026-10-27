# Copyright 2026 Transcription Module contributors

"""Typed operator report for policy-bound transcription authority."""

from __future__ import annotations

from enum import StrEnum

from transcription_module.models import FrozenModel, RunId
from transcription_module.visual_evidence_policy import EvidenceEra  # noqa: TC001


class EvidenceArtifactState(StrEnum):
    """Operator-visible state of one fixed authority artifact."""

    CURRENT = "current"
    STALE = "stale"
    INVALID = "invalid"
    ABSENT = "absent"


class EvidenceArtifactStatus(FrozenModel):
    """One fixed run-relative artifact and its current byte identity."""

    path: str
    state: EvidenceArtifactState
    sha256: str | None = None


class SubtitleInventoryStatus(EvidenceArtifactStatus):
    """Exact admitted subtitle availability without exposing subtitle text."""

    availability: str
    exact_ko_orig_count: int


class OperationEventsStatus(FrozenModel):
    """Documented behavior when the orchestration extension is unavailable."""

    availability: str = "unavailable"
    reason_code: str = "operation_events_unavailable"
    blocks_transcription: bool = False


class EvidenceStatusReport(FrozenModel):
    """Complete read-only policy authority and lifecycle inventory."""

    run_id: RunId
    era: EvidenceEra
    protected_run: EvidenceArtifactStatus
    media: EvidenceArtifactStatus
    asr: EvidenceArtifactStatus
    policy: EvidenceArtifactStatus
    subtitle_inventory: SubtitleInventoryStatus
    vad: EvidenceArtifactStatus
    visual_observations: EvidenceArtifactStatus
    correction_receipts: EvidenceArtifactStatus
    cue_provenance: EvidenceArtifactStatus
    window_evidence: EvidenceArtifactStatus
    deterministic_assembly: EvidenceArtifactStatus
    confirmation_anchor: EvidenceArtifactStatus
    final_review: EvidenceArtifactStatus
    validation: EvidenceArtifactStatus
    context_asset: EvidenceArtifactStatus
    canonical_source: EvidenceArtifactStatus
    invalidation_required: bool
    reusable: bool
    operation_events: OperationEventsStatus = OperationEventsStatus()


__all__ = [
    "EvidenceArtifactState",
    "EvidenceArtifactStatus",
    "EvidenceStatusReport",
    "SubtitleInventoryStatus",
]
