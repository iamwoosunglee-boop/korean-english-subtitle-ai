# Copyright 2026 Transcription Module contributors

"""Validated attempt-ledger snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from translation_module.canonical._attempt_models import (
    CorruptAttemptLedgerError,
    TranslationAttempt,
    TranslationAttemptLedgerAuthority,
)
from translation_module.canonical._attempt_validation import parse_ledger

if TYPE_CHECKING:
    from translation_module.canonical._authority_namespace import AuthorityNamespace


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    """Exact ledger bytes and parsed append history."""

    ledger_bytes: bytes
    attempts: tuple[TranslationAttempt, ...]


def ledger_digest(payload: bytes) -> str:
    """Return the ledger integrity digest."""
    return hashlib.sha256(payload).hexdigest()


def load_snapshot(
    namespace: AuthorityNamespace,
    ledger_name: str,
    authority_name: str,
) -> AttemptSnapshot:
    """Load bytes only when they match descriptor-relative authority."""
    ledger_exists = namespace.exists_regular(ledger_name)
    authority_exists = namespace.exists_regular(authority_name)
    if not ledger_exists and not authority_exists:
        return AttemptSnapshot(b"", ())
    if ledger_exists != authority_exists:
        message = "attempt ledger integrity authority is incomplete"
        raise CorruptAttemptLedgerError(message)
    ledger_bytes = namespace.read_regular(ledger_name)
    attempts = parse_ledger(ledger_bytes)
    try:
        authority = TranslationAttemptLedgerAuthority.model_validate_json(
            namespace.read_regular(authority_name)
        )
    except ValidationError as error:
        message = "attempt ledger integrity authority is invalid"
        raise CorruptAttemptLedgerError(message) from error
    first = attempts[0] if attempts else None
    if (
        first is None
        or authority.attempt_count != len(attempts)
        or authority.ledger_digest != ledger_digest(ledger_bytes)
        or authority.translation_document_id
        != first.binding.translation_document_id
        or authority.translation_revision_id
        != first.binding.translation_revision_id
    ):
        message = "attempt ledger prefix integrity mismatch"
        raise CorruptAttemptLedgerError(message)
    return AttemptSnapshot(ledger_bytes, attempts)


__all__ = ["AttemptSnapshot", "ledger_digest", "load_snapshot"]
