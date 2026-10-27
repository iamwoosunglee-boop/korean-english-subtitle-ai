# Copyright 2026 Transcription Module contributors

"""Typed inputs and outcomes for deterministic visual corrections."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import FrozenModel
from transcription_module.visual_evidence_policy import (  # noqa: TC001
    CorrectionAction,
)
from transcription_module.visual_observations import (  # noqa: TC001
    VisualObservation,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CorrectionValidationError(ValueError):
    """A proposed correction is malformed, stale, or not candidate-derived."""


class CandidateOrigin(StrEnum):
    """The only candidate classes that may reach correction validation."""

    KO_ORIG = "ko_orig"
    VISUAL_ONLY = "visual_only"


class TextSlice(FrozenModel):
    """One non-empty half-open character range in an exact text value."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty and reversed character ranges."""
        if self.end <= self.start:
            message = "text slice end must follow start"
            raise ValueError(message)
        return self


class VisualCorrectionRequest(FrozenModel):
    """One requested action and every exact input needed to reproduce it."""

    policy_digest: Sha256Digest
    requested_action: CorrectionAction
    observation: VisualObservation
    candidate_origin: CandidateOrigin | None = None
    before_text: str
    submitted_result: str
    source_slice: TextSlice | None = None
    candidate_slice: TextSlice | None = None
    speaker: str | None = None
    confirmed_field: bool = False


class ValidatedCorrection(FrozenModel):
    """One deterministic correction outcome with no hidden generated text."""

    action: CorrectionAction
    before_text: str
    result_text: str
    candidate_text: str | None = None
    observation_id: NonBlank
    reason_codes: tuple[NonBlank, ...] = ()


__all__ = [
    "CandidateOrigin",
    "CorrectionValidationError",
    "TextSlice",
    "ValidatedCorrection",
    "VisualCorrectionRequest",
]
