# Copyright 2026 Transcription Module contributors

"""Shared policy claim contracts and real input loading for integration tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    DraftCue,
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.models import FrameId
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
)
from transcription_module.visual_observations import VisualObservation

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.window_evidence_models import WindowCueEvidence


@dataclass(frozen=True, slots=True)
class ClaimBundle:
    """One cue and all independently persisted authority it claims."""

    cue: DraftCue
    observation: VisualObservation
    receipt: CorrectionReceipt
    evidence: WindowCueEvidence


@dataclass(frozen=True, slots=True)
class ObservationSpec:
    """Complete semantic and temporal observation identity for a claim."""

    observation_id: str
    frame_ids: tuple[str, ...]
    frame_pts_ms: tuple[int, ...]
    text: str
    vad_id: str
    turn_id: str | None


def make_observation(
    data: WindowDraftInput,
    spec: ObservationSpec,
) -> VisualObservation:
    """Build one exact supported observation from persisted local frames."""
    return VisualObservation(
        observation_id=spec.observation_id,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_digest(data),
        role=VisualRole.SPEECH_SUPPORTING,
        state=EvidenceState.SUPPORTED,
        semantic_relation=SemanticRelation.EXACT,
        frame_ids=tuple(FrameId(item) for item in spec.frame_ids),
        frame_pts_ms=spec.frame_pts_ms,
        start_tc_ms=min(spec.frame_pts_ms) - 100,
        end_tc_ms=max(spec.frame_pts_ms) + 200,
        complete_text=spec.text,
        vad_interval_ids=(spec.vad_id,),
        turn_ids=() if spec.turn_id is None else (spec.turn_id,),
        caption_regime_supported=True,
        temporal_alignment_supported=True,
        speech_presence_supported=True,
        evidence_for=("exact_text", "timed_speech", "verified_turn"),
    )


def make_draft(
    data: WindowDraftInput,
    claims: tuple[ClaimBundle, ...],
) -> WindowDraft:
    """Aggregate independent claims under the exact supplied window identity."""
    return WindowDraft(
        window_index=data.window_index,
        cues=tuple(item.cue for item in claims),
        unresolved=(),
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=data.policy_digest,
        inventory_digest=data.inventory_digest,
        source_digest=data.source_digest,
        observations=tuple(item.observation for item in claims),
        corrections=tuple(item.receipt for item in claims),
        cue_evidence=tuple(item.evidence for item in claims),
    )


def load_policy_window_input(root: Path) -> WindowDraftInput:
    """Load the production input boundary from complete persisted sidecars."""
    fixture = create_policy_window_fixture(root)
    captured: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )
    return captured[0]


def policy_digest(data: WindowDraftInput) -> str:
    """Return required test policy identity without a narrowing cast."""
    if data.policy_digest is None:
        message = "test input requires a policy digest"
        raise AssertionError(message)
    return data.policy_digest


def source_digest(data: WindowDraftInput) -> str:
    """Return required test source identity without a narrowing cast."""
    if data.source_digest is None:
        message = "test input requires a source digest"
        raise AssertionError(message)
    return data.source_digest


DraftBuilder = Callable[[WindowDraftInput], WindowDraft]

__all__ = [
    "ClaimBundle",
    "DraftBuilder",
    "ObservationSpec",
    "load_policy_window_input",
    "make_draft",
    "make_observation",
    "policy_digest",
    "source_digest",
]
