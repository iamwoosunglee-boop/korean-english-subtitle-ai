# Copyright 2026 Transcription Module contributors

"""Typed local authorities and per-cue window coverage records."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import FrozenModel, RunId
from transcription_module.visual_correction_models import (  # noqa: TC001
    CandidateOrigin,
)
from transcription_module.visual_evidence_policy import (  # noqa: TC001
    CorrectionAction,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
PositiveOrdinal = Annotated[int, Field(ge=1)]


class WindowCueResolution(StrEnum):
    """Whether one covered cue is authorized or explicitly unresolved."""

    AUTHORIZED = "authorized"
    UNRESOLVED = "unresolved"


class WindowSubtitleCue(FrozenModel):
    """One exact admitted subtitle cue with stable local timing identity."""

    schema_version: Literal["window-subtitle-cue.v1"] = "window-subtitle-cue.v1"
    cue_id: NonBlank
    track_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty and reversed subtitle intervals."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "window subtitle cue end must follow start"
            raise ValueError(message)
        return self


class WindowVadInterval(FrozenModel):
    """One deterministic local reference to stored speech presence."""

    schema_version: Literal["window-vad-interval.v1"] = "window-vad-interval.v1"
    interval_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty and reversed VAD intervals."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "window VAD interval end must follow start"
            raise ValueError(message)
        return self


class ProvisionalVisualInsertion(FrozenModel):
    """One audio-bounded visual candidate outside exportable cues."""

    schema_version: Literal["provisional-visual-insertion.v1"] = (
        "provisional-visual-insertion.v1"
    )
    provisional_id: NonBlank
    candidate_id: NonBlank
    observation_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    speech: NonBlank
    speaker: NonBlank | None = None
    vad_interval_ids: tuple[NonBlank, ...] = Field(min_length=1)
    turn_id: NonBlank | None = None
    disposition_evidence_ids: tuple[NonBlank, ...] = Field(min_length=1)
    exportable: Literal[False] = False

    @model_validator(mode="after")
    def evidence_is_bounded_and_unique(self) -> Self:
        """Reject invalid timing or repeated evidence identities."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "provisional visual insertion end must follow start"
            raise ValueError(message)
        if (self.speaker is None) != (self.turn_id is None):
            message = (
                "provisional visual insertion speaker and verified turn "
                "must be present together"
            )
            raise ValueError(message)
        for label, values in (
            ("VAD", self.vad_interval_ids),
            ("disposition evidence", self.disposition_evidence_ids),
        ):
            if len(values) != len(set(values)):
                message = f"provisional visual insertion {label} IDs must be unique"
                raise ValueError(message)
        return self


class LegacyWindowVerifiedTurn(FrozenModel):
    """Parse-only v1 turn assertion; never accepted as policy authority."""

    schema_version: Literal["window-verified-turn.v1"] = "window-verified-turn.v1"
    turn_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    cluster: NonBlank
    speaker: NonBlank
    verification_id: NonBlank
    verified: Literal[True]

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Keep historical artifacts structurally readable."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "legacy window verified turn end must follow start"
            raise ValueError(message)
        return self


class LegacyVerifiedSpeakerTurnArtifact(FrozenModel):
    """Parse-only self-asserted v1 sidecar with zero policy authority."""

    schema_version: Literal["verified-speaker-turns.v1"] = "verified-speaker-turns.v1"
    run_id: RunId
    policy_digest: Sha256Digest
    audio_sha256: Sha256Digest
    diarization_digest: Sha256Digest
    turns: tuple[LegacyWindowVerifiedTurn, ...] = Field(min_length=1)


class WindowVerifiedTurn(FrozenModel):
    """One turn replayed from independently admitted speaker authority."""

    schema_version: Literal["window-verified-turn.v2"] = "window-verified-turn.v2"
    turn_id: NonBlank
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    cluster: NonBlank
    speaker: NonBlank
    authority_digest: Sha256Digest
    verification_evidence_ids: tuple[NonBlank, ...] = Field(min_length=1)
    verification_decision_ids: tuple[NonBlank, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty, reversed, or duplicate proof references."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "window verified turn end must follow start"
            raise ValueError(message)
        for values in (
            self.verification_evidence_ids,
            self.verification_decision_ids,
        ):
            if len(values) != len(set(values)):
                message = "window verified turn proof references must be unique"
                raise ValueError(message)
        return self


class VerifiedSpeakerTurnArtifact(FrozenModel):
    """Derived turns replayed from one admitted independent authority file."""

    schema_version: Literal["verified-speaker-turns.v2"] = "verified-speaker-turns.v2"
    run_id: RunId
    policy_digest: Sha256Digest
    source_digest: Sha256Digest
    authority_path: Literal["speaker-turn-authority.json"]
    authority_sha256: Sha256Digest
    turns: tuple[WindowVerifiedTurn, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def verified_turn_identities_are_complete(self) -> Self:
        """Require one turn and one admitted authority payload per identity."""
        turn_ids = tuple(item.turn_id for item in self.turns)
        authority_digests = tuple(item.authority_digest for item in self.turns)
        if len(turn_ids) != len(set(turn_ids)) or len(authority_digests) != len(
            set(authority_digests)
        ):
            message = "verified speaker turn authority must be unique"
            raise ValueError(message)
        return self


class WindowCueEvidence(FrozenModel):
    """One output cue allocated to an exact authority bundle."""

    schema_version: Literal["window-cue-evidence.v1"] = "window-cue-evidence.v1"
    cue_index: NonNegative
    cue_identity: NonBlank
    resolution: WindowCueResolution
    start_tc_ms: NonNegative
    end_tc_ms: Positive
    speech_digest: Sha256Digest
    speaker: str | None = None
    source_evidence_ids: tuple[NonBlank, ...] = Field(min_length=1)
    asr_ordinals: tuple[PositiveOrdinal, ...] = ()
    subtitle_track_ids: tuple[NonBlank, ...] = ()
    subtitle_cue_ids: tuple[NonBlank, ...] = ()
    observation_ids: tuple[NonBlank, ...] = ()
    vad_interval_ids: tuple[NonBlank, ...] = ()
    turn_ids: tuple[NonBlank, ...] = ()
    requested_action: CorrectionAction
    candidate_origin: CandidateOrigin | None = None
    confirmed_field: bool = False
    correction_receipt_id: NonBlank

    @model_validator(mode="after")
    def evidence_is_ordered_and_unique(self) -> Self:
        """Reject invalid timing and repeated authority identities."""
        if self.end_tc_ms <= self.start_tc_ms:
            message = "window cue evidence end must follow start"
            raise ValueError(message)
        for values, label in (
            (self.source_evidence_ids, "source evidence IDs"),
            (self.asr_ordinals, "ASR ordinals"),
            (self.subtitle_track_ids, "subtitle track IDs"),
            (self.subtitle_cue_ids, "subtitle cue IDs"),
            (self.observation_ids, "observation IDs"),
            (self.vad_interval_ids, "VAD interval IDs"),
            (self.turn_ids, "turn IDs"),
        ):
            if len(values) != len(set(values)):
                message = f"window cue evidence {label} must be unique"
                raise ValueError(message)
        if self.subtitle_cue_ids and not self.subtitle_track_ids:
            message = "window subtitle cue references require a track identity"
            raise ValueError(message)
        return self


__all__ = [
    "LegacyVerifiedSpeakerTurnArtifact",
    "LegacyWindowVerifiedTurn",
    "ProvisionalVisualInsertion",
    "VerifiedSpeakerTurnArtifact",
    "WindowCueEvidence",
    "WindowCueResolution",
    "WindowSubtitleCue",
    "WindowVadInterval",
    "WindowVerifiedTurn",
]
