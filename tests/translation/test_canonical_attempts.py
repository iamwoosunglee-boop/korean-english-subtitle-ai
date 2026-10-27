# Copyright 2026 Transcription Module contributors

"""Append-only canonical translation attempt and selection tests."""

import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.translation.canonical_attempt_support import (
    attempt_binding,
    attempt_document,
    failed_attempt,
    successful_attempt,
)
from translation_module.canonical.attempts import (
    AttemptBindingError,
    AttemptCoverageError,
    AttemptLedgerConflictError,
    CorruptAttemptLedgerError,
    TranslationAttempt,
    TranslationAttemptKind,
    TranslationAttemptOutcome,
    TranslationAttemptStore,
    TranslationLineRationale,
    translation_line_text_digest,
)
from translation_module.canonical.digest import bind_canonical_digests


def test_attempt_models_are_strict_frozen_and_outcome_specific() -> None:
    """Malformed, mutable, and outcome-inconsistent sidecars reject."""
    document = attempt_document()
    attempt = successful_attempt(document)
    payload = attempt.model_dump(mode="json", exclude_none=True)
    payload["future_field"] = "rejected"

    with pytest.raises(ValidationError, match="future_field"):
        _ = TranslationAttempt.model_validate(payload)
    with pytest.raises(ValidationError, match="frozen"):
        attempt.ordinal = 2
    with pytest.raises(ValidationError, match="candidate"):
        _ = TranslationAttempt(
            attempt_id="failed-with-candidate",
            ordinal=1,
            kind=TranslationAttemptKind.RETRY,
            outcome=TranslationAttemptOutcome.FAILED,
            binding=attempt_binding(document),
            candidate_digest=document.content_digest,
        )
    with pytest.raises(ValidationError, match="Korean"):
        _ = TranslationLineRationale(
            target_cue_id="target:000001",
            line_number=1,
            speaker_id="speaker-a",
            line_text_digest="a" * 64,
            rationale_ko="English only",
        )


def test_append_preserves_prefix_and_rejects_duplicate_identity(tmp_path: Path) -> None:
    """Each accepted append retains its exact trusted predecessor bytes."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    first = successful_attempt(document)
    second = successful_attempt(document, "attempt-b", 2).model_copy(
        update={"kind": TranslationAttemptKind.ALTERNATIVE}
    )
    _ = store.append(first, document)
    prefix = store.ledger_path.read_bytes()

    _ = store.append(second, document)

    assert store.ledger_path.read_bytes().startswith(prefix)
    assert tuple(item.ordinal for item in store.load()) == (1, 2)
    stable = store.ledger_path.read_bytes()
    with pytest.raises(AttemptLedgerConflictError, match="attempt ID"):
        _ = store.append(successful_attempt(document, "attempt-a", 3), document)
    with pytest.raises(AttemptLedgerConflictError, match="ordinal"):
        _ = store.append(successful_attempt(document, "attempt-d", 2), document)
    assert store.ledger_path.read_bytes() == stable


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("missing-line", "order"),
        ("duplicate-line", "order"),
        ("foreign-speaker", "speaker"),
        ("stale-text", "text digest"),
    ],
)
def test_successful_attempt_requires_exact_explicit_line_coverage(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    """Every displayed line has one ordered explicit speaker rationale."""
    document = attempt_document()
    attempt = successful_attempt(document)
    assert attempt.rationales is not None
    rationales = list(attempt.rationales)
    if mutation == "missing-line":
        _ = rationales.pop()
    elif mutation == "duplicate-line":
        rationales[1] = rationales[0]
    elif mutation == "foreign-speaker":
        rationales[1] = rationales[1].model_copy(update={"speaker_id": "intruder"})
    elif mutation == "stale-text":
        rationales[1] = rationales[1].model_copy(update={"line_text_digest": "f" * 64})
    malformed = attempt.model_copy(update={"rationales": tuple(rationales)})

    with pytest.raises(AttemptCoverageError, match=match):
        _ = TranslationAttemptStore(tmp_path / "store").append(malformed, document)


def test_joint_speaker_target_requires_one_displayed_line_rationale(
    tmp_path: Path,
) -> None:
    """One line shared by multiple speakers remains one reviewable utterance."""
    document = attempt_document()
    cue = document.cues[0].model_copy(
        update={"text": "Gaurim!", "speakers": ("오태우", "박도현")}
    )
    document = bind_canonical_digests(document.model_copy(update={"cues": (cue,)}))
    attempt = TranslationAttempt(
        attempt_id="joint-attempt",
        ordinal=1,
        kind=TranslationAttemptKind.INITIAL,
        outcome=TranslationAttemptOutcome.SUCCEEDED,
        binding=attempt_binding(document),
        candidate_digest=document.content_digest,
        rationales=(
            TranslationLineRationale(
                target_cue_id=cue.cue_id,
                line_number=1,
                speaker_id=cue.speakers[0],
                line_text_digest=translation_line_text_digest(cue.text),
                rationale_ko="두 화자가 함께 외친 한 발화를 한 줄로 보존했습니다.",
            ),
        ),
    )

    stored = TranslationAttemptStore(tmp_path / "store").append(attempt, document)

    assert stored.attempt_id == "joint-attempt"


def test_append_rejects_stale_candidate_and_dependency_bindings(tmp_path: Path) -> None:
    """Candidate and dependency digests bind against the supplied document."""
    document = attempt_document()
    stale_candidate = successful_attempt(document).model_copy(
        update={"candidate_digest": "f" * 64}
    )
    stale_binding = successful_attempt(document).model_copy(
        update={
            "binding": attempt_binding(document).model_copy(
                update={"glossary_content_digest": "f" * 64}
            )
        }
    )
    store = TranslationAttemptStore(tmp_path / "store")

    with pytest.raises(AttemptBindingError, match="candidate"):
        _ = store.append(stale_candidate, document)
    with pytest.raises(AttemptBindingError, match="dependency binding"):
        _ = store.append(stale_binding, document)


def test_failed_attempt_appends_without_candidate_or_rationale(tmp_path: Path) -> None:
    """Failed attempts preserve lineage without claiming candidate coverage."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    _ = store.append(successful_attempt(document), document)
    _ = store.append(successful_attempt(document, "attempt-b", 2), document)

    _ = store.append(failed_attempt(document), document)

    payload = store.load()[-1].model_dump(mode="json", exclude_none=True)
    assert {"candidate_digest", "rationales"}.isdisjoint(payload)


def test_reversed_rationale_tuple_rejects_ordered_coverage(tmp_path: Path) -> None:
    """Coverage order must exactly follow cue and displayed-line order."""
    document = attempt_document()
    attempt = successful_attempt(document)
    assert attempt.rationales is not None
    reversed_attempt = attempt.model_copy(
        update={"rationales": tuple(reversed(attempt.rationales))}
    )

    with pytest.raises(AttemptCoverageError, match="order"):
        _ = TranslationAttemptStore(tmp_path / "store").append(
            reversed_attempt, document
        )


def test_structurally_valid_prefix_rewrite_rejects_append(tmp_path: Path) -> None:
    """Persisted authority detects byte rewrites before extending the ledger."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    _ = store.append(successful_attempt(document), document)
    _ = store.ledger_path.write_bytes(b" " + store.ledger_path.read_bytes())

    with pytest.raises(CorruptAttemptLedgerError, match="integrity"):
        _ = store.append(successful_attempt(document, "attempt-b", 2), document)


def test_internally_stale_document_digest_rejects_append(tmp_path: Path) -> None:
    """Stored document self-digests are verified before binding attempts."""
    stale = attempt_document().model_copy(update={"content_digest": "f" * 64})
    attempt = successful_attempt(stale)

    with pytest.raises(AttemptBindingError, match="digest"):
        _ = TranslationAttemptStore(tmp_path / "store").append(attempt, stale)


def test_ledger_post_rename_interruption_recovers_one_committed_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry recognizes committed success or restores a clean append position."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    _ = store.append(successful_attempt(document), document)
    expected = store.ledger_path
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
            _ = store.append(successful_attempt(document, "attempt-b", 2), document)
    _ = store.append(successful_attempt(document, "attempt-b", 2), document)

    assert tuple(item.attempt_id for item in store.load()) == (
        "attempt-a",
        "attempt-b",
    )


@pytest.mark.parametrize("failure_point", ["authority_replace", "directory_fsync"])
def test_post_replace_failure_rolls_back_and_retry_commits_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """Later authority/dir failures restore both files for one clean retry."""
    document = attempt_document()
    store = TranslationAttemptStore(tmp_path / "store")
    _ = store.append(successful_attempt(document), document)
    second = successful_attempt(document, "attempt-b", 2)

    with monkeypatch.context() as injected:
        calls = 0
        if failure_point == "authority_replace":
            original_replace = os.replace

            def fail_authority(
                source: str,
                target: str,
                *,
                src_dir_fd: int | None = None,
                dst_dir_fd: int | None = None,
            ) -> None:
                original_replace(
                    source,
                    target,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )
                if target == store.authority_path.name:
                    message = "injected post-replace authority failure"
                    raise OSError(message)

            injected.setattr(os, "replace", fail_authority)
        else:
            original_fsync = os.fsync

            def fail_directory(descriptor: int) -> None:
                nonlocal calls
                original_fsync(descriptor)
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    calls += 1
                    if calls == 1:
                        message = "injected post-replace directory failure"
                        raise OSError(message)

            injected.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="post-replace"):
            _ = store.append(second, document)

    _ = store.append(second, document)
    assert tuple(item.attempt_id for item in store.load()) == ("attempt-a", "attempt-b")
