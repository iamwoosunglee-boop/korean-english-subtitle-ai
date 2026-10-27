# Copyright 2026 Transcription Module contributors

"""Concurrent canonical candidate-attempt CLI regressions."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TYPE_CHECKING

from tests.translation.canonical_attempt_support import (
    attempt_binding,
    attempt_document,
    successful_attempt,
)
from translation_module.canonical import runtime as canonical_runtime
from translation_module.canonical._attempt_store_io import locked_attempt_namespace
from translation_module.canonical._selected_store import (
    persist_candidate_in_namespace,
)
from translation_module.canonical.attempts import (
    AttemptLedgerConflictError,
    TranslationAttempt,
    TranslationAttemptKind,
    TranslationAttemptOutcome,
    TranslationAttemptStore,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.runtime import CanonicalTranslationWorkflow

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    import pytest

    from translation_module.canonical._authority_namespace import AuthorityNamespace
    from translation_module.canonical.translation import TranslationDocumentV2


def test_concurrent_distinct_candidates_leave_no_losing_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent admission persists only the candidate whose attempt wins."""
    # Given
    first = attempt_document()
    cue = first.cues[0].model_copy(
        update={"text": "Different first line.\nDifferent second line."}
    )
    second = bind_canonical_digests(
        first.model_copy(
            update={
                "revision_id": f"{first.revision_id}:concurrent",
                "cues": (cue,),
            }
        )
    )
    root = tmp_path / "authority"
    workflow = CanonicalTranslationWorkflow(root)
    barrier = threading.Barrier(2)
    entered: set[int] = set()
    entered_lock = threading.Lock()
    original = TranslationAttemptStore.validate_append

    def synchronized_admission(
        store: TranslationAttemptStore,
        attempt: TranslationAttempt,
        document: TranslationDocumentV2,
    ) -> TranslationAttempt:
        thread_id = threading.get_ident()
        with entered_lock:
            first_entry = thread_id not in entered
            entered.add(thread_id)
        if first_entry:
            _ = barrier.wait(timeout=5)
        return original(store, attempt, document)

    monkeypatch.setattr(
        TranslationAttemptStore,
        "validate_append",
        synchronized_admission,
    )

    def record(
        attempt: TranslationAttempt,
        document: TranslationDocumentV2,
    ) -> str:
        try:
            _ = workflow.record_attempt(attempt, document)
        except AttemptLedgerConflictError:
            return "conflict"
        return "succeeded"

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(record, successful_attempt(first, "attempt-a"), first),
            executor.submit(record, successful_attempt(second, "attempt-b"), second),
        )
        outcomes = tuple(future.result(timeout=10) for future in futures)

    # Then
    assert sorted(outcomes) == ["conflict", "succeeded"]
    assert len(tuple(root.glob("translation-candidate.*.json"))) == 1
    assert len(workflow.attempt_store.load()) == 1


def test_successful_and_failed_recording_leave_no_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent failed attempt cannot steal admission after persistence."""
    # Given
    root = tmp_path / "authority"
    workflow = CanonicalTranslationWorkflow(root)
    document = attempt_document()
    successful = successful_attempt(document, "successful")
    failed = TranslationAttempt(
        attempt_id="failed",
        ordinal=1,
        kind=TranslationAttemptKind.INITIAL,
        outcome=TranslationAttemptOutcome.FAILED,
        binding=attempt_binding(document),
    )
    success_reached_persist, release_success = (
        threading.Event(),
        threading.Event(),
    )
    rendezvous: queue.Queue[str] = queue.Queue()
    role = threading.local()
    real_persist, real_root_lock, real_append_failed = (
        persist_candidate_in_namespace,
        locked_attempt_namespace,
        TranslationAttemptStore.append_failed,
    )

    def paused_persist(
        namespace: AuthorityNamespace,
        candidate: TranslationDocumentV2,
    ) -> Path:
        success_reached_persist.set()
        assert release_success.wait(timeout=5)
        return real_persist(namespace, candidate)

    @contextmanager
    def observed_root_lock(
        namespace: AuthorityNamespace,
    ) -> Generator[AuthorityNamespace]:
        if getattr(role, "value", None) == "failed":
            rendezvous.put("failed-requested-root-lock")
        with real_root_lock(namespace):
            yield namespace

    def observed_append_failed(
        store: TranslationAttemptStore,
        attempt: TranslationAttempt,
    ) -> TranslationAttempt:
        result = real_append_failed(store, attempt)
        if getattr(role, "value", None) == "failed":
            rendezvous.put("failed-ledger-committed")
        return result

    monkeypatch.setattr(
        canonical_runtime,
        "persist_candidate_in_namespace",
        paused_persist,
    )
    monkeypatch.setattr(
        canonical_runtime,
        "locked_attempt_namespace",
        observed_root_lock,
    )
    monkeypatch.setattr(
        TranslationAttemptStore,
        "append_failed",
        observed_append_failed,
    )

    def record_success() -> str:
        role.value = "success"
        try:
            _ = workflow.record_attempt(successful, document)
        except AttemptLedgerConflictError:
            return "conflict"
        return "succeeded"

    def record_failure() -> str:
        role.value = "failed"
        try:
            _ = workflow.record_failed_attempt(failed)
        except AttemptLedgerConflictError:
            return "conflict"
        return "succeeded"

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        success_future = executor.submit(record_success)
        assert success_reached_persist.wait(timeout=5)
        failed_future = executor.submit(record_failure)
        phase = rendezvous.get(timeout=5)
        assert phase in {
            "failed-ledger-committed",
            "failed-requested-root-lock",
        }
        release_success.set()
        success_outcome = success_future.result(timeout=5)
        failed_outcome = failed_future.result(timeout=5)

    # Then
    attempts, candidates = (
        workflow.attempt_store.load(),
        tuple(root.glob("translation-candidate.*.json")),
    )
    assert success_outcome == "succeeded"
    assert failed_outcome == "conflict"
    assert tuple((item.attempt_id, item.outcome) for item in attempts) == (
        ("successful", TranslationAttemptOutcome.SUCCEEDED),
    )
    assert len(candidates) == 1
