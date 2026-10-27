# Copyright 2026 Transcription Module contributors

"""Typed errors rendered at the CLI boundary."""

from __future__ import annotations

from enum import StrEnum

from transcription_module.models import FrozenModel


class ErrorCode(StrEnum):
    """Stable machine-readable CLI error codes."""

    INVALID_URL = "invalid_url"
    INVALID_RUN_ID = "invalid_run_id"
    INVALID_EPISODE_CONTEXT = "invalid_episode_context"
    RUN_NOT_FOUND = "run_not_found"
    CORRUPT_CONFIRMATION_LOG = "corrupt_confirmation_log"
    INVALID_CONFIRMATION_ID = "invalid_confirmation_id"
    DUPLICATE_ANSWER = "duplicate_answer"
    CONFIRMATION_SUGGESTION_CONFLICT = "confirmation_suggestion_conflict"
    EMPTY_ANSWER = "empty_answer"
    NO_PENDING_CONFIRMATION = "no_pending_confirmation"
    CONFIRMATION_CONSENT_REQUIRED = "confirmation_consent_required"
    CONFIRMATION_SESSION_ENDED = "confirmation_session_ended"
    CONFIRMATION_SESSION_ACTIVE = "confirmation_session_active"
    STALE_CONFIRMATION = "stale_confirmation"
    CONFIRMATION_TARGET_DELETED = "confirmation_target_deleted"
    SPEAKER_INFERENCE_REQUIRED = "speaker_inference_required"
    MEDIA_PREPARATION_FAILED = "media_preparation_failed"
    ASR_FAILED = "asr_failed"
    VAD_RECOVERY_FAILED = "vad_recovery_failed"
    VISION_FAILED = "vision_failed"
    VALIDATION_FAILED = "validation_failed"
    PROTECTED_RUN_FAILED = "protected_run_failed"
    SOURCE_EDIT_FAILED = "source_edit_failed"
    TRANSLATION_ADMISSION_FAILED = "translation_admission_failed"


class CliBoundaryError(Exception):
    """Expected failure that the CLI renders without a traceback."""

    code: ErrorCode
    message: str

    def __init__(self, code: ErrorCode, message: str) -> None:
        """Create one expected CLI boundary failure."""
        super().__init__(message)
        self.code = code
        self.message = message


class ErrorDetail(FrozenModel):
    """Stable error payload nested below the error key."""

    code: ErrorCode
    message: str


class ErrorEnvelope(FrozenModel):
    """Machine-readable stderr payload."""

    error: ErrorDetail
