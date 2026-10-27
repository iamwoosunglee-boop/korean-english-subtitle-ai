# Copyright 2026 Transcription Module contributors

"""Deterministic eligibility routing for bounded visual corrections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._visual_correction_operations import (
    preserved_correction,
    unique_reasons,
    validate_insertion,
    validate_replacement,
)
from transcription_module.visual_correction_models import (
    CandidateOrigin,
    CorrectionValidationError,
    TextSlice,
    ValidatedCorrection,
    VisualCorrectionRequest,
)
from transcription_module.visual_evidence_policy import (
    CorrectionAction,
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualEvidencePolicy,
    VisualRole,
    default_evidence_policy,
    policy_digest,
)

if TYPE_CHECKING:
    from transcription_module.visual_observations import VisualObservation


def validate_visual_correction(
    request: VisualCorrectionRequest,
    policy: VisualEvidencePolicy | None = None,
) -> ValidatedCorrection:
    """Authorize an exact bounded edit or preserve source through KEEP/HOLD."""
    active = default_evidence_policy() if policy is None else policy
    _require_current_policy(request, active)
    preserved = _preflight_preservation(request)
    if preserved is not None:
        return preserved
    if request.requested_action is CorrectionAction.REPLACE_SPAN:
        return validate_replacement(request)
    if request.requested_action is CorrectionAction.INSERT_CUE:
        return validate_insertion(
            request,
            active.insertion.minimum_distinct_pts,
        )
    message = "visual correction action has no deterministic validator"
    raise CorrectionValidationError(message)


def _preflight_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    checks = (
        _requested_preservation,
        _confirmed_field_preservation,
        _state_preservation,
        _role_preservation,
        _semantic_preservation,
        _missing_evidence_preservation,
    )
    for check in checks:
        if (outcome := check(request)) is not None:
            return outcome
    return None


def _requested_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    outcomes = {
        CorrectionAction.KEEP: (CorrectionAction.KEEP, ("requested_keep",)),
        CorrectionAction.HOLD_FOR_CONFIRMATION: (
            CorrectionAction.HOLD_FOR_CONFIRMATION,
            ("requested_hold",),
        ),
    }
    outcome = outcomes.get(request.requested_action)
    return None if outcome is None else preserved_correction(request, *outcome)


def _confirmed_field_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    if not request.confirmed_field:
        return None
    return preserved_correction(
        request,
        CorrectionAction.HOLD_FOR_CONFIRMATION,
        ("confirmed_field_conflict",),
    )


def _state_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    observation = request.observation
    if observation.state is EvidenceState.SUPPORTED:
        return None
    return preserved_correction(
        request,
        CorrectionAction.HOLD_FOR_CONFIRMATION,
        _observation_reasons(observation, "observation_not_supported"),
    )


def _role_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    role = request.observation.role
    if role is VisualRole.SPEECH_SUPPORTING:
        return None
    outcomes = {
        VisualRole.UNKNOWN: (
            CorrectionAction.HOLD_FOR_CONFIRMATION,
            ("unknown_visual_role",),
        ),
        VisualRole.NON_SPEECH: (
            CorrectionAction.KEEP,
            ("non_speech_preserve_source",),
        ),
    }
    action, reasons = outcomes[role]
    return preserved_correction(request, action, reasons)


def _semantic_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    relation = request.observation.semantic_relation
    if relation is SemanticRelation.EXACT:
        return None
    return preserved_correction(
        request,
        CorrectionAction.KEEP,
        (f"{relation.value}_preserve_source",),
    )


def _missing_evidence_preservation(
    request: VisualCorrectionRequest,
) -> ValidatedCorrection | None:
    missing = _missing_mutation_evidence(request)
    if not missing:
        return None
    return preserved_correction(
        request,
        CorrectionAction.HOLD_FOR_CONFIRMATION,
        missing,
    )


def _require_current_policy(
    request: VisualCorrectionRequest,
    policy: VisualEvidencePolicy,
) -> None:
    expected = policy_digest(policy)
    observation = request.observation
    if request.policy_digest != expected:
        message = "visual correction policy digest is stale"
        raise CorrectionValidationError(message)
    if (
        observation.era is not EvidenceEra.POLICY_BOUND
        or observation.policy_digest != expected
    ):
        message = "visual observation is not bound to the current policy"
        raise CorrectionValidationError(message)
    if request.requested_action not in policy.actions:
        message = "visual correction action is not allowed by policy"
        raise CorrectionValidationError(message)


def _missing_mutation_evidence(
    request: VisualCorrectionRequest,
) -> tuple[str, ...]:
    observation = request.observation
    checks = (
        (observation.caption_regime_supported, "caption_regime_missing"),
        (observation.temporal_alignment_supported, "temporal_alignment_missing"),
        (observation.speech_presence_supported, "speech_presence_missing"),
        (bool(observation.vad_interval_ids), "vad_binding_missing"),
        (not observation.conflicts, "visual_conflict_present"),
        (
            bool(observation.complete_text and observation.complete_text.strip()),
            "complete_candidate_missing",
        ),
    )
    reasons = tuple(reason for valid, reason in checks if not valid)
    conflicts = tuple(conflict.value for conflict in observation.conflicts)
    return unique_reasons((*reasons, *conflicts))


def _observation_reasons(
    observation: VisualObservation,
    fallback: str,
) -> tuple[str, ...]:
    return unique_reasons(
        (
            *(conflict.value for conflict in observation.conflicts),
            *observation.evidence_against,
            *observation.missing_evidence,
            fallback,
        )
    )


__all__ = [
    "CandidateOrigin",
    "CorrectionValidationError",
    "TextSlice",
    "ValidatedCorrection",
    "VisualCorrectionRequest",
    "validate_visual_correction",
]
