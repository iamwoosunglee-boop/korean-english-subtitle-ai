# Copyright 2026 Transcription Module contributors

"""Typed contracts for semantic final-review execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TC003  # Pydantic resolves fields at runtime
from typing import TYPE_CHECKING, Protocol, override

from transcription_module.asr import AsrSegment  # noqa: TC001
from transcription_module.final_review_models import (  # noqa: TC001
    ReviewSegmentAssignment,
    ReviewSegmentResult,
    Sha256Digest,
)
from transcription_module.media import (  # noqa: TC001
    PreparedFrameManifest,
    SubtitleSource,
)
from transcription_module.models import Cue, FrozenModel, RunId

if TYPE_CHECKING:
    from collections.abc import Awaitable


@dataclass(slots=True)
class FinalReviewExecutionError(Exception):
    """A reviewer execution input or output is incomplete or unsafe."""

    message: str

    @override
    def __str__(self) -> str:
        return self.message


class KoreanVttEvidence(FrozenModel):
    """One confined stored Korean VTT supplied without source media paths."""

    path: str
    language: str
    source: SubtitleSource
    contents: str


class FinalReviewSegmentInput(FrozenModel):
    """Complete stored semantic evidence for one assigned review segment."""

    run_id: RunId
    run_dir: Path
    assignment_digest: Sha256Digest
    segment: ReviewSegmentAssignment
    purpose: str
    failure_context: str
    forbidden_proxy_metrics: tuple[str, ...]
    required_evidence_actions: str
    observable_stop_condition: str
    frames: tuple[PreparedFrameManifest, ...]
    cues: tuple[Cue, ...]
    asr_segments: tuple[AsrSegment, ...]
    korean_vtt_evidence: tuple[KoreanVttEvidence, ...]


class FinalReviewSegmentReviewer(Protocol):
    """Asynchronous reviewer boundary independent of any provider SDK."""

    def __call__(
        self,
        review_input: FinalReviewSegmentInput,
        /,
    ) -> Awaitable[ReviewSegmentResult]:
        """Review one segment and return its complete typed result."""
        ...


__all__ = [
    "FinalReviewExecutionError",
    "FinalReviewSegmentInput",
    "FinalReviewSegmentReviewer",
    "KoreanVttEvidence",
]
