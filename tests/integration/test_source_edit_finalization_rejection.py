# Copyright 2026 Transcription Module contributors

"""Run-owned source finalization rejection tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.integration.source_edit_lifecycle_support import (
    CLI_ERROR_EXIT,
    runner,
    source_edit_fixture,
)
from transcription_module.cli import app
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.source_edit_lifecycle import start_source_edit
from transcription_module.storage import write_model_atomic
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.source import (
    SourceFinalization,
    SourceTranscriptV2,
)
from translation_module.canonical.validation import finalize_source

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.models import RunId
    from transcription_module.storage import RunStore
    from transcription_module.storage_paths import RunPaths


def _started_fixture(tmp_path: Path) -> tuple[RunStore, RunId, RunPaths]:
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    _ = start_source_edit(store, run_id)
    return store, run_id, store.paths(run_id)


def _assert_finalization_rejected(
    store: RunStore,
    run_id: RunId,
    paths: RunPaths,
) -> None:
    result = runner.invoke(
        app,
        [
            "finalize-source-edit",
            str(run_id),
            "--finalized-at",
            "2026-08-26T12:00:00Z",
            "--finalized-by",
            "human-editor",
            "--runs-dir",
            str(store.runs_dir),
        ],
    )
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
    assert not paths.source_final.exists()


def test_finalize_source_edit_rejects_stale_child_digest(tmp_path: Path) -> None:
    """A stale child digest cannot publish a partial final artifact."""
    # Given
    store, run_id, paths = _started_fixture(tmp_path)
    child = SourceTranscriptV2.model_validate_json(paths.source_human_edit.read_bytes())
    changed_cue = child.cues[0].model_copy(update={"text": "stale digest"})
    stale = child.model_copy(update={"cues": (changed_cue, *child.cues[1:])})
    write_model_atomic(paths.source_human_edit, stale)

    # When / Then
    _assert_finalization_rejected(store, run_id, paths)


def test_finalize_source_edit_rejects_wrong_input_context(tmp_path: Path) -> None:
    """A replaced context cannot satisfy the assigned child binding."""
    # Given
    store, run_id, paths = _started_fixture(tmp_path)
    context = ContentContextAsset.model_validate_json(paths.input_context.read_bytes())
    wrong_context = bind_canonical_digests(
        context.model_copy(update={"revision_id": "wrong-input-context"})
    )
    write_model_atomic(paths.input_context, wrong_context)

    # When / Then
    _assert_finalization_rejected(store, run_id, paths)


def test_finalize_source_edit_rejects_non_draft_child(tmp_path: Path) -> None:
    """A pre-finalized child cannot be finalized through the run command."""
    # Given
    store, run_id, paths = _started_fixture(tmp_path)
    child = SourceTranscriptV2.model_validate_json(paths.source_human_edit.read_bytes())
    context = ContentContextAsset.model_validate_json(paths.input_context.read_bytes())
    finalized_child = finalize_source(
        child,
        SourceFinalization(
            finalized_at="2026-08-26T12:00:00Z",
            finalized_by="human-editor",
        ),
        input_context=context,
    )
    write_model_atomic(paths.source_human_edit, finalized_child)

    # When / Then
    _assert_finalization_rejected(store, run_id, paths)
