# Copyright 2026 Transcription Module contributors

"""No-follow authority-root boundary tests for controlled canonical surfaces."""

from __future__ import annotations

import os
import stat
from inspect import signature
from typing import TYPE_CHECKING, cast

import pytest

from tests.e2e.canonical_workflow_translation_fixture_support import successful_attempt
from tests.e2e.canonical_workflow_translation_support import (
    checker_evidence,
    selection_receipt,
)
from tests.translation.canonical_finalization_test_support import (
    finalized_dependencies_fixture,
    review_for_fixture,
    translation_finalization_fixture,
    translation_for_dependencies_fixture,
    validation_environment_fixture,
)
from translation_module.canonical import bind_canonical_digests
from translation_module.canonical._authority_namespace import (
    AuthorityNamespace,
    open_authority_namespace,
)
from translation_module.canonical.attempts import CorruptAttemptLedgerError
from translation_module.canonical.common import DocumentLifecycleStatus
from translation_module.canonical.finalization_authority import (
    finalize_translation_core,
)
from translation_module.canonical.preflight import CandidatePreflightContext
from translation_module.canonical.review_authority import assemble_review_artifact_core
from translation_module.canonical.runtime import CanonicalTranslationWorkflow
from translation_module.canonical.translation import ReviewAcknowledgement

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from translation_module.canonical.translation import TranslationDocumentV2


def test_review_core_accepts_persistence_coordinates_not_capabilities() -> None:
    """The public review core exposes only root and immutable identifiers."""
    parameters = tuple(signature(assemble_review_artifact_core).parameters)
    assert parameters == ("root", "document_id", "revision_id", "inputs")


def test_created_root_and_attempts_fsync_each_owning_parent_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given missing authority directories, each mkdir is durably published."""
    # Given
    events: list[str] = []
    original_mkdir = os.mkdir
    original_fsync = os.fsync

    def record_mkdir(
        path: str,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        original_mkdir(path, mode, dir_fd=dir_fd)
        events.append(f"mkdir:{path}")

    def record_fsync(descriptor: int) -> None:
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
        events.append("fsync:owner")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "mkdir", record_mkdir)
    monkeypatch.setattr(os, "fsync", record_fsync)

    # When
    with (
        open_authority_namespace(tmp_path / "authority", create=True) as root,
        root.child("attempts", create=True),
    ):
        pass

    # Then
    assert events == [
        "mkdir:authority",
        "fsync:owner",
        "mkdir:attempts",
        "fsync:owner",
    ]


def test_created_namespace_fails_closed_when_owner_fsync_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given unsupported directory fsync, creation cannot report success."""
    # Given
    def reject_directory_fsync(_descriptor: int) -> None:
        message = "directory fsync unsupported"
        raise OSError(message)

    monkeypatch.setattr(os, "fsync", reject_directory_fsync)

    # When / Then
    with (
        pytest.raises(OSError, match="directory fsync unsupported"),
        open_authority_namespace(tmp_path / "authority", create=True),
    ):
        pass


def test_forged_authority_object_cannot_finalize() -> None:
    """Finalization cannot consume a caller-supplied no-op capability."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(source, context, glossary)
    review = review_for_fixture(translation, source, context, glossary)
    finalization = translation_finalization_fixture(ReviewAcknowledgement.PASSED)
    environment = validation_environment_fixture(
        source, context, glossary, reviews=(review,)
    )

    class ForgedAuthority:
        @staticmethod
        def require_current() -> str:
            return "forged"

    forged = cast("Callable[..., TranslationDocumentV2]", finalize_translation_core)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        _ = forged(
            ForgedAuthority(),
            translation.document_id,
            translation.revision_id,
            finalization,
            environment,
        )


def test_finalization_root_boundary_rejects_links_and_binds_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root traversal cannot redirect any selected-candidate reload read."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(source, context, glossary)
    review = review_for_fixture(translation, source, context, glossary)
    finalization = translation_finalization_fixture(ReviewAcknowledgement.PASSED)
    environment = validation_environment_fixture(
        source, context, glossary, reviews=(review,)
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    real_root = real_parent / "authority"
    workflow = CanonicalTranslationWorkflow(real_root)
    attempt = successful_attempt(translation, "namespace-attempt", 1)
    _ = workflow.record_attempt(attempt, translation)
    receipt = selection_receipt(translation, attempt.attempt_id)
    workflow.preflight_and_authorize(
        CandidatePreflightContext(translation, source, glossary),
        checker_evidence(translation),
        receipt,
    )
    review = bind_canonical_digests(
        review.model_copy(
            update={
                "binding": review.binding.model_copy(
                    update={
                        "selection_id": receipt.selection_id,
                        "attempt_id": receipt.attempt_id,
                    }
                ),
                "content_digest": "0" * 64,
                "record_digest": "0" * 64,
            },
        )
    )
    environment = validation_environment_fixture(
        source,
        context,
        glossary,
        reviews=(review,),
    )
    _ = (real_root / "outside-marker").write_text("unchanged", encoding="utf-8")

    final_link = tmp_path / "authority-link"
    final_link.symlink_to(real_root, target_is_directory=True)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    for unsafe_root in (final_link, parent_link / "authority"):
        with pytest.raises(CorruptAttemptLedgerError, match="unsafe authority root"):
            _ = finalize_translation_core(
                unsafe_root,
                translation.document_id,
                translation.revision_id,
                finalization,
                environment,
            )

    with pytest.raises(CorruptAttemptLedgerError, match="parent traversal"):
        _ = finalize_translation_core(
            real_root / ".." / "authority",
            translation.document_id,
            translation.revision_id,
            finalization,
            environment,
        )
    non_directory = tmp_path / "authority-file"
    _ = non_directory.write_text("outside", encoding="utf-8")
    with pytest.raises(CorruptAttemptLedgerError, match="unsafe authority root"):
        _ = finalize_translation_core(
            non_directory,
            translation.document_id,
            translation.revision_id,
            finalization,
            environment,
        )
    assert non_directory.read_text(encoding="utf-8") == "outside"

    moved_root = real_parent / "authority-moved"
    original_read = AuthorityNamespace.read_regular
    swapped = False

    def swap_then_read(namespace: AuthorityNamespace, name: str) -> bytes:
        nonlocal swapped
        if name.startswith("translation-candidate.") and not swapped:
            swapped = True
            _ = real_root.rename(moved_root)
            real_root.mkdir()
            _ = (real_root / "replacement-marker").write_text(
                "outside", encoding="utf-8"
            )
        return original_read(namespace, name)

    monkeypatch.setattr(AuthorityNamespace, "read_regular", swap_then_read)
    finalized = finalize_translation_core(
        real_root,
        translation.document_id,
        translation.revision_id,
        finalization,
        environment,
    )
    assert finalized.lifecycle_status is DocumentLifecycleStatus.FINALIZED
    assert (real_root / "replacement-marker").read_text(encoding="utf-8") == ("outside")
    assert (moved_root / "outside-marker").read_text(encoding="utf-8") == ("unchanged")
