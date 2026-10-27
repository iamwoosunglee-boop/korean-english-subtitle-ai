# Copyright 2026 Transcription Module contributors

"""Semantic replay checks for named-turn visual verification evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module.visual_evidence_policy import SemanticRelation
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from transcription_module.visual_observations import VisualObservation
    from transcription_module.window_speaker_authority_models import (
        SpeakerTurnAuthorityRecord,
        SpeakerTurnReplayContext,
    )


def validate_speaker_observation(
    context: SpeakerTurnReplayContext,
    record: SpeakerTurnAuthorityRecord,
    observation: VisualObservation,
) -> None:
    """Require exact current frame/VAD identities and positive visual semantics."""
    frames = dict(context.frame_points)
    frame_ids = tuple(str(item) for item in observation.frame_ids)
    if (
        len(frames) != len(context.frame_points)
        or any(item not in frames for item in frame_ids)
        or tuple(frames[item] for item in frame_ids) != observation.frame_pts_ms
        or any(
            point < record.start_tc_ms or point >= record.end_tc_ms
            for point in observation.frame_pts_ms
        )
    ):
        fail_window_evidence("speaker-turn verification frames are foreign or stale")
    vad = {item_id: (start, end) for item_id, start, end in context.vad_intervals}
    if not observation.vad_interval_ids or any(
        item not in vad
        or vad[item][0] >= record.end_tc_ms
        or vad[item][1] <= record.start_tc_ms
        for item in observation.vad_interval_ids
    ):
        fail_window_evidence("speaker-turn verification VAD evidence is inexact")
    if (
        observation.semantic_relation is not SemanticRelation.EXACT
        or not observation.caption_regime_supported
        or not observation.temporal_alignment_supported
        or not observation.speech_presence_supported
        or observation.conflicts
        or observation.evidence_against
        or observation.missing_evidence
    ):
        fail_window_evidence("speaker-turn verification semantics are insufficient")


__all__ = ["validate_speaker_observation"]
