# Copyright 2026 Transcription Module contributors

"""Typed domain contracts for transcription runs."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, NewType, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

RunId = NewType("RunId", str)
CueId = NewType("CueId", str)
YouTubeVideoUrl = NewType("YouTubeVideoUrl", str)
EvidenceId = NewType("EvidenceId", str)
FrameId = NewType("FrameId", str)
Milliseconds = NewType("Milliseconds", int)

NonNegativeMilliseconds = Annotated[Milliseconds, Field(ge=0)]
PositiveMilliseconds = Annotated[Milliseconds, Field(gt=0)]
SequenceNumber = Annotated[int, Field(ge=1)]
ContextDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PersonId = Annotated[
    str,
    Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"),
]


class WorkflowStatus(StrEnum):
    """User-visible lifecycle of one transcription run."""

    WAITING_CONTEXT = "waiting_context"
    PROCESSING = "processing"
    WAITING_CONFIRMATION_CONSENT = "waiting_confirmation_consent"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CONFIRMATION_DECLINED = "confirmation_declined"
    READY_FOR_VALIDATION = "ready_for_validation"
    FINALIZED = "finalized"
    FAILED = "failed"


class ValidationStatus(StrEnum):
    """Independent validity state of generated files."""

    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class ValidationFindingCode(StrEnum):
    """Stable machine codes for source-quality findings."""

    PATHOLOGICAL_REPETITION = "pathological_repetition"
    UNREALISTIC_DURATION = "unrealistic_duration"
    ABNORMAL_WHITESPACE = "abnormal_whitespace"
    UNRESOLVED_SPEECH = "unresolved_speech"
    INVALID_SPEAKER = "invalid_speaker"
    SAME_SPEAKER_DUPLICATE = "same_speaker_duplicate"

    @property
    def blocks_source_finalization(self) -> bool:
        """Return whether this finding makes source publication unsafe."""
        return self in {
            self.UNRESOLVED_SPEECH,
            self.INVALID_SPEAKER,
            self.SAME_SPEAKER_DUPLICATE,
        }


class Uncertainty(StrEnum):
    """Whether a cue still needs a user decision."""

    CERTAIN = "certain"
    NEEDS_CONFIRMATION = "needs_confirmation"


class SubtitleTrust(StrEnum):
    """Episode-level trust assigned to visible lower-third subtitles."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FrozenModel(BaseModel):
    """Base model for immutable boundary data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ValidationFinding(FrozenModel):
    """One machine-addressable source finding with its human issue text."""

    code: ValidationFindingCode
    cue_ids: tuple[CueId, ...]
    issue: str


class ValidationRunArtifact(FrozenModel):
    """Persisted structure and content-quality validation evidence."""

    run_id: RunId
    validation_status: ValidationStatus
    transcript_path: str
    csv_path: str
    cue_count: int
    structure_issues: tuple[str, ...] = ()
    quality_issues: tuple[str, ...] = ()
    findings: tuple[ValidationFinding, ...] = ()


class ContextPerson(FrozenModel):
    """One context-grounded candidate identity without turn authority."""

    person_id: PersonId
    display_name: str
    appearance_hints: tuple[str, ...] = Field(min_length=1)

    @field_validator("display_name")
    @classmethod
    def display_name_is_exact(cls, value: str) -> str:
        """Reject blank or padded names rather than normalizing identity."""
        if not value or value != value.strip():
            message = "display_name must not be blank or have surrounding whitespace"
            raise ValueError(message)
        return value

    @field_validator("appearance_hints")
    @classmethod
    def appearance_hints_are_exact(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Reject blank, padded, or repeated hints and normalize their order."""
        if any(not hint or hint != hint.strip() for hint in value):
            message = (
                "appearance hints must not be blank or have surrounding whitespace"
            )
            raise ValueError(message)
        if len(value) != len(set(value)):
            message = "appearance hints must be unique for each roster person"
            raise ValueError(message)
        return tuple(sorted(value))


class EpisodeContext(FrozenModel):
    """Raw context plus an optional versioned candidate roster."""

    schema_version: Literal[
        "episode-context.v1",
        "episode-context.v2",
    ] = "episode-context.v1"
    raw_text: str = Field(min_length=1)
    characters: tuple[str, ...] = ()
    subtitle_trust_override: SubtitleTrust | None = None
    roster: tuple[ContextPerson, ...] = ()
    context_digest: ContextDigest | None = None

    @field_validator("roster")
    @classmethod
    def roster_order_is_not_authority(
        cls,
        value: tuple[ContextPerson, ...],
    ) -> tuple[ContextPerson, ...]:
        """Normalize roster order by opaque stable identity."""
        return tuple(sorted(value, key=lambda person: person.person_id))

    @model_validator(mode="after")
    def context_contract_is_consistent(self) -> Self:
        """Keep v1 readable and require grounded, digest-bound v2 context."""
        if not self.raw_text.strip():
            message = "raw_text must contain non-whitespace context"
            raise ValueError(message)
        if self.schema_version == "episode-context.v1":
            self._validate_legacy_contract()
            return self
        self._validate_typed_contract()
        return self

    def _validate_legacy_contract(self) -> None:
        if self.roster:
            message = "legacy episode context cannot contain a typed roster"
            raise ValueError(message)
        if self.context_digest is not None:
            message = "legacy episode context cannot contain context_digest"
            raise ValueError(message)

    def _validate_typed_contract(self) -> None:
        if not self.roster:
            message = "typed episode context requires a non-empty roster"
            raise ValueError(message)
        if self.context_digest is None:
            message = "typed episode context requires context_digest"
            raise ValueError(message)
        if self.characters:
            message = "typed episode context cannot contain legacy characters"
            raise ValueError(message)
        self._validate_roster_uniqueness()
        self._validate_roster_grounding()
        if self.context_digest != episode_context_digest(self):
            message = "context_digest does not match canonical episode context bytes"
            raise ValueError(message)

    def _validate_roster_uniqueness(self) -> None:
        person_ids = tuple(person.person_id for person in self.roster)
        if len(person_ids) != len(set(person_ids)):
            message = "roster person_id values must be unique"
            raise ValueError(message)
        display_names = tuple(person.display_name for person in self.roster)
        if len(display_names) != len(set(display_names)):
            message = "roster display_name values must be unique"
            raise ValueError(message)

    def _validate_roster_grounding(self) -> None:
        for person in self.roster:
            if person.display_name not in self.raw_text:
                message = (
                    "roster display_name is absent from raw_text: "
                    f"{person.display_name}"
                )
                raise ValueError(message)
            for hint in person.appearance_hints:
                if hint not in self.raw_text:
                    message = (
                        "roster appearance hint is absent from raw_text: "
                        f"{hint}"
                    )
                    raise ValueError(message)

    @classmethod
    def bind_typed(
        cls,
        *,
        raw_text: str,
        roster: tuple[ContextPerson, ...],
        subtitle_trust_override: SubtitleTrust | None = None,
    ) -> Self:
        """Create one normalized v2 context with its canonical digest."""
        normalized_roster = tuple(
            sorted(roster, key=lambda person: person.person_id)
        )
        payload: dict[str, object] = {
            "schema_version": "episode-context.v2",
            "raw_text": raw_text,
            "characters": (),
            "subtitle_trust_override": subtitle_trust_override,
            "roster": tuple(
                person.model_dump(mode="json") for person in normalized_roster
            ),
        }
        payload["context_digest"] = _context_payload_digest(payload)
        return cls.model_validate(payload)


def _context_payload_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def episode_context_digest(context: EpisodeContext) -> str:
    """Return the digest of canonical context bytes excluding self-reference."""
    payload = context.model_dump(mode="json", exclude={"context_digest"})
    return _context_payload_digest(payload)


class Cue(FrozenModel):
    """One canonical transcript row."""

    sequence: SequenceNumber
    cue_id: CueId | None = None
    start_tc_ms: NonNegativeMilliseconds
    end_tc_ms: PositiveMilliseconds
    speech: str
    speaker: str | None = None
    uncertainty: Uncertainty
    screen_text: str | None = None
    vision_evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed cue intervals."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "end_tc_ms must be greater than start_tc_ms"
            raise ValueError(msg)
        return self


class VisionEvidence(FrozenModel):
    """Stable evidence returned by a visual audit."""

    evidence_id: EvidenceId
    frame_ids: tuple[FrameId, ...]
    scene: str
    rough_lower_third: str | None = None


class FixtureRun(FrozenModel):
    """Deterministic adapter output used by the walking skeleton."""

    duration_ms: PositiveMilliseconds
    cues: tuple[Cue, ...]
    vision_evidence: tuple[VisionEvidence, ...]


class Transcript(FrozenModel):
    """Canonical transcript from which every derived file is generated."""

    run_id: RunId
    workflow_status: WorkflowStatus
    validation_status: ValidationStatus
    duration_ms: PositiveMilliseconds
    cues: tuple[Cue, ...]
    vision_evidence: tuple[VisionEvidence, ...]

    @model_validator(mode="after")
    def transcript_contract_is_consistent(self) -> Self:
        """Enforce ordering, duration and evidence-reference contracts."""
        expected_sequences = tuple(range(1, len(self.cues) + 1))
        actual_sequences = tuple(cue.sequence for cue in self.cues)
        if actual_sequences != expected_sequences:
            msg = "cue sequence values must be contiguous and start at 1"
            raise ValueError(msg)
        if any(cue.end_tc_ms > self.duration_ms for cue in self.cues):
            msg = "cue end_tc_ms must not exceed duration_ms"
            raise ValueError(msg)
        evidence_ids = {item.evidence_id for item in self.vision_evidence}
        missing_ids = {
            evidence_id
            for cue in self.cues
            for evidence_id in cue.vision_evidence_ids
            if evidence_id not in evidence_ids
        }
        if missing_ids:
            msg = f"cue references unknown vision evidence: {sorted(missing_ids)}"
            raise ValueError(msg)
        return self


MIN_REPETITION_COUNT = 4
MIN_REPEATED_TOKENS = 8


def has_pathological_repetition(speech: str) -> bool:
    """Detect repeated token runs characteristic of broken ASR output."""
    tokens = speech.split()
    for size in range(1, len(tokens) // MIN_REPETITION_COUNT + 1):
        for start in range(len(tokens) - size * MIN_REPETITION_COUNT + 1):
            phrase = tokens[start : start + size]
            repeats = 1
            while (
                start + size * (repeats + 1) <= len(tokens)
                and tokens[start + size * repeats : start + size * (repeats + 1)]
                == phrase
            ):
                repeats += 1
            if (
                repeats >= MIN_REPETITION_COUNT
                and repeats * size >= MIN_REPEATED_TOKENS
            ):
                return True
    return False


class RunState(FrozenModel):
    """Persisted workflow state independent from transcript validity."""

    run_id: RunId
    source_url: YouTubeVideoUrl
    workflow_status: WorkflowStatus
    validation_status: ValidationStatus = ValidationStatus.PENDING
    context: EpisodeContext | None = None


class StartResult(FrozenModel):
    """Machine-readable result of starting a run."""

    run_id: RunId
    workflow_status: WorkflowStatus


class ProcessResult(FrozenModel):
    """Machine-readable result after deterministic processing."""

    run_id: RunId
    workflow_status: WorkflowStatus
    validation_status: ValidationStatus
