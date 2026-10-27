# Copyright 2026 Transcription Module contributors

"""Duplicate-safe source maps for one complete policy window."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from transcription_module._drafting_models import WindowDraft, WindowDraftInput
    from transcription_module.asr import AsrSegment
    from transcription_module.correction_receipts import CorrectionReceipt
    from transcription_module.media import PreparedFrameManifest, PreparedSubtitleTrack
    from transcription_module.visual_observations import VisualObservation
    from transcription_module.window_evidence_models import (
        WindowSubtitleCue,
        WindowVadInterval,
        WindowVerifiedTurn,
    )


@dataclass(frozen=True, slots=True)
class WindowAuthority:
    """Every exact input and output identity available in one window."""

    window_input: WindowDraftInput
    asr: Mapping[int, AsrSegment]
    tracks: Mapping[str, PreparedSubtitleTrack]
    subtitles: Mapping[str, WindowSubtitleCue]
    vad: Mapping[str, WindowVadInterval]
    turns: Mapping[str, WindowVerifiedTurn]
    frames: Mapping[str, PreparedFrameManifest]
    observations: Mapping[str, VisualObservation]
    receipts: Mapping[str, CorrectionReceipt]


def build_window_authority(
    window_input: WindowDraftInput,
    draft: WindowDraft,
) -> WindowAuthority:
    """Index all identities without allowing duplicate-map collapse."""
    asr_items = tuple(
        item for item in window_input.asr_segments if item.source_ordinal is not None
    )
    if len(asr_items) != len(window_input.asr_segments):
        fail_window_evidence("policy window ASR requires stable identities")
    track_items = tuple(
        item for item in window_input.subtitle_tracks if item.track_id is not None
    )
    if len(track_items) != len(window_input.subtitle_tracks):
        fail_window_evidence("policy window subtitle tracks require identities")
    turns = _unique_map(
        window_input.verified_turns,
        lambda item: item.turn_id,
        "turn identities",
    )
    _validate_verified_turns(turns)
    return WindowAuthority(
        window_input=window_input,
        asr=_unique_map(
            asr_items,
            lambda item: _required_ordinal(item.source_ordinal),
            "ASR identities",
        ),
        tracks=_unique_map(
            track_items,
            lambda item: _required_text(item.track_id),
            "subtitle track identities",
        ),
        subtitles=_unique_map(
            window_input.subtitle_cues,
            lambda item: item.cue_id,
            "subtitle cue identities",
        ),
        vad=_unique_map(
            window_input.vad_intervals,
            lambda item: item.interval_id,
            "VAD identities",
        ),
        turns=turns,
        frames=_unique_map(
            window_input.frames,
            lambda item: str(item.frame_id),
            "frame identities",
        ),
        observations=_unique_map(
            draft.observations,
            lambda item: item.observation_id,
            "observation identities",
        ),
        receipts=_unique_map(
            draft.corrections,
            lambda item: item.receipt_id,
            "correction receipt identities",
        ),
    )


def core_owned_asr(authority: WindowAuthority) -> tuple[AsrSegment, ...]:
    """Return ASR output authority for this half-open core in source order."""
    data = authority.window_input
    return tuple(
        item
        for item in data.asr_segments
        if data.core_start_ms <= item.start_ms < data.core_end_ms
    )


def render_asr_source(
    authority: WindowAuthority,
    ordinals: tuple[int, ...],
) -> str:
    """Render one validated ASR claim with the canonical U+0020 separator."""
    return " ".join(authority.asr[ordinal].speech for ordinal in ordinals)


def is_grouped_asr_claim(
    authority: WindowAuthority,
    action: CorrectionAction,
    ordinals: tuple[int, ...],
) -> bool:
    """Classify grouped actions, excluding only legacy identical-source HOLD."""
    if not ordinals[1:]:
        return False
    if action in {CorrectionAction.KEEP, CorrectionAction.REPLACE_SPAN}:
        return True
    if action is not CorrectionAction.HOLD_FOR_CONFIRMATION:
        return False
    identities = {
        (
            authority.asr[ordinal].start_ms,
            authority.asr[ordinal].end_ms,
            authority.asr[ordinal].speech,
        )
        for ordinal in ordinals
    }
    return len(identities) > 1


def _validate_verified_turns(
    turns: Mapping[str, WindowVerifiedTurn],
) -> None:
    authority_digests = tuple(item.authority_digest for item in turns.values())
    evidence_ids = tuple(
        value for item in turns.values() for value in item.verification_evidence_ids
    )
    decision_ids = tuple(
        value for item in turns.values() for value in item.verification_decision_ids
    )
    if (
        len(authority_digests) != len(set(authority_digests))
        or len(evidence_ids) != len(set(evidence_ids))
        or len(decision_ids) != len(set(decision_ids))
    ):
        fail_window_evidence("window verified turn authority must be distinct")


def _unique_map[T, K: Hashable](
    items: tuple[T, ...],
    key: Callable[[T], K],
    label: str,
) -> Mapping[K, T]:
    mapped = {key(item): item for item in items}
    if len(mapped) != len(items):
        fail_window_evidence(f"window {label} must be unique")
    return MappingProxyType(mapped)


def _required_ordinal(value: int | None) -> int:
    if value is None:
        fail_window_evidence("policy window ASR requires stable identities")
    return value


def _required_text(value: str | None) -> str:
    if value is None:
        fail_window_evidence("policy window source identity is missing")
    return value


__all__ = [
    "WindowAuthority",
    "build_window_authority",
    "core_owned_asr",
    "is_grouped_asr_claim",
    "render_asr_source",
]
