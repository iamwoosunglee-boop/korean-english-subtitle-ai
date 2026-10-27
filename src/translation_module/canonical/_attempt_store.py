# Copyright 2026 Transcription Module contributors

"""Canonical store-root authority for attempts and selections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, final

from pydantic import ValidationError

from translation_module.canonical._attempt_models import (
    AttemptLedgerConflictError,
    SelectionBindingError,
    SelectionConflictError,
    TranslationAttempt,
    TranslationAttemptLedgerAuthority,
    TranslationSelectionReceipt,
)
from translation_module.canonical._attempt_snapshot import (
    AttemptSnapshot,
    ledger_digest,
    load_snapshot,
)
from translation_module.canonical._attempt_store_io import (
    cleanup_orphan_attempt_temps,
    locked_attempt_root,
    replace_attempt_files_transactionally,
)
from translation_module.canonical._attempt_validation import (
    validate_attempt,
    validate_failed_attempt,
    validate_selection,
    validate_selection_document,
)
from translation_module.canonical._attempt_wal import (
    persist_append_wal,
    prepare_append_wal,
    recover_append_wal,
)
from translation_module.canonical.digest import canonical_sha256

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical._authority_namespace import AuthorityNamespace
    from translation_module.canonical._preflight_models import (
        CandidatePreflightContext,
        PreflightReport,
    )
    from translation_module.canonical.translation import TranslationDocumentV2


class PreflightSelection(Protocol):
    """Structural preflight request accepted by the store gate."""

    @property
    def receipt(self) -> TranslationSelectionReceipt:
        """Return the requested immutable receipt."""
        ...

    @property
    def context(self) -> CandidatePreflightContext:
        """Return the exact candidate dependency graph."""
        ...

    @property
    def report(self) -> PreflightReport:
        """Return the deterministic report carried by this request."""
        ...

    def validate(self) -> None:
        """Validate current deterministic preflight authority."""
        ...


LEDGER_NAME: Final = "translation-attempts.jsonl"
AUTHORITY_NAME: Final = "translation-attempts.integrity.json"


def _validate_admission(
    attempt: TranslationAttempt,
    attempts: tuple[TranslationAttempt, ...],
) -> TranslationAttempt:
    existing = next(
        (item for item in attempts if item.attempt_id == attempt.attempt_id),
        None,
    )
    if existing is not None:
        if existing == attempt:
            return existing
        message = "attempt ID already exists with different content"
        raise AttemptLedgerConflictError(message)
    if attempt.ordinal != len(attempts) + 1:
        message = "attempt ordinal must be the next append position"
        raise AttemptLedgerConflictError(message)
    known_ids = {item.attempt_id for item in attempts}
    if (
        attempt.parent_attempt_id is not None
        and attempt.parent_attempt_id not in known_ids
    ):
        message = "parent attempt must reference an earlier attempt ID"
        raise AttemptLedgerConflictError(message)
    return attempt


@final
class TranslationAttemptStore:
    """Own one trusted attempt ledger and canonical revision receipts."""

    def __init__(self, root: Path) -> None:
        """Configure one canonical authority root without creating it."""
        self._root = root

    @property
    def ledger_path(self) -> Path:
        """Return the sole append-only ledger path under this authority."""
        return self._root / LEDGER_NAME

    @property
    def authority_path(self) -> Path:
        """Return the trusted ledger-prefix integrity path."""
        return self._root / AUTHORITY_NAME

    def selection_path(self, document_id: str, revision_id: str) -> Path:
        """Derive the sole receipt path for one translation revision."""
        key = canonical_sha256((document_id, revision_id))
        return self._root / f"translation-selection.{key}.json"

    def _snapshot(self, namespace: AuthorityNamespace) -> AttemptSnapshot:
        cleanup_orphan_attempt_temps(namespace)
        recover_append_wal(namespace, LEDGER_NAME, AUTHORITY_NAME)
        return load_snapshot(namespace, LEDGER_NAME, AUTHORITY_NAME)

    def load(self) -> tuple[TranslationAttempt, ...]:
        """Load attempts only when bytes match the trusted prefix authority."""
        if not self._root.exists():
            return ()
        with locked_attempt_root(self._root) as namespace:
            return self._snapshot(namespace).attempts

    def append(
        self,
        attempt: TranslationAttempt,
        document: TranslationDocumentV2,
    ) -> TranslationAttempt:
        """Append and advance ledger integrity as one rollback-safe transaction."""
        attempt = validate_attempt(attempt, document)
        return self._append_validated(attempt)

    def validate_append(
        self,
        attempt: TranslationAttempt,
        document: TranslationDocumentV2,
    ) -> TranslationAttempt:
        """Validate candidate and current ledger admission without writing."""
        attempt = validate_attempt(attempt, document)
        return _validate_admission(attempt, self.load())

    def append_failed(self, attempt: TranslationAttempt) -> TranslationAttempt:
        """Append a failed execution with an intended binding and no candidate."""
        attempt = validate_failed_attempt(attempt)
        return self._append_validated(attempt)

    def validate_failed_append(
        self,
        attempt: TranslationAttempt,
    ) -> TranslationAttempt:
        """Validate failed-attempt ledger admission without writing."""
        attempt = validate_failed_attempt(attempt)
        return _validate_admission(attempt, self.load())

    def _append_validated(self, attempt: TranslationAttempt) -> TranslationAttempt:
        with locked_attempt_root(self._root, create=True) as namespace:
            snapshot = self._snapshot(namespace)
            admitted = _validate_admission(attempt, snapshot.attempts)
            if admitted is not attempt:
                return admitted
            ledger_bytes = snapshot.ledger_bytes + (
                f"{attempt.model_dump_json(exclude_none=True)}\n".encode()
            )
            authority_binding = (
                snapshot.attempts[0].binding if snapshot.attempts else attempt.binding
            )
            authority = TranslationAttemptLedgerAuthority(
                translation_document_id=authority_binding.translation_document_id,
                translation_revision_id=authority_binding.translation_revision_id,
                attempt_count=attempt.ordinal,
                ledger_digest=ledger_digest(ledger_bytes),
            )
            authority_bytes = f"{authority.model_dump_json(indent=2)}\n".encode()
            wal = prepare_append_wal(
                snapshot.ledger_bytes,
                namespace.read_regular(AUTHORITY_NAME)
                if namespace.exists_regular(AUTHORITY_NAME)
                else None,
                ledger_bytes[len(snapshot.ledger_bytes) :],
                authority_bytes,
            )
            persist_append_wal(namespace, wal)
            recover_append_wal(namespace, LEDGER_NAME, AUTHORITY_NAME)
        return attempt

    def persist_preflighted_selection(self, selection: PreflightSelection) -> Path:
        """Select only through a current clean deterministic preflight."""
        selection.validate()
        return self._select(selection.receipt, selection.context.candidate)

    def _select(
        self,
        receipt: TranslationSelectionReceipt,
        document: TranslationDocumentV2,
    ) -> Path:
        """Persist one receipt after the public preflight gate has passed."""
        receipt = TranslationSelectionReceipt.model_validate(receipt.model_dump())
        validate_selection_document(document)
        if not self._root.exists():
            message = "selection attempt is not persisted in this store"
            raise SelectionBindingError(message)
        with locked_attempt_root(self._root) as namespace:
            attempts = self._snapshot(namespace).attempts
            attempt = next(
                (item for item in attempts if item.attempt_id == receipt.attempt_id),
                None,
            )
            if attempt is None:
                message = "selection attempt is not persisted in this store"
                raise SelectionBindingError(message)
            validate_selection(receipt, attempt, document)
            path = self.selection_path(document.document_id, document.revision_id)
            name = path.name
            payload = f"{receipt.model_dump_json(indent=2)}\n".encode()
            if namespace.exists_regular(name):
                existing_bytes = namespace.read_regular(name)
                try:
                    existing = TranslationSelectionReceipt.model_validate_json(
                        existing_bytes
                    )
                except ValidationError as error:
                    message = "existing translation selection receipt is invalid"
                    raise SelectionConflictError(message) from error
                if existing != receipt or existing_bytes != payload:
                    message = "existing translation selection receipt is different"
                    raise SelectionConflictError(message)
                return path
            replace_attempt_files_transactionally(namespace, ((name, payload),))
            return path

    def require_selected_receipt(
        self,
        document: TranslationDocumentV2,
    ) -> TranslationSelectionReceipt:
        """Resolve the immutable current receipt against ledger authority."""
        validate_selection_document(document)
        path = self.selection_path(document.document_id, document.revision_id)
        if not self._root.exists():
            message = "translation revision has no selection receipt"
            raise SelectionBindingError(message)
        with locked_attempt_root(self._root) as namespace:
            attempts = self._snapshot(namespace).attempts
            if not namespace.exists_regular(path.name):
                message = "translation revision has no selection receipt"
                raise SelectionBindingError(message)
            try:
                receipt = TranslationSelectionReceipt.model_validate_json(
                    namespace.read_regular(path.name)
                )
            except ValidationError as error:
                message = "translation selection receipt is invalid"
                raise SelectionBindingError(message) from error
            attempt = next(
                (item for item in attempts if item.attempt_id == receipt.attempt_id),
                None,
            )
            if attempt is None:
                message = "selection attempt is not persisted in this store"
                raise SelectionBindingError(message)
            validate_selection(receipt, attempt, document)
            return receipt
