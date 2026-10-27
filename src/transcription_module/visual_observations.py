# Copyright 2026 Transcription Module contributors

"""Categorical visual observations bound to exact frames and policy bytes."""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import FrameId, FrozenModel, RunId
from transcription_module.visual_evidence_policy import (
    ConflictRule,
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]


class VisualObservation(FrozenModel):
    """One complete bounded classification with evidence for and against."""

    schema_version: Literal["visual-observation.v1"] = "visual-observation.v1"
    observation_id: NonBlank
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    role: VisualRole
    state: EvidenceState
    semantic_relation: SemanticRelation
    frame_ids: tuple[FrameId, ...] = Field(min_length=1)
    frame_pts_ms: tuple[NonNegative, ...] = ()
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    complete_text: str | None = None
    vad_interval_ids: tuple[NonBlank, ...] = ()
    turn_ids: tuple[NonBlank, ...] = ()
    caption_regime_supported: bool = False
    temporal_alignment_supported: bool = False
    speech_presence_supported: bool = False
    conflicts: tuple[ConflictRule, ...] = ()
    evidence_for: tuple[NonBlank, ...] = ()
    evidence_against: tuple[NonBlank, ...] = ()
    missing_evidence: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def observation_contract_is_consistent(self) -> Self:
        """Require unique frames, valid bounds, and new-era policy identity."""
        _validate_observation_interval(self)
        _validate_observation_identities(self)
        _validate_policy_bound_observation(self)
        _validate_observation_role_and_state(self)
        return self


def _validate_observation_interval(observation: VisualObservation) -> None:
    if observation.end_tc_ms <= observation.start_tc_ms:
        message = "visual observation end must follow start"
        raise ValueError(message)


def _validate_observation_identities(observation: VisualObservation) -> None:
    for values, label in (
        (observation.frame_ids, "frame IDs"),
        (observation.vad_interval_ids, "VAD interval IDs"),
        (observation.turn_ids, "turn IDs"),
        (observation.conflicts, "conflicts"),
    ):
        if len(values) != len(set(values)):
            message = f"visual observation {label} must be unique"
            raise ValueError(message)


def _validate_policy_bound_observation(observation: VisualObservation) -> None:
    if observation.era is EvidenceEra.LEGACY:
        return
    if observation.policy_digest is None:
        message = "policy-bound observation requires policy digest"
        raise ValueError(message)
    if len(observation.frame_pts_ms) != len(observation.frame_ids):
        message = "policy-bound frame PTS must match frame identities"
        raise ValueError(message)
    if any(
        current >= following
        for current, following in pairwise(observation.frame_pts_ms)
    ):
        message = "policy-bound frame PTS must be strictly increasing"
        raise ValueError(message)


def _validate_observation_role_and_state(observation: VisualObservation) -> None:
    if observation.role is VisualRole.SPEECH_SUPPORTING and not (
        observation.complete_text and observation.complete_text.strip()
    ):
        message = "speech-supporting observation requires complete text"
        raise ValueError(message)
    required = {
        EvidenceState.SUPPORTED: (
            observation.evidence_for,
            "supported observation requires positive evidence",
        ),
        EvidenceState.CONFLICTED: (
            observation.evidence_against,
            "conflicted observation requires evidence against",
        ),
        EvidenceState.INSUFFICIENT: (
            observation.missing_evidence,
            "insufficient observation requires missing evidence",
        ),
    }
    evidence, message = required[observation.state]
    if not evidence:
        raise ValueError(message)


class VisualObservationSet(FrozenModel):
    """All observations for one run under one evidence era and policy."""

    schema_version: Literal["visual-observation-set.v1"] = (
        "visual-observation-set.v1"
    )
    run_id: RunId
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    observations: tuple[VisualObservation, ...]

    @model_validator(mode="after")
    def observation_bindings_match_set(self) -> Self:
        """Reject duplicate IDs, mixed eras, and cross-policy observations."""
        observation_ids = tuple(item.observation_id for item in self.observations)
        if len(observation_ids) != len(set(observation_ids)):
            message = "visual observation IDs must be unique"
            raise ValueError(message)
        if any(item.era is not self.era for item in self.observations):
            message = "visual observation set cannot mix eras"
            raise ValueError(message)
        if self.era is EvidenceEra.POLICY_BOUND:
            if self.policy_digest is None:
                message = "policy-bound observation set requires policy digest"
                raise ValueError(message)
            if any(
                item.policy_digest != self.policy_digest
                for item in self.observations
            ):
                message = "visual observation has a foreign policy digest"
                raise ValueError(message)
        return self


__all__ = ["VisualObservation", "VisualObservationSet"]
