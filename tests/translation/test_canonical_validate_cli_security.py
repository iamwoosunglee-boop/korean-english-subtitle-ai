# Copyright 2026 Transcription Module contributors

"""Fail-closed canonical validation CLI cases."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.translation.canonical_validate_cli_support import (
    CLI_ERROR_EXIT,
    run_validate,
    write_artifact,
)
from tests.translation.test_canonical_validation_state import (
    finalized_dependencies_fixture,
    translation_for_dependencies_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical.common import CanonicalModel


def test_validate_rejects_non_object_source_json(tmp_path: Path) -> None:
    """Schema dispatch reports typed invalid_document instead of traceback."""
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "translation.json"
    _ = source_path.write_text("[]", encoding="utf-8")

    result = run_validate(source_path, document_path)

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"invalid_document"' in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_rejects_invalid_utf8_source_json(tmp_path: Path) -> None:
    """Malformed bytes remain inside the machine-readable error boundary."""
    source_path = tmp_path / "source.json"
    document_path = tmp_path / "translation.json"
    _ = source_path.write_bytes(b"\xff")

    result = run_validate(source_path, document_path)

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"invalid_document"' in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("corrupted_name", ["source", "context", "glossary"])
def test_validate_rejects_corrupted_canonical_dependency_digest(
    tmp_path: Path,
    corrupted_name: str,
) -> None:
    """Every loaded dependency must verify its own canonical digests."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(
        source,
        context,
        glossary,
    )
    assert translation.source_ref.path is not None
    assert translation.prepared_context_ref.path is not None
    assert translation.glossary_ref.path is not None
    artifacts: dict[str, CanonicalModel] = {
        "source": source.model_copy(
            update={
                "cues": (
                    source.cues[0].model_copy(
                        update={"text": f"{source.cues[0].text} tampered"}
                    ),
                    *source.cues[1:],
                )
            }
        ),
        "context": context.model_copy(
            update={
                "facts": (
                    context.facts[0].model_copy(
                        update={"statement": (f"{context.facts[0].statement} tampered")}
                    ),
                    *context.facts[1:],
                )
            }
        ),
        "glossary": glossary.model_copy(
            update={
                "entries": (
                    glossary.entries[0].model_copy(
                        update={
                            "preferred_target": (
                                f"{glossary.entries[0].preferred_target} tampered"
                            )
                        }
                    ),
                    *glossary.entries[1:],
                )
            }
        ),
    }
    document_path = tmp_path / "project" / "translation.json"
    source_path = document_path.parent / translation.source_ref.path
    context_path = document_path.parent / translation.prepared_context_ref.path
    glossary_path = document_path.parent / translation.glossary_ref.path
    write_artifact(
        source_path,
        artifacts["source"] if corrupted_name == "source" else source,
    )
    write_artifact(
        context_path,
        artifacts["context"] if corrupted_name == "context" else context,
    )
    write_artifact(
        glossary_path,
        artifacts["glossary"] if corrupted_name == "glossary" else glossary,
    )
    write_artifact(document_path, translation)

    result = run_validate(source_path, document_path)

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"validation_failed"' in result.stderr
    assert "digest" in result.stderr


def test_validate_rejects_symlinked_canonical_dependency(
    tmp_path: Path,
) -> None:
    """Document references cannot bypass no-follow input reads."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(
        source,
        context,
        glossary,
    )
    assert translation.source_ref.path is not None
    assert translation.prepared_context_ref.path is not None
    assert translation.glossary_ref.path is not None
    document_path = tmp_path / "project" / "translation.json"
    source_path = document_path.parent / translation.source_ref.path
    context_path = document_path.parent / translation.prepared_context_ref.path
    glossary_path = document_path.parent / translation.glossary_ref.path
    victim = tmp_path / "victim-context.json"
    write_artifact(source_path, source)
    write_artifact(victim, context)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.symlink_to(victim)
    write_artifact(glossary_path, glossary)
    write_artifact(document_path, translation)

    result = run_validate(source_path, document_path)

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"validation_failed"' in result.stderr
