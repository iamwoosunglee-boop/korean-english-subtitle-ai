# Copyright 2026 Transcription Module contributors

"""Internal evidence snapshot shared by bounded window input construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcription_module.visual_evidence_policy import EvidenceEra

if TYPE_CHECKING:
    from transcription_module._drafting_models import SpeakerEvidenceSegment
    from transcription_module.asr import AsrSegment
    from transcription_module.media import PreparedMediaManifest, PreparedSubtitleTrack
    from transcription_module.models import EpisodeContext
    from transcription_module.window_evidence_models import (
        WindowSubtitleCue,
        WindowVadInterval,
        WindowVerifiedTurn,
    )


@dataclass(frozen=True, slots=True)
class WindowEvidenceSnapshot:
    """One validated run-level source snapshot before overlap filtering."""

    media: PreparedMediaManifest
    asr_segments: tuple[AsrSegment, ...]
    subtitle_tracks: tuple[PreparedSubtitleTrack, ...]
    speaker_segments: tuple[SpeakerEvidenceSegment, ...]
    vision_artifact_path: str | None
    episode_context: EpisodeContext | None = None
    era: EvidenceEra = EvidenceEra.LEGACY
    policy_digest: str | None = None
    inventory_digest: str | None = None
    source_digest: str | None = None
    subtitle_cues: tuple[WindowSubtitleCue, ...] = ()
    vad_intervals: tuple[WindowVadInterval, ...] = ()
    verified_turns: tuple[WindowVerifiedTurn, ...] = ()


__all__ = ["WindowEvidenceSnapshot"]
