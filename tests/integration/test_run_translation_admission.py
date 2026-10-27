# Copyright 2026 Transcription Module contributors

"""Run-owned finalized source translation admission tests."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal

import pytest

from tests.integration.source_edit_lifecycle_support import (
    CLI_ERROR_EXIT,
    runner,
    source_edit_fixture,
)
from transcription_module.cli import app
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.source_edit_lifecycle import (
    finalize_source_edit,
    start_source_edit,
)
from transcription_module.storage import write_model_atomic
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.digest import (
    bind_canonical_digests,
    verify_canonical_digests,
)
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.source import SourceFinalization, SourceTranscriptV2

if TYPE_CHECKING:
    from typer.testing import Result

    from transcription_module.models import RunId

type LegacyExporter = Literal["json", "ass"]


def _admission_arguments(
    run_id: RunId,
    runs_dir: Path,
    output_dir: Path,
) -> list[str]:
    return [
        "prepare-run-translation-assets",
        str(run_id),
        "--output-dir",
        str(output_dir),
        "--runs-dir",
        str(runs_dir),
    ]


def _assert_admission_failed_without_outputs(
    result: Result,
    output_dir: Path,
) -> None:
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.TRANSLATION_ADMISSION_FAILED
    assert not (output_dir / "context.prepared.json").exists()
    assert not (output_dir / "glossary.json").exists()


def test_prepare_run_translation_assets_binds_only_finalized_human_source(
    tmp_path: Path,
) -> None:
    """The run command resolves the exact final source without a source option."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    finalized = finalize_source_edit(
        store,
        run_id,
        SourceFinalization(
            finalized_at="2026-08-26T12:00:00Z",
            finalized_by="human-editor",
        ),
    )
    output_dir = tmp_path / "translation-assets"

    # When
    result = runner.invoke(
        app,
        _admission_arguments(run_id, store.runs_dir, output_dir),
    )

    # Then
    assert result.exit_code == 0, result.output
    context = ContentContextAsset.model_validate_json(
        (output_dir / "context.prepared.json").read_bytes()
    )
    glossary = TranslationGlossaryAsset.model_validate_json(
        (output_dir / "glossary.json").read_bytes()
    )
    verify_canonical_digests(context)
    verify_canonical_digests(glossary)
    expected_locator = os.path.relpath(
        store.paths(run_id).source_final,
        start=output_dir,
    )
    context_ref = context.prepared_from_source_ref
    assert context_ref is not None
    assert context_ref.document_id == finalized.document_id
    assert context_ref.revision_id == finalized.revision_id
    assert context_ref.content_digest == finalized.content_digest
    assert context_ref.path == expected_locator
    assert glossary.prepared_from_source_ref.document_id == finalized.document_id
    assert glossary.prepared_from_source_ref.revision_id == finalized.revision_id
    assert glossary.prepared_from_source_ref.content_digest == finalized.content_digest


def test_prepare_run_translation_assets_rejects_missing_final_source(
    tmp_path: Path,
) -> None:
    """A human-edit draft alone cannot enter translation preparation."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    output_dir = tmp_path / "translation-assets"

    # When
    result = runner.invoke(
        app,
        _admission_arguments(run_id, store.runs_dir, output_dir),
    )

    # Then
    _assert_admission_failed_without_outputs(result, output_dir)


def test_prepare_run_translation_assets_rejects_draft_at_final_path(
    tmp_path: Path,
) -> None:
    """The stable final path cannot make a draft source admissible."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    paths = store.paths(run_id)
    draft = SourceTranscriptV2.model_validate_json(
        paths.source_human_edit.read_bytes()
    )
    write_model_atomic(paths.source_final, draft)
    output_dir = tmp_path / "translation-assets"

    # When
    result = runner.invoke(
        app,
        _admission_arguments(run_id, store.runs_dir, output_dir),
    )

    # Then
    _assert_admission_failed_without_outputs(result, output_dir)


def test_prepare_run_translation_assets_rejects_stale_final_digest(
    tmp_path: Path,
) -> None:
    """A stale finalized digest fails before either prepared output is written."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    _ = finalize_source_edit(
        store,
        run_id,
        SourceFinalization(
            finalized_at="2026-08-26T12:00:00Z",
            finalized_by="human-editor",
        ),
    )
    paths = store.paths(run_id)
    finalized = SourceTranscriptV2.model_validate_json(paths.source_final.read_bytes())
    changed_cue = finalized.cues[0].model_copy(update={"text": "stale digest"})
    stale = finalized.model_copy(
        update={"cues": (changed_cue, *finalized.cues[1:])}
    )
    write_model_atomic(paths.source_final, stale)
    output_dir = tmp_path / "translation-assets"

    # When
    result = runner.invoke(
        app,
        _admission_arguments(run_id, store.runs_dir, output_dir),
    )

    # Then
    _assert_admission_failed_without_outputs(result, output_dir)


def test_prepare_run_translation_assets_rejects_wrong_input_context(
    tmp_path: Path,
) -> None:
    """A replaced input context cannot satisfy the finalized source binding."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    _ = finalize_source_edit(
        store,
        run_id,
        SourceFinalization(
            finalized_at="2026-08-26T12:00:00Z",
            finalized_by="human-editor",
        ),
    )
    paths = store.paths(run_id)
    context = ContentContextAsset.model_validate_json(paths.input_context.read_bytes())
    wrong_context = bind_canonical_digests(
        context.model_copy(update={"revision_id": "wrong-input-context"})
    )
    write_model_atomic(paths.input_context, wrong_context)
    output_dir = tmp_path / "translation-assets"

    # When
    result = runner.invoke(
        app,
        _admission_arguments(run_id, store.runs_dir, output_dir),
    )

    # Then
    _assert_admission_failed_without_outputs(result, output_dir)


@pytest.mark.parametrize("exporter", ["json", "ass"])
def test_legacy_run_exporters_are_blocked_after_source_edit_starts(
    tmp_path: Path,
    exporter: LegacyExporter,
) -> None:
    """Legacy run-owned source paths cannot bypass the human edit."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    output = tmp_path / f"legacy-{exporter}.json"
    commands: dict[LegacyExporter, list[str]] = {
        "json": [
            "export-translation-source",
            str(run_id),
            "--output",
            str(output),
            "--runs-dir",
            str(store.runs_dir),
        ],
        "ass": [
            "export-ass-translation-source",
            str(run_id),
            "--expected-ass-sha256",
            "0" * 64,
            "--output",
            str(output),
            "--runs-dir",
            str(store.runs_dir),
        ],
    }

    # When
    result = runner.invoke(app, commands[exporter])

    # Then
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
    assert not output.exists()


def test_prepare_run_translation_assets_accepts_symlinked_absolute_root(
    tmp_path: Path,
) -> None:
    """A `/tmp` locator is normalized before authority-root publication."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    _ = finalize_source_edit(
        store,
        run_id,
        SourceFinalization(
            finalized_at="2026-08-26T12:00:00Z",
            finalized_by="human-editor",
        ),
    )

    # When
    with TemporaryDirectory(prefix="run-admission-", dir="/tmp") as root:
        output_dir = Path(root) / "assets"
        result = runner.invoke(
            app,
            _admission_arguments(run_id, store.runs_dir, output_dir),
        )

        # Then
        assert result.exit_code == 0, result.output
        assert (output_dir / "context.prepared.json").is_file()
        assert (output_dir / "glossary.json").is_file()
