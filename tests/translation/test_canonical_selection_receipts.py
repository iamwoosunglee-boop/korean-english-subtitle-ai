# Copyright 2026 Transcription Module contributors

"""Canonical selection receipt authority tests."""

import os
from pathlib import Path

import pytest

from tests.translation.canonical_attempt_support import (
    attempt_document,
    failed_attempt,
    selection_receipt,
    successful_attempt,
)
from tests.translation.canonical_preflight_support import (
    checker_evidence,
    preflight_context,
)
from translation_module.canonical.attempts import (
    CorruptAttemptLedgerError,
    SelectionBindingError,
    SelectionConflictError,
    TranslationAttemptStore,
    TranslationSelectionReceipt,
)
from translation_module.canonical.preflight import (
    CandidatePreflightContext,
    PreflightedSelection,
    PreflightGateError,
    build_preflight_report,
)
from translation_module.canonical.translation import TranslationDocumentV2


def _selection_request(
    document: TranslationDocumentV2,
    receipt: TranslationSelectionReceipt,
) -> PreflightedSelection:
    fixture = preflight_context()
    context = CandidatePreflightContext(document, fixture.source, fixture.glossary)
    return PreflightedSelection(
        receipt, context, build_preflight_report(context, checker_evidence(context))
    )


def test_store_exposes_no_direct_selection_bypass(tmp_path: Path) -> None:
    """Selection persistence exists only behind the complete preflight API."""
    store = TranslationAttemptStore(tmp_path / "store")

    assert not hasattr(store, "select")


def test_selection_is_immutable_and_binds_successful_attempt(tmp_path: Path) -> None:
    """Only one persisted matching attempt can authorize a revision."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    first = successful_attempt(document)
    selected = successful_attempt(document, "attempt-b", 2)
    failed = failed_attempt(document)
    _ = store.append(first, document)
    _ = store.append(selected, document)
    _ = store.append(failed, document)
    receipt = selection_receipt(document, selected)

    path = store.persist_preflighted_selection(_selection_request(document, receipt))

    stored = path.read_bytes()
    assert TranslationSelectionReceipt.model_validate_json(stored) == receipt
    assert "rationale" not in receipt.model_dump(mode="json")
    assert (
        store.persist_preflighted_selection(_selection_request(document, receipt))
        == path
    )
    different_receipt = selection_receipt(document, first)
    with pytest.raises(SelectionConflictError, match="different"):
        _ = store.persist_preflighted_selection(
            _selection_request(document, different_receipt)
        )
    failed_receipt = receipt.model_copy(update={"attempt_id": failed.attempt_id})
    with pytest.raises(SelectionBindingError, match="successful"):
        _ = store.persist_preflighted_selection(
            _selection_request(document, failed_receipt)
        )
    stale = receipt.model_copy(update={"translation_document_digest": "f" * 64})
    with pytest.raises(SelectionBindingError, match="document digest"):
        _ = store.persist_preflighted_selection(_selection_request(document, stale))
    assert path.read_bytes() == stored


def test_unpersisted_attempt_cannot_be_selected(tmp_path: Path) -> None:
    """Selection authority resolves attempts only from its persisted ledger."""
    document = attempt_document()
    attempt = successful_attempt(document)
    receipt = selection_receipt(document, attempt)

    with pytest.raises(SelectionBindingError, match="persisted"):
        _ = TranslationAttemptStore(tmp_path / "store").persist_preflighted_selection(
            _selection_request(document, receipt)
        )


def test_same_revision_has_one_store_derived_selection_path(tmp_path: Path) -> None:
    """Separate callers sharing a root cannot choose alternate receipt paths."""
    document = attempt_document()
    first_caller = TranslationAttemptStore(tmp_path / "store")
    attempt = successful_attempt(document)
    _ = first_caller.append(attempt, document)
    receipt = selection_receipt(document, attempt)
    path = first_caller.persist_preflighted_selection(
        _selection_request(document, receipt)
    )

    assert (
        TranslationAttemptStore(tmp_path / "store").persist_preflighted_selection(
            _selection_request(document, receipt)
        )
        == path
    )


def test_internally_stale_document_digest_rejects_selection(tmp_path: Path) -> None:
    """Selection verifies document self-digests before resolving authority."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    attempt = successful_attempt(document)
    _ = store.append(attempt, document)
    receipt = selection_receipt(document, attempt)
    stale = document.model_copy(update={"content_digest": "f" * 64})

    with pytest.raises(PreflightGateError, match="digest"):
        _ = store.persist_preflighted_selection(_selection_request(stale, receipt))


@pytest.mark.parametrize("cycle", ["first", "second"])
def test_post_rename_selection_interruption_replays_committed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cycle: str,
) -> None:
    """A committed receipt is deterministic success after repeated interrupts."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / cycle)
    attempt = successful_attempt(document)
    _ = store.append(attempt, document)
    receipt = selection_receipt(document, attempt)
    expected = store.selection_path(document.document_id, document.revision_id)
    original = os.replace
    interrupted = False

    def commit_then_interrupt(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal interrupted
        original(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        if target == expected.name and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    with monkeypatch.context() as injected:
        injected.setattr(os, "replace", commit_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            _ = store.persist_preflighted_selection(
                _selection_request(document, receipt)
            )
    assert (
        store.persist_preflighted_selection(_selection_request(document, receipt))
        == expected
    )
    committed = expected.read_bytes()
    assert (
        store.persist_preflighted_selection(_selection_request(document, receipt))
        == expected
    )
    assert expected.read_bytes() == committed


def test_tampered_existing_receipt_rejects_replay(tmp_path: Path) -> None:
    """An existing path is success only for exact canonical receipt bytes."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    attempt = successful_attempt(document)
    _ = store.append(attempt, document)
    receipt = selection_receipt(document, attempt)
    path = store.persist_preflighted_selection(_selection_request(document, receipt))
    _ = path.write_bytes(b"{}\n")

    with pytest.raises(SelectionConflictError, match="invalid"):
        _ = store.persist_preflighted_selection(_selection_request(document, receipt))


def test_committed_receipt_replay_revalidates_document_and_ledger(
    tmp_path: Path,
) -> None:
    """Replay never bypasses current document or persisted-ledger authority."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    attempt = successful_attempt(document)
    _ = store.append(attempt, document)
    receipt = selection_receipt(document, attempt)
    _ = store.persist_preflighted_selection(_selection_request(document, receipt))
    stale = document.model_copy(update={"content_digest": "f" * 64})

    with pytest.raises(PreflightGateError, match="digest"):
        _ = store.persist_preflighted_selection(_selection_request(stale, receipt))
    _ = store.ledger_path.write_bytes(b" " + store.ledger_path.read_bytes())
    with pytest.raises(CorruptAttemptLedgerError, match="integrity"):
        _ = store.persist_preflighted_selection(_selection_request(document, receipt))
