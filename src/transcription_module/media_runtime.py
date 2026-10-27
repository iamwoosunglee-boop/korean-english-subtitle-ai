# Copyright 2026 Transcription Module contributors

"""Injected adapter contracts and completion clock for media preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from transcription_module.media_checkpoint import (
    MediaAdapterContracts,
    MediaCheckpointStore,
    StageCompletion,
    StageName,
)

if TYPE_CHECKING:
    from transcription_module.media import MediaAdapters


@dataclass(frozen=True, slots=True)
class CheckpointedMediaAdapters:
    """Media implementations paired with contracts and one completion clock."""

    adapters: MediaAdapters
    contracts: MediaAdapterContracts
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def stage_completion(
    store: MediaCheckpointStore,
    stage: StageName,
    runtime: CheckpointedMediaAdapters,
) -> StageCompletion:
    """Inspect staged output with the runtime's fixed contract and clock."""
    contract = {
        StageName.VIDEO: runtime.contracts.downloader,
        StageName.SUBTITLES: runtime.contracts.subtitles,
        StageName.AUDIO: runtime.contracts.audio,
        StageName.PROBE: runtime.contracts.probe,
        StageName.FRAMES: runtime.contracts.frames,
    }[stage]
    return store.inspect_staged(stage, contract, runtime.completed_at)
