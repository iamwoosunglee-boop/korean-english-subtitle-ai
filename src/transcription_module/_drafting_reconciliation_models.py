# Copyright 2026 Transcription Module contributors

"""Internal immutable values shared by deterministic transcript reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transcription_module._drafting_models import DraftCue, UnresolvedItem
    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.models import FrameId
    from transcription_module.window_authority_allocation import AllocatedCue
    from transcription_module.window_evidence_models import (
        ProvisionalVisualInsertion,
    )


@dataclass(frozen=True, slots=True)
class ReconciliationCandidate:
    """One Task 11 allocation plus unresolved records owned by its cue."""

    allocated: AllocatedCue
    unresolved: tuple[UnresolvedItem, ...]


@dataclass(frozen=True, slots=True)
class ReconciledCue:
    """One final cue with its sole applied receipt and stable identity inputs."""

    cue: DraftCue
    receipt: CorrectionReceipt
    identity_refs: tuple[str, ...]
    unresolved: tuple[UnresolvedItem, ...]
    frame_points: tuple[tuple[int, FrameId], ...]


@dataclass(frozen=True, slots=True)
class ReconciledProvisionalInsertion:
    """One non-exportable insertion and its inspected source frames."""

    insertion: ProvisionalVisualInsertion
    frame_points: tuple[tuple[int, FrameId], ...]


@dataclass(frozen=True, slots=True)
class PolicyReconciliation:
    """Complete deterministic output before permanent cue IDs are assigned."""

    policy_digest: str
    source_digest: str
    cues: tuple[ReconciledCue, ...]
    inspected_frame_ids: tuple[FrameId, ...]
    inspected_frame_points: tuple[tuple[int, FrameId], ...]
    provisional_insertions: tuple[ReconciledProvisionalInsertion, ...] = ()


__all__ = [
    "PolicyReconciliation",
    "ReconciledCue",
    "ReconciledProvisionalInsertion",
    "ReconciliationCandidate",
]
