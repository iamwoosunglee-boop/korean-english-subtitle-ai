# Copyright 2026 Transcription Module contributors

"""Typed records for resumable one-at-a-time confirmations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, NewType, Self, override

from pydantic import Field, TypeAdapter, model_validator

from transcription_module.models import (
    CueId,
    FrozenModel,
    Milliseconds,
    WorkflowStatus,
)

ConfirmationId = NewType("ConfirmationId", str)
ConfirmationSessionId = NewType("ConfirmationSessionId", str)


class ConfirmationField(StrEnum):
    """Cue fields supported by the one-at-a-time confirmation lane."""

    SPEECH = "speech"
    SPEAKER = "speaker"
    SCREEN_TEXT = "screen_text"


class ConfirmationItemKind(StrEnum):
    """Typed interaction represented by one confirmation request."""

    FIELD = "field"
    VISUAL_INSERTION = "visual_insertion"


class VisualInsertionAction(StrEnum):
    """Explicit decisions for a provisional visual-speech insertion."""

    ACCEPT = "accept"
    REJECT = "reject"


class ConfirmationErrorCode(StrEnum):
    """Stable failures that must leave the current draft unchanged."""

    CORRUPT_LOG = "corrupt_confirmation_log"
    INVALID_CONFIRMATION_ID = "invalid_confirmation_id"
    DUPLICATE_ANSWER = "duplicate_answer"
    EMPTY_ANSWER = "empty_answer"
    NO_PENDING_ITEM = "no_pending_confirmation"
    CONSENT_REQUIRED = "confirmation_consent_required"
    SESSION_ENDED = "confirmation_session_ended"
    SESSION_ACTIVE = "confirmation_session_active"
    SPEAKER_INFERENCE_REQUIRED = "speaker_inference_required"
    STALE_CONFIRMATION = "stale_confirmation"


class ConfirmationSessionStatus(StrEnum):
    """Consent and completion lifecycle for one confirmation attempt."""

    AWAITING_CONSENT = "awaiting_consent"
    ACTIVE = "active"
    DECLINED = "declined"
    COMPLETED = "completed"


@dataclass(slots=True)
class ConfirmationError(Exception):
    """Typed confirmation failure translated by the CLI boundary."""

    code: ConfirmationErrorCode
    message: str

    @override
    def __str__(self) -> str:
        """Return the stable human-readable error."""
        return self.message


class ConfirmationItem(FrozenModel):
    """One unresolved cue field shown to the user."""

    kind: ConfirmationItemKind = ConfirmationItemKind.FIELD
    confirmation_id: ConfirmationId = Field(min_length=1)
    cue_id: CueId | None = None
    cue_sequence: int = Field(ge=1)
    field: ConfirmationField | None = None
    provisional_id: str | None = Field(default=None, min_length=1)
    actions: tuple[VisualInsertionAction, ...] = ()
    depends_on_confirmation_id: ConfirmationId | None = None
    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)
    current_value: str | None
    cue_speech: str | None = None
    candidates: tuple[str, ...]
    evidence: tuple[str, ...]

    @model_validator(mode="after")
    def interaction_is_explicit(self) -> Self:
        """Reject field/action hybrids and implicit insertion decisions."""
        match self.kind:
            case ConfirmationItemKind.FIELD:
                if (
                    self.field is None
                    or self.provisional_id is not None
                    or self.actions
                ):
                    message = "field confirmation has invalid insertion fields"
                    raise ValueError(message)
            case ConfirmationItemKind.VISUAL_INSERTION:
                if (
                    self.field is not None
                    or self.provisional_id is None
                    or self.cue_id is None
                    or self.depends_on_confirmation_id is not None
                    or self.actions
                    != (
                        VisualInsertionAction.ACCEPT,
                        VisualInsertionAction.REJECT,
                    )
                ):
                    message = "visual insertion confirmation requires accept and reject"
                    raise ValueError(message)
        return self


class ConfirmationQueueStatus(FrozenModel):
    """Single-item confirmation queue projection for the CLI."""

    pending: bool
    item: ConfirmationItem | None = None
    current_number: int = Field(ge=1)
    total_count: int = Field(ge=1)
    remaining_count: int = Field(ge=1)


class ConfirmationSession(FrozenModel):
    """One attempt whose consent cannot leak into a later attempt."""

    session_id: ConfirmationSessionId
    status: ConfirmationSessionStatus
    source_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    scope_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class ConfirmationRequestRecord(FrozenModel):
    """Append-only request record."""

    kind: Literal["request"] = "request"
    ordinal: int = Field(ge=1)
    item: ConfirmationItem


class ConfirmationAnswerRecord(FrozenModel):
    """Append-only user-answer record."""

    kind: Literal["answer"] = "answer"
    ordinal: int = Field(ge=1)
    confirmation_id: ConfirmationId
    answer: str = Field(min_length=1)


class ConfirmationSuggestionRecord(FrozenModel):
    """Append-only agent suggestion that never resolves an item."""

    kind: Literal["suggestion"] = "suggestion"
    ordinal: int = Field(ge=1)
    confirmation_id: ConfirmationId
    suggestion: str = Field(min_length=1)


class ConfirmationAppliedRecord(FrozenModel):
    """Append-only record of a successfully regenerated draft."""

    kind: Literal["applied"] = "applied"
    ordinal: int = Field(ge=1)
    confirmation_id: ConfirmationId
    applied_value: str
    workflow_status: WorkflowStatus


class ConfirmationVisualInsertionAnswerRecord(FrozenModel):
    """Append-only typed answer for one provisional insertion."""

    kind: Literal["visual_insertion_answer"] = "visual_insertion_answer"
    ordinal: int = Field(ge=1)
    confirmation_id: ConfirmationId
    action: VisualInsertionAction
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_source_digest_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConfirmationVisualInsertionAppliedRecord(FrozenModel):
    """Replayable application provenance for an insertion decision."""

    kind: Literal["visual_insertion_applied"] = "visual_insertion_applied"
    ordinal: int = Field(ge=1)
    confirmation_id: ConfirmationId
    provisional_id: str = Field(min_length=1)
    action: VisualInsertionAction
    cue_id: CueId
    source_digest_before: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_digest_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest_before: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_status: WorkflowStatus


class ConfirmationRetiredRecord(FrozenModel):
    """Retire one request whose prerequisite was rejected."""

    kind: Literal["retired"] = "retired"
    ordinal: int = Field(ge=1)
    confirmation_id: ConfirmationId
    depends_on_confirmation_id: ConfirmationId
    reason: Literal["provisional_insertion_rejected"] = (
        "provisional_insertion_rejected"
    )


type ConfirmationRecord = Annotated[
    ConfirmationRequestRecord
    | ConfirmationAnswerRecord
    | ConfirmationSuggestionRecord
    | ConfirmationAppliedRecord
    | ConfirmationVisualInsertionAnswerRecord
    | ConfirmationVisualInsertionAppliedRecord
    | ConfirmationRetiredRecord,
    Field(discriminator="kind"),
]
CONFIRMATION_RECORD_ADAPTER: TypeAdapter[ConfirmationRecord] = TypeAdapter(
    ConfirmationRecord
)
