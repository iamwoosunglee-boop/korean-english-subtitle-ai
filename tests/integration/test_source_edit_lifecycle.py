# Copyright 2026 Transcription Module contributors

"""Run-owned canonical source human-edit lifecycle tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.source_edit_lifecycle_support import (
    CLI_ERROR_EXIT,
    InvalidChildFailure,
    runner,
    source_edit_fixture,
    write_invalid_child,
)
from transcription_module.cli import app
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.source_edit_errors import SourceEditLifecycleError
from transcription_module.source_edit_lifecycle import SourceEditStartResult
from transcription_module.validation import validate_run
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.source import SourceTranscriptV2

if TYPE_CHECKING:
    from pathlib import Path


def test_start_source_edit_creates_direct_child_without_changing_origin(
    tmp_path: Path,
) -> None:
    """The public command preserves the generated source and emits one child."""
    # Given
    store, run_id, source, origin_bytes, _ = source_edit_fixture(tmp_path)
    paths = store.paths(run_id)

    # When
    result = runner.invoke(
        app,
        [
            "start-source-edit",
            str(run_id),
            "--runs-dir",
            str(store.runs_dir),
        ],
    )

    # Then
    assert result.exit_code == 0, result.output
    child = SourceTranscriptV2.model_validate_json(
        (paths.run_dir / "source.human-edit.json").read_bytes()
    )
    verify_canonical_digests(child)
    assert paths.canonical_source.read_bytes() == origin_bytes
    assert child.document_id == source.document_id
    assert child.revision_id != source.revision_id
    assert child.supersedes_revision_id == source.revision_id
    assert child.content_digest == source.content_digest


def test_start_source_edit_reuses_the_exact_existing_child(tmp_path: Path) -> None:
    """A command retry returns the first child without allocating a revision."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    arguments = [
        "start-source-edit",
        str(run_id),
        "--runs-dir",
        str(store.runs_dir),
    ]
    first = runner.invoke(app, arguments)
    assert first.exit_code == 0, first.output
    child_path = store.paths(run_id).run_dir / "source.human-edit.json"
    child_bytes = child_path.read_bytes()

    # When
    second = runner.invoke(app, arguments)

    # Then
    assert second.exit_code == 0, second.output
    assert child_path.read_bytes() == child_bytes
    assert second.stdout == first.stdout


def test_start_source_edit_reports_active_path_and_next_action(tmp_path: Path) -> None:
    """The CLI exposes stable machine-consumed editing state."""
    # Given
    store, run_id, source, _, _ = source_edit_fixture(tmp_path)
    paths = store.paths(run_id)

    # When
    result = runner.invoke(
        app,
        [
            "start-source-edit",
            str(run_id),
            "--runs-dir",
            str(store.runs_dir),
        ],
    )

    # Then
    assert result.exit_code == 0, result.output
    payload = SourceEditStartResult.model_validate_json(result.stdout)
    assert payload.run_id == run_id
    assert payload.state == "editing"
    assert payload.next_action == "edit_source"
    assert payload.origin_path == str(paths.canonical_source)
    assert payload.source_path == str(paths.source_human_edit)
    assert payload.document_id == source.document_id
    assert payload.origin_revision_id == source.revision_id


@pytest.mark.parametrize(
    "failure",
    ["malformed", "wrong_document", "same_revision", "wrong_lineage"],
)
def test_start_source_edit_rejects_invalid_existing_child_without_changes(
    tmp_path: Path,
    failure: InvalidChildFailure,
) -> None:
    """An invalid occupied child path fails closed without replacing either file."""
    # Given
    store, run_id, origin, origin_bytes, _ = source_edit_fixture(tmp_path)
    arguments = [
        "start-source-edit",
        str(run_id),
        "--runs-dir",
        str(store.runs_dir),
    ]
    created = runner.invoke(app, arguments)
    assert created.exit_code == 0, created.output
    child_path = store.paths(run_id).run_dir / "source.human-edit.json"
    write_invalid_child(child_path, origin, failure)
    invalid_child_bytes = child_path.read_bytes()

    # When
    result = runner.invoke(app, arguments)

    # Then
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
    assert store.paths(run_id).canonical_source.read_bytes() == origin_bytes
    assert child_path.read_bytes() == invalid_child_bytes


def test_transcript_mutation_is_rejected_after_source_edit_starts(
    tmp_path: Path,
) -> None:
    """A working source freezes transcript mutation that would replace its origin."""
    # Given
    store, run_id, _, origin_bytes, transcript = source_edit_fixture(tmp_path)
    started = runner.invoke(
        app,
        [
            "start-source-edit",
            str(run_id),
            "--runs-dir",
            str(store.runs_dir),
        ],
    )
    assert started.exit_code == 0, started.output
    paths = store.paths(run_id)
    child_bytes = paths.source_human_edit.read_bytes()
    transcript_bytes = paths.transcript.read_bytes()
    changed_cue = transcript.cues[0].model_copy(update={"speech": "바뀐 전사"})
    changed = transcript.model_copy(
        update={"cues": (changed_cue, *transcript.cues[1:])}
    )

    # When / Then
    with pytest.raises(SourceEditLifecycleError):
        store.save_transcript(changed)
    assert paths.canonical_source.read_bytes() == origin_bytes
    assert paths.source_human_edit.read_bytes() == child_bytes
    assert paths.transcript.read_bytes() == transcript_bytes


def test_canonical_revalidation_is_rejected_after_source_edit_starts(
    tmp_path: Path,
) -> None:
    """Validation cannot regenerate the origin behind an active source edit."""
    # Given
    store, run_id, _, origin_bytes, _ = source_edit_fixture(tmp_path)
    started = runner.invoke(
        app,
        [
            "start-source-edit",
            str(run_id),
            "--runs-dir",
            str(store.runs_dir),
        ],
    )
    assert started.exit_code == 0, started.output
    paths = store.paths(run_id)
    child_bytes = paths.source_human_edit.read_bytes()

    # When / Then
    with pytest.raises(SourceEditLifecycleError):
        _ = validate_run(store, run_id)
    assert paths.canonical_source.read_bytes() == origin_bytes
    assert paths.source_human_edit.read_bytes() == child_bytes


def test_validate_cli_reports_typed_source_edit_failure(tmp_path: Path) -> None:
    """The public validation boundary renders the active lifecycle conflict."""
    # Given
    store, run_id, _, _, _ = source_edit_fixture(tmp_path)
    started = runner.invoke(
        app,
        [
            "start-source-edit",
            str(run_id),
            "--runs-dir",
            str(store.runs_dir),
        ],
    )
    assert started.exit_code == 0, started.output

    # When
    result = runner.invoke(
        app,
        ["validate", str(run_id), "--runs-dir", str(store.runs_dir)],
    )

    # Then
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
