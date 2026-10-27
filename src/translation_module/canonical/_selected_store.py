# Copyright 2026 Transcription Module contributors

"""Persisted candidate and selected-authority resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from translation_module.canonical._attempt_models import (
    AttemptLedgerConflictError,
    SelectionBindingError,
    TranslationSelectionReceipt,
)
from translation_module.canonical._attempt_snapshot import load_snapshot
from translation_module.canonical._attempt_store_io import (
    cleanup_orphan_attempt_temps,
    locked_attempt_namespace,
)
from translation_module.canonical._attempt_validation import validate_selection
from translation_module.canonical._attempt_wal import recover_append_wal
from translation_module.canonical._authority_namespace import (
    AuthorityNamespace,
    open_authority_namespace,
)
from translation_module.canonical._preflight_models import (
    CandidatePreflightContext,
    PreflightGateCode,
    PreflightGateError,
    PreflightReport,
)
from translation_module.canonical.digest import canonical_sha256
from translation_module.canonical.preflight import validate_preflight_report
from translation_module.canonical.translation import TranslationDocumentV2

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.source import SourceTranscriptV2

LEDGER_NAME = "translation-attempts.jsonl"
INTEGRITY_NAME = "translation-attempts.integrity.json"


def _candidate_name(document_id: str, revision_id: str) -> str:
    key = canonical_sha256((document_id, revision_id))
    return f"translation-candidate.{key}.json"


def preflight_path(root: Path, document_id: str, revision_id: str) -> Path:
    """Return the fixed persisted preflight path for one revision."""
    key = canonical_sha256((document_id, revision_id))
    return root / f"translation-preflight.{key}.json"


def persist_candidate(root: Path, candidate: TranslationDocumentV2) -> Path:
    """Persist exact immutable candidate bytes through a bound namespace."""
    with (
        open_authority_namespace(root, create=True) as namespace,
        locked_attempt_namespace(namespace),
    ):
        return persist_candidate_in_namespace(namespace, candidate)


def persist_candidate_in_namespace(
    namespace: AuthorityNamespace,
    candidate: TranslationDocumentV2,
) -> Path:
    """Persist one candidate inside an already locked authority namespace."""
    name = _candidate_name(candidate.document_id, candidate.revision_id)
    payload = f"{candidate.model_dump_json(indent=2)}\n".encode()
    if namespace.exists_regular(name):
        if namespace.read_regular(name) != payload:
            message = "persisted translation candidate is different"
            raise AttemptLedgerConflictError(message)
        return namespace.display_path / name
    namespace.write_atomic(name, payload)
    return namespace.display_path / name


def _load_candidate(
    namespace: AuthorityNamespace,
    document_id: str,
    revision_id: str,
) -> TranslationDocumentV2:
    name = _candidate_name(document_id, revision_id)
    if not namespace.exists_regular(name):
        message = "persisted translation candidate is unavailable"
        raise SelectionBindingError(message)
    try:
        candidate = TranslationDocumentV2.model_validate_json(
            namespace.read_regular(name)
        )
    except (OSError, ValidationError) as error:
        message = "persisted translation candidate is invalid"
        raise SelectionBindingError(message) from error
    if candidate.document_id != document_id or candidate.revision_id != revision_id:
        message = "persisted translation candidate identity mismatch"
        raise SelectionBindingError(message)
    return candidate


def _require_receipt(
    attempts_namespace: AuthorityNamespace,
    candidate: TranslationDocumentV2,
) -> TranslationSelectionReceipt:
    with locked_attempt_namespace(attempts_namespace):
        cleanup_orphan_attempt_temps(attempts_namespace)
        recover_append_wal(attempts_namespace, LEDGER_NAME, INTEGRITY_NAME)
        attempts = load_snapshot(
            attempts_namespace, LEDGER_NAME, INTEGRITY_NAME
        ).attempts
        key = canonical_sha256((candidate.document_id, candidate.revision_id))
        name = f"translation-selection.{key}.json"
        if not attempts_namespace.exists_regular(name):
            message = "translation revision has no selection receipt"
            raise SelectionBindingError(message)
        try:
            receipt = TranslationSelectionReceipt.model_validate_json(
                attempts_namespace.read_regular(name)
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
        validate_selection(receipt, attempt, candidate)
        return receipt


def _load_selected_candidate(
    root: Path,
    document_id: str,
    revision_id: str,
    source: SourceTranscriptV2,
    glossary: TranslationGlossaryAsset,
) -> tuple[TranslationDocumentV2, TranslationSelectionReceipt]:
    """Reload all authority through one no-follow root descriptor binding."""
    with (
        open_authority_namespace(root) as namespace,
        namespace.child("attempts") as attempts_namespace,
    ):
        candidate = _load_candidate(namespace, document_id, revision_id)
        persisted_context = CandidatePreflightContext(candidate, source, glossary)
        report_name = preflight_path(root, document_id, revision_id).name
        if not namespace.exists_regular(report_name):
            error = FileNotFoundError(report_name)
            raise PreflightGateError(
                PreflightGateCode.COVERAGE_INVALID,
                "persisted preflight report is unavailable or invalid",
            ) from error
        try:
            report = PreflightReport.model_validate_json(
                namespace.read_regular(report_name)
            )
        except (OSError, ValidationError) as error:
            raise PreflightGateError(
                PreflightGateCode.COVERAGE_INVALID,
                "persisted preflight report is unavailable or invalid",
            ) from error
        validate_preflight_report(persisted_context, report)
        receipt = _require_receipt(attempts_namespace, candidate)
        return candidate, receipt


def load_selected_candidate(
    root: Path,
    document_id: str,
    revision_id: str,
    source: SourceTranscriptV2,
    glossary: TranslationGlossaryAsset,
) -> TranslationDocumentV2:
    """Reload one selected candidate through a bound root descriptor."""
    return _load_selected_candidate(
        root,
        document_id,
        revision_id,
        source,
        glossary,
    )[0]


def load_selected_candidate_with_receipt(
    root: Path,
    document_id: str,
    revision_id: str,
    source: SourceTranscriptV2,
    glossary: TranslationGlossaryAsset,
) -> tuple[TranslationDocumentV2, TranslationSelectionReceipt]:
    """Reload one candidate and its exact persisted selection receipt."""
    return _load_selected_candidate(
        root,
        document_id,
        revision_id,
        source,
        glossary,
    )


__all__ = [
    "load_selected_candidate",
    "load_selected_candidate_with_receipt",
    "persist_candidate",
    "preflight_path",
]
