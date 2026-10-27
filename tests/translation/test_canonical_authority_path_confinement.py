# Copyright 2026 Transcription Module contributors

"""Canonical finalization authority path confinement."""

from pathlib import Path

import pytest

from tests.translation.canonical_finalization_test_support import (
    finalized_dependencies_fixture,
    review_for_fixture,
    selected_authority_fixture,
    translation_finalization_fixture,
    translation_for_dependencies_fixture,
    validation_environment_fixture,
)
from translation_module.canonical import (
    DocumentLifecycleStatus,
    ReviewAcknowledgement,
)
from translation_module.canonical._authority_namespace import AuthorityNamespace
from translation_module.canonical.attempts import CorruptAttemptLedgerError
from translation_module.canonical.finalization_authority import (
    finalize_translation_core as finalize_translation,
)


def test_finalization_rejects_symlink_roots_and_survives_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalization binds one no-follow root descriptor for its full reload."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(source, context, glossary)
    review = review_for_fixture(translation, source, context, glossary)
    environment = validation_environment_fixture(
        source, context, glossary, reviews=(review,)
    )
    finalization = translation_finalization_fixture(ReviewAcknowledgement.PASSED)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    real_root = real_parent / "authority"
    coordinates = selected_authority_fixture(real_root, translation, source, glossary)
    _ = (real_root / "outside-marker").write_text("unchanged", encoding="utf-8")

    final_link = tmp_path / "authority-link"
    final_link.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(CorruptAttemptLedgerError, match="unsafe authority root"):
        _ = finalize_translation(
            final_link,
            translation.document_id,
            translation.revision_id,
            finalization,
            environment,
        )

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(CorruptAttemptLedgerError, match="unsafe authority root"):
        _ = finalize_translation(
            parent_link / "authority",
            translation.document_id,
            translation.revision_id,
            finalization,
            environment,
        )

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
    finalized = finalize_translation(*coordinates, finalization, environment)
    assert finalized.lifecycle_status is DocumentLifecycleStatus.FINALIZED
    assert (real_root / "replacement-marker").read_text(encoding="utf-8") == ("outside")
    assert (moved_root / "outside-marker").read_text(encoding="utf-8") == ("unchanged")
