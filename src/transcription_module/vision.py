# Copyright (c) 2026
"""Vision evidence contracts, fallback routing, and cue alignment policy."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from transcription_module.models import (
    Cue,
    FrameId,
    FrozenModel,
    Milliseconds,
    SubtitleTrust,
)

if TYPE_CHECKING:
    from transcription_module.timing import SubtitleInterval


class VisionContractError(Exception):
    """Vision evidence does not cover the required timeline or cue set."""


class VisionResolution(StrEnum):
    """Whether the current visual pass settled the requested evidence."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class VisionAuditRequest(FrozenModel):
    """One bounded audit request containing only manifest-backed frame IDs."""

    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)
    frame_ids: tuple[FrameId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def end_follows_start(self) -> VisionAuditRequest:
        """Reject empty or reversed requests."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "vision audit end must follow start"
            raise ValueError(msg)
        return self


class VisionFinding(FrozenModel):
    """Scene and rough lower-third evidence returned in the same pass."""

    scene: str = Field(min_length=1)
    rough_lower_third: str | None
    resolution: VisionResolution
    confidence: float = Field(ge=0, le=1)
    first_visible_frame_id: FrameId | None = None
    first_not_visible_frame_id: FrameId | None = None


class VisionRouteResult(FrozenModel):
    """Final visual finding plus the exact resolver route used."""

    finding: VisionFinding
    route: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScreenEvidence:
    """Lower-third text, timing, trust, and alignment preconditions."""

    screen_text: str
    interval: SubtitleInterval
    estimated_trust: SubtitleTrust
    meaning_matches: bool
    audio_boundary_uncertain: bool
    trust_override: SubtitleTrust | None = None


class AuditSegment(FrozenModel):
    """Continuous timeline ownership with read-only neighboring cue context."""

    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)
    owned_cue_sequences: tuple[int, ...]
    context_cue_sequences: tuple[int, ...] = ()

    @model_validator(mode="after")
    def end_follows_start(self) -> AuditSegment:
        """Reject empty or reversed audit segments."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "audit segment end must follow start"
            raise ValueError(msg)
        return self


Resolver = Callable[[VisionAuditRequest], VisionFinding]


def resolve_vision(
    request: VisionAuditRequest,
    *,
    sol: Resolver,
    terra: Resolver,
    ocr: Resolver,
) -> VisionRouteResult:
    """Run Sol, then Terra, then OCR only while evidence stays unresolved."""
    sol_finding = sol(request)
    match sol_finding.resolution:
        case VisionResolution.RESOLVED:
            return VisionRouteResult(finding=sol_finding, route=("sol",))
        case VisionResolution.UNRESOLVED:
            pass

    terra_finding = terra(request)
    match terra_finding.resolution:
        case VisionResolution.RESOLVED:
            return VisionRouteResult(
                finding=terra_finding,
                route=("sol", "terra"),
            )
        case VisionResolution.UNRESOLVED:
            pass

    return VisionRouteResult(
        finding=ocr(request),
        route=("sol", "terra", "ocr"),
    )


def assert_full_vision_coverage(
    segments: Sequence[AuditSegment],
    *,
    duration_ms: Milliseconds,
    cues: Sequence[Cue],
) -> None:
    """Require gapless full-duration coverage and one owner per final cue."""
    if not segments or segments[0].start_tc_ms != 0:
        msg = "vision audit must start at zero"
        raise VisionContractError(msg)
    for current, following in pairwise(segments):
        if current.end_tc_ms != following.start_tc_ms:
            msg = "vision audit segments must cover the timeline without gaps"
            raise VisionContractError(msg)
    if segments[-1].end_tc_ms != duration_ms:
        msg = "vision audit must end at source duration"
        raise VisionContractError(msg)

    ownership = Counter(
        sequence
        for segment in segments
        for sequence in segment.owned_cue_sequences
    )
    expected = {cue.sequence for cue in cues}
    if set(ownership) != expected or any(count != 1 for count in ownership.values()):
        msg = "every final cue must be owned exactly once"
        raise VisionContractError(msg)


def apply_screen_evidence(
    cue: Cue,
    evidence: ScreenEvidence,
) -> Cue:
    """Preserve speech while applying trust-gated screen timing and text."""
    effective_trust = evidence.trust_override or evidence.estimated_trust
    start_tc_ms = cue.start_tc_ms
    end_tc_ms = cue.end_tc_ms
    match effective_trust:
        case SubtitleTrust.LOW | SubtitleTrust.MEDIUM:
            pass
        case SubtitleTrust.HIGH:
            if evidence.meaning_matches and evidence.audio_boundary_uncertain:
                start_tc_ms = evidence.interval.start_tc_ms
                end_tc_ms = evidence.interval.end_tc_ms

    return Cue(
        sequence=cue.sequence,
        cue_id=cue.cue_id,
        start_tc_ms=start_tc_ms,
        end_tc_ms=end_tc_ms,
        speech=cue.speech,
        speaker=cue.speaker,
        uncertainty=cue.uncertainty,
        screen_text=evidence.screen_text,
        vision_evidence_ids=cue.vision_evidence_ids,
    )
