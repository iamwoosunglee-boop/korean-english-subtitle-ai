# Copyright 2026 Transcription Module contributors

"""Public canonical translation attempt store contracts."""

from translation_module.canonical._attempt_models import (
    AttemptBindingError,
    AttemptCoverageError,
    AttemptLedgerConflictError,
    CorruptAttemptLedgerError,
    SelectionBindingError,
    SelectionConflictError,
    TranslationAttempt,
    TranslationAttemptBinding,
    TranslationAttemptKind,
    TranslationAttemptLedgerAuthority,
    TranslationAttemptOutcome,
    TranslationLineRationale,
    TranslationSelectionReceipt,
)
from translation_module.canonical._attempt_store import TranslationAttemptStore
from translation_module.canonical._attempt_validation import (
    translation_line_text_digest,
)

__all__ = [
    "AttemptBindingError",
    "AttemptCoverageError",
    "AttemptLedgerConflictError",
    "CorruptAttemptLedgerError",
    "SelectionBindingError",
    "SelectionConflictError",
    "TranslationAttempt",
    "TranslationAttemptBinding",
    "TranslationAttemptKind",
    "TranslationAttemptLedgerAuthority",
    "TranslationAttemptOutcome",
    "TranslationAttemptStore",
    "TranslationLineRationale",
    "TranslationSelectionReceipt",
    "translation_line_text_digest",
]
