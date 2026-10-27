# Copyright 2026 Transcription Module contributors

"""Exact candidate binding and ordered rationale validation."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import ValidationError

from translation_module.canonical._attempt_models import (
    AttemptBindingError,
    AttemptCoverageError,
    AttemptLedgerConflictError,
    CorruptAttemptLedgerError,
    SelectionBindingError,
    TranslationAttempt,
    TranslationAttemptBinding,
    TranslationAttemptOutcome,
    TranslationSelectionReceipt,
)
from translation_module.canonical.digest import (
    CanonicalDigestMismatchError,
    verify_canonical_digests,
)

if TYPE_CHECKING:
    from translation_module.canonical.translation import (
        TargetCueV2,
        TranslationDocumentV2,
    )


def _validate_history(attempts: tuple[TranslationAttempt, ...]) -> None:
    seen: set[str] = set()
    for expected_ordinal, attempt in enumerate(attempts, start=1):
        if attempt.ordinal != expected_ordinal:
            message = "attempt ordinal conflicts with append order"
            raise AttemptLedgerConflictError(message)
        if attempt.attempt_id in seen:
            message = "attempt ID is duplicated"
            raise AttemptLedgerConflictError(message)
        if (
            attempt.parent_attempt_id is not None
            and attempt.parent_attempt_id not in seen
        ):
            message = "parent attempt must reference an earlier attempt ID"
            raise AttemptLedgerConflictError(message)
        seen.add(attempt.attempt_id)


def parse_ledger(payload: bytes) -> tuple[TranslationAttempt, ...]:
    """Parse structurally valid append history before integrity comparison."""
    if payload and not payload.endswith(b"\n"):
        message = "attempt ledger has an incomplete final line"
        raise CorruptAttemptLedgerError(message)
    try:
        attempts = tuple(
            TranslationAttempt.model_validate_json(line)
            for line in payload.splitlines()
        )
    except ValidationError as error:
        message = "attempt ledger contains an invalid record"
        raise CorruptAttemptLedgerError(message) from error
    _validate_history(attempts)
    return attempts


def expected_binding(document: TranslationDocumentV2) -> TranslationAttemptBinding:
    """Build the exact immutable dependency binding for a candidate."""
    return TranslationAttemptBinding(
        translation_document_id=document.document_id,
        translation_revision_id=document.revision_id,
        source_document_id=document.source_ref.document_id,
        source_revision_id=document.source_ref.revision_id,
        source_content_digest=document.source_ref.content_digest,
        prepared_context_asset_id=document.prepared_context_ref.asset_id,
        prepared_context_revision_id=document.prepared_context_ref.revision_id,
        prepared_context_content_digest=document.prepared_context_ref.content_digest,
        glossary_asset_id=document.glossary_ref.asset_id,
        glossary_revision_id=document.glossary_ref.revision_id,
        glossary_content_digest=document.glossary_ref.content_digest,
    )


def displayed_lines(cue: TargetCueV2) -> tuple[tuple[str, str], ...]:
    """Pair each explicit displayed line with unambiguous speaker ownership."""
    lines = cue.text.split("\n")
    if any(not line.strip() for line in lines):
        message = f"rationale coverage cannot bind blank line: {cue.cue_id}"
        raise AttemptCoverageError(message)
    if len(lines) == 1:
        return ((lines[0], cue.speakers[0]),)
    if len(cue.speakers) == 1:
        return tuple((line, cue.speakers[0]) for line in lines)
    if len(cue.speakers) != len(lines):
        message = f"ambiguous speaker ownership: {cue.cue_id}"
        raise AttemptCoverageError(message)
    return tuple(zip(lines, cue.speakers, strict=True))


def _verify_document(document: TranslationDocumentV2) -> None:
    try:
        verify_canonical_digests(document)
    except CanonicalDigestMismatchError as error:
        message = f"translation document digest mismatch: {error}"
        raise AttemptBindingError(message) from error


def _validate_ordered_rationales(
    attempt: TranslationAttempt,
    document: TranslationDocumentV2,
) -> None:
    rationales = attempt.rationales
    if rationales is None:
        message = "successful attempt has no rationale coverage"
        raise AttemptCoverageError(message)
    position = 0
    for cue in document.cues:
        for line_number, (line, speaker) in enumerate(displayed_lines(cue), start=1):
            if position >= len(rationales):
                message = "rationale coverage order is incomplete"
                raise AttemptCoverageError(message)
            rationale = rationales[position]
            expected_position = (cue.cue_id, line_number)
            if (rationale.target_cue_id, rationale.line_number) != expected_position:
                message = "rationale coverage order must match displayed lines exactly"
                raise AttemptCoverageError(message)
            if rationale.speaker_id != speaker:
                message = f"rationale speaker mismatch: {cue.cue_id}:{line_number}"
                raise AttemptCoverageError(message)
            if rationale.line_text_digest != translation_line_text_digest(line):
                message = (
                    f"rationale line text digest mismatch: {cue.cue_id}:{line_number}"
                )
                raise AttemptCoverageError(message)
            position += 1
    if position != len(rationales):
        message = "rationale coverage order contains foreign displayed lines"
        raise AttemptCoverageError(message)


def validate_attempt(
    attempt: TranslationAttempt,
    document: TranslationDocumentV2,
) -> TranslationAttempt:
    """Parse and bind one attempt to a digest-valid candidate and ordered lines."""
    attempt = TranslationAttempt.model_validate(
        attempt.model_dump(mode="python", exclude_none=True)
    )
    _verify_document(document)
    if attempt.binding != expected_binding(document):
        message = "attempt dependency binding mismatch"
        raise AttemptBindingError(message)
    if attempt.outcome is TranslationAttemptOutcome.FAILED:
        return attempt
    if attempt.candidate_digest != document.content_digest:
        message = "attempt candidate digest mismatch"
        raise AttemptBindingError(message)
    _validate_ordered_rationales(attempt, document)
    return attempt


def validate_failed_attempt(attempt: TranslationAttempt) -> TranslationAttempt:
    """Parse one candidate-free failed attempt before ledger admission."""
    attempt = TranslationAttempt.model_validate(
        attempt.model_dump(mode="python", exclude_none=True)
    )
    if attempt.outcome is not TranslationAttemptOutcome.FAILED:
        message = "candidate-free append requires a failed attempt"
        raise AttemptLedgerConflictError(message)
    return attempt


def validate_selection_document(document: TranslationDocumentV2) -> None:
    """Reject a selection candidate whose stored self-digests are stale."""
    try:
        _verify_document(document)
    except AttemptBindingError as error:
        raise SelectionBindingError(str(error)) from error


def validate_selection(
    receipt: TranslationSelectionReceipt,
    attempt: TranslationAttempt,
    document: TranslationDocumentV2,
) -> None:
    """Require a receipt to select one matching persisted successful attempt."""
    try:
        attempt = validate_attempt(attempt, document)
    except AttemptBindingError as error:
        raise SelectionBindingError(str(error)) from error
    if attempt.outcome is not TranslationAttemptOutcome.SUCCEEDED:
        message = "selection requires a successful persisted attempt"
        raise SelectionBindingError(message)
    if receipt.translation_document_digest != document.content_digest:
        message = "selection document digest mismatch"
        raise SelectionBindingError(message)
    if (
        receipt.translation_document_id != document.document_id
        or receipt.translation_revision_id != document.revision_id
        or receipt.attempt_id != attempt.attempt_id
        or receipt.attempt_candidate_digest != attempt.candidate_digest
    ):
        message = "selection identity mismatch"
        raise SelectionBindingError(message)


def translation_line_text_digest(text: str) -> str:
    """Hash the exact UTF-8 bytes displayed on one target line."""
    return hashlib.sha256(text.encode()).hexdigest()
