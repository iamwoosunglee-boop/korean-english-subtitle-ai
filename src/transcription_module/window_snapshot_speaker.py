# Copyright 2026 Transcription Module contributors

"""Captured named-speaker authority inputs for one window transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcription_module.window_evidence_errors import fail_window_evidence
from transcription_module.window_speaker_authority_models import (
    SpeakerTurnEvidenceSnapshot,
)

if TYPE_CHECKING:
    from transcription_module.storage_paths import RunPaths
    from transcription_module.subtitle_inventory import SubtitleInventory
    from transcription_module.window_snapshot_fs import SnapshotBuilder


@dataclass(frozen=True, slots=True)
class CapturedSpeakerFiles:
    """Exact optional derived payload and complete admitted proof bytes."""

    derived_payload: bytes | None
    evidence: SpeakerTurnEvidenceSnapshot | None


def capture_speaker_files(
    builder: SnapshotBuilder,
    paths: RunPaths,
    inventory: SubtitleInventory,
) -> CapturedSpeakerFiles:
    """Open every known speaker input once before deciding its authority."""
    authority = builder.optional(
        "speaker-turn authority path",
        paths.speaker_turn_authority_artifact,
    )
    observations = builder.optional(
        "speaker-turn observation path",
        paths.visual_observations_artifact,
    )
    decisions = builder.optional(
        "speaker-turn confirmation path",
        paths.speaker_turn_decisions,
    )
    derived = builder.optional(
        "verified speaker-turn authority path",
        paths.verified_speaker_turns_artifact,
    )
    admitted = bool(inventory.evidence_authorities)
    if not admitted:
        return CapturedSpeakerFiles(
            derived_payload=(None if derived is None else derived.require_payload()),
            evidence=None,
        )
    if authority is None or observations is None or decisions is None:
        fail_window_evidence("admitted speaker-turn authority path is missing")
    return CapturedSpeakerFiles(
        derived_payload=(None if derived is None else derived.require_payload()),
        evidence=SpeakerTurnEvidenceSnapshot(
            authority_payload=authority.require_payload(),
            observation_payload=observations.require_payload(),
            decision_payload=decisions.require_payload(),
        ),
    )


__all__ = ["CapturedSpeakerFiles", "capture_speaker_files"]
