# Copyright (c) 2026
"""Typed missing-speech detection and large-v3 recovery decisions."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from transcription_module.models import FrozenModel, Milliseconds


class VadSpeechInterval(FrozenModel):
    """One speech interval reported by VAD."""

    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed VAD intervals."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "VAD interval end must follow start"
            raise ValueError(msg)
        return self


class CueCoverageInterval(FrozenModel):
    """One interval already covered by a transcript cue."""

    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed cue coverage."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "cue coverage end must follow start"
            raise ValueError(msg)
        return self


class MissingSpeechInterval(FrozenModel):
    """A VAD-backed interval not covered by any current cue."""

    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed missing-speech intervals."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "missing-speech interval end must follow start"
            raise ValueError(msg)
        return self


class RecoveryContext(FrozenModel):
    """Missing interval plus neighboring speech supplied to large-v3."""

    missing_interval: MissingSpeechInterval
    preceding_speech: str
    following_speech: str


class LargeV3Recovery(FrozenModel):
    """Structured result returned by the contextual large-v3 adapter."""

    speech: str
    confident: bool
    evidence: tuple[str, ...]


class RecoveredSpeech(FrozenModel):
    """A confident recovered utterance ready for automatic application."""

    interval: MissingSpeechInterval
    speech: str = Field(min_length=1)
    evidence: tuple[str, ...]


class UnresolvedSpeech(FrozenModel):
    """An uncertain recovery retained for user confirmation."""

    interval: MissingSpeechInterval
    current_value: str
    candidates: tuple[str, ...]
    evidence: tuple[str, ...]


type RecoveryDecision = RecoveredSpeech | UnresolvedSpeech


def find_missing_speech(
    vad_intervals: tuple[VadSpeechInterval, ...],
    cue_intervals: tuple[CueCoverageInterval, ...],
) -> tuple[MissingSpeechInterval, ...]:
    """Subtract cue coverage from VAD speech without product thresholds."""
    uncovered: list[MissingSpeechInterval] = []
    ordered_cues = sorted(cue_intervals, key=lambda interval: interval.start_tc_ms)
    for vad in vad_intervals:
        fragments = [(int(vad.start_tc_ms), int(vad.end_tc_ms))]
        for cue in ordered_cues:
            next_fragments: list[tuple[int, int]] = []
            for start_tc_ms, end_tc_ms in fragments:
                if cue.end_tc_ms <= start_tc_ms or cue.start_tc_ms >= end_tc_ms:
                    next_fragments.append((start_tc_ms, end_tc_ms))
                    continue
                if cue.start_tc_ms > start_tc_ms:
                    next_fragments.append((start_tc_ms, int(cue.start_tc_ms)))
                if cue.end_tc_ms < end_tc_ms:
                    next_fragments.append((int(cue.end_tc_ms), end_tc_ms))
            fragments = next_fragments
        uncovered.extend(
            MissingSpeechInterval(
                start_tc_ms=Milliseconds(start_tc_ms),
                end_tc_ms=Milliseconds(end_tc_ms),
            )
            for start_tc_ms, end_tc_ms in fragments
        )
    return tuple(uncovered)


def decide_large_v3_recovery(
    context: RecoveryContext,
    recovery: LargeV3Recovery,
) -> RecoveryDecision:
    """Auto-apply only a confident, non-empty contextual recovery."""
    speech = recovery.speech.strip()
    if recovery.confident and speech:
        return RecoveredSpeech(
            interval=context.missing_interval,
            speech=speech,
            evidence=recovery.evidence,
        )
    candidates = (speech,) if speech else ()
    return UnresolvedSpeech(
        interval=context.missing_interval,
        current_value="",
        candidates=candidates,
        evidence=recovery.evidence,
    )
