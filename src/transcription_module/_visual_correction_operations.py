# Copyright 2026 Transcription Module contributors

"""Exact character operations for validated visual correction candidates."""

from __future__ import annotations

import unicodedata

from transcription_module.visual_correction_models import (
    CandidateOrigin,
    CorrectionValidationError,
    TextSlice,
    ValidatedCorrection,
    VisualCorrectionRequest,
)
from transcription_module.visual_evidence_policy import CorrectionAction


def validate_replacement(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection:
    """Reconstruct one replacement only from exact source/candidate slices."""
    candidate = request.observation.complete_text or ""
    if request.candidate_origin not in {
        CandidateOrigin.KO_ORIG,
        CandidateOrigin.VISUAL_ONLY,
    }:
        return preserved_correction(
            request,
            CorrectionAction.HOLD_FOR_CONFIRMATION,
            ("replacement_candidate_origin_not_admitted",),
        )
    if request.source_slice is None or request.candidate_slice is None:
        message = "replacement requires source and candidate slices"
        raise CorrectionValidationError(message)
    _ = _exact_slice(request.before_text, request.source_slice, "source")
    candidate_part = _exact_slice(candidate, request.candidate_slice, "candidate")
    expected = (
        request.before_text[: request.source_slice.start]
        + candidate_part
        + request.before_text[request.source_slice.end :]
    )
    if request.submitted_result != expected:
        message = "replacement result is not derived from the exact candidate slice"
        raise CorrectionValidationError(message)
    return ValidatedCorrection(
        action=CorrectionAction.REPLACE_SPAN,
        before_text=request.before_text,
        result_text=expected,
        candidate_text=candidate,
        observation_id=request.observation.observation_id,
        reason_codes=("candidate_span_exact",),
    )


def validate_insertion(
    request: VisualCorrectionRequest,
    minimum_distinct_pts: int,
) -> ValidatedCorrection:
    """Apply only NFC, line joining, and whitespace collapse to visual text."""
    observation = request.observation
    if request.candidate_origin is not CandidateOrigin.VISUAL_ONLY:
        return preserved_correction(
            request,
            CorrectionAction.HOLD_FOR_CONFIRMATION,
            ("insertion_candidate_origin_not_admitted",),
        )
    if request.before_text:
        message = "visual-only insertion requires an empty source span"
        raise CorrectionValidationError(message)
    if len(observation.frame_pts_ms) < minimum_distinct_pts:
        return preserved_correction(
            request,
            CorrectionAction.HOLD_FOR_CONFIRMATION,
            ("two_distinct_pts_missing",),
        )
    candidate = normalize_visual_candidate(observation.complete_text or "")
    if request.submitted_result != candidate:
        message = "insertion result contains text outside the normalized candidate"
        raise CorrectionValidationError(message)
    return ValidatedCorrection(
        action=CorrectionAction.INSERT_CUE,
        before_text="",
        result_text=candidate,
        candidate_text=candidate,
        observation_id=observation.observation_id,
        reason_codes=("two_pts_vad_single_turn",),
    )


def preserved_correction(
    request: VisualCorrectionRequest,
    action: CorrectionAction,
    reasons: tuple[str, ...],
) -> ValidatedCorrection:
    """Return KEEP or HOLD while preserving exact prior speech bytes."""
    return ValidatedCorrection(
        action=action,
        before_text=request.before_text,
        result_text=request.before_text,
        observation_id=request.observation.observation_id,
        reason_codes=unique_reasons(reasons),
    )


def normalize_visual_candidate(text: str) -> str:
    """Apply the complete fixed normalization allowlist."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def unique_reasons(values: tuple[str, ...]) -> tuple[str, ...]:
    """Deduplicate nonblank stable reason codes without reordering."""
    return tuple(dict.fromkeys(value for value in values if value.strip()))


def _exact_slice(text: str, selected: TextSlice, label: str) -> str:
    if selected.end > len(text):
        message = f"{label} slice exceeds exact candidate bounds"
        raise CorrectionValidationError(message)
    return text[selected.start : selected.end]


__all__ = [
    "normalize_visual_candidate",
    "preserved_correction",
    "unique_reasons",
    "validate_insertion",
    "validate_replacement",
]
