# Copyright 2026 Transcription Module contributors

"""Canonical v2 validation through the real translation CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from tests.translation.canonical_validate_cli_support import (
    CLI_ERROR_EXIT,
    run_validate,
    write_artifact,
)
from tests.translation.test_canonical_validation_state import (
    finalized_dependencies_fixture,
    translation_for_dependencies_fixture,
)
from translation_module.canonical.digest import (
    bind_canonical_digests,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_validate_accepts_canonical_v2_dependency_graph(
    tmp_path: Path,
) -> None:
    """The advertised validate command must not route v2 JSON through v1."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(
        source,
        context,
        glossary,
    )
    assert translation.source_ref.path is not None
    assert translation.prepared_context_ref.path is not None
    assert translation.glossary_ref.path is not None
    translation_path = tmp_path / "project" / "translation.json"
    source_path = translation_path.parent / translation.source_ref.path
    context_path = translation_path.parent / translation.prepared_context_ref.path
    glossary_path = translation_path.parent / translation.glossary_ref.path
    write_artifact(source_path, source)
    write_artifact(context_path, context)
    write_artifact(glossary_path, glossary)
    write_artifact(translation_path, translation)

    result = run_validate(source_path, translation_path)

    assert result.returncode == 0, result.stderr
    payload = cast("dict[str, object]", json.loads(result.stdout))
    assert payload["schema_version"] == "translation-document.v2"
    assert payload["valid"] is True
    assert payload["blocking"] is False
    assert payload["diagnostics"] == [
        {
            "code": "review_not_run",
            "severity": "warning",
            "message": "no review artifact exists for this translation",
            "source_cue_ids": [],
            "target_cue_ids": [],
        }
    ]


def test_validate_rejects_stale_canonical_source_binding(
    tmp_path: Path,
) -> None:
    """A valid but different source revision cannot satisfy the document."""
    source, context, glossary = finalized_dependencies_fixture()
    translation = translation_for_dependencies_fixture(
        source,
        context,
        glossary,
    )
    assert translation.source_ref.path is not None
    assert translation.prepared_context_ref.path is not None
    assert translation.glossary_ref.path is not None
    translation_path = tmp_path / "project" / "translation.json"
    source_path = translation_path.parent / translation.source_ref.path
    stale_source = bind_canonical_digests(
        source.model_copy(
            update={
                "revision_id": f"{source.revision_id}:stale",
                "content_digest": "0" * 64,
                "record_digest": "0" * 64,
            }
        )
    )
    write_artifact(source_path, stale_source)
    write_artifact(
        translation_path.parent / translation.prepared_context_ref.path,
        context,
    )
    write_artifact(
        translation_path.parent / translation.glossary_ref.path,
        glossary,
    )
    write_artifact(translation_path, translation)

    result = run_validate(source_path, translation_path)

    assert result.returncode == CLI_ERROR_EXIT
    assert '"code":"validation_failed"' in result.stderr
    assert "source reference" in result.stderr
