# Copyright 2026 Transcription Module contributors

"""Run-owned canonical human-edit finalization tests."""

from __future__ import annotations

import fcntl
import multiprocessing
from typing import TYPE_CHECKING

from tests.integration.source_edit_lifecycle_support import (
    CLI_ERROR_EXIT,
    LockProbeChannels,
    finalize_after_lock_probe,
    runner,
    source_edit_fixture,
)
from transcription_module.cli import app
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.storage import write_model_atomic
from translation_module.canonical.common import DocumentLifecycleStatus
from translation_module.canonical.digest import (
    bind_canonical_digests,
    verify_canonical_digests,
)
from translation_module.canonical.source import SourceTranscriptV2

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.models import RunId
    from transcription_module.storage import RunStore


def _finalization_arguments(
    store: RunStore,
    run_id: RunId,
    *,
    finalized_by: str = "human-editor",
) -> list[str]:
    return [
        "finalize-source-edit",
        str(run_id),
        "--finalized-at",
        "2026-08-26T12:00:00Z",
        "--finalized-by",
        finalized_by,
        "--runs-dir",
        str(store.runs_dir),
    ]


def test_finalize_source_edit_publishes_exact_child_without_changing_history(
    tmp_path: Path,
) -> None:
    """The public command finalizes one assigned no-change draft create-once."""
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
    child = SourceTranscriptV2.model_validate_json(child_bytes)

    # When
    result = runner.invoke(app, _finalization_arguments(store, run_id))

    # Then
    assert result.exit_code == 0, result.output
    finalized = SourceTranscriptV2.model_validate_json(paths.source_final.read_bytes())
    verify_canonical_digests(finalized)
    assert finalized.lifecycle_status is DocumentLifecycleStatus.FINALIZED
    assert finalized.revision_id == child.revision_id
    assert finalized.content_digest == child.content_digest
    assert finalized.finalization is not None
    assert finalized.finalization.finalized_at == "2026-08-26T12:00:00Z"
    assert finalized.finalization.finalized_by == "human-editor"
    assert paths.canonical_source.read_bytes() == origin_bytes
    assert paths.source_human_edit.read_bytes() == child_bytes


def test_finalize_source_edit_reuses_identical_finalization(tmp_path: Path) -> None:
    """An identical finalization retry returns the create-once result."""
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
    arguments = _finalization_arguments(store, run_id)
    first = runner.invoke(app, arguments)
    assert first.exit_code == 0, first.output
    final_path = store.paths(run_id).source_final
    final_bytes = final_path.read_bytes()

    # When
    second = runner.invoke(app, arguments)

    # Then
    assert second.exit_code == 0, second.output
    assert second.stdout == first.stdout
    assert final_path.read_bytes() == final_bytes


def test_finalize_source_edit_rejects_different_finalization_identity(
    tmp_path: Path,
) -> None:
    """A create-once final cannot be rebound to another human identity."""
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
    first = runner.invoke(app, _finalization_arguments(store, run_id))
    assert first.exit_code == 0, first.output
    final_path = store.paths(run_id).source_final
    final_bytes = final_path.read_bytes()

    # When
    result = runner.invoke(
        app,
        _finalization_arguments(store, run_id, finalized_by="other-human"),
    )

    # Then
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
    assert final_path.read_bytes() == final_bytes


def test_finalize_source_edit_rejects_child_changed_after_finalization(
    tmp_path: Path,
) -> None:
    """A later draft save cannot replace an existing final source."""
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
    first = runner.invoke(app, _finalization_arguments(store, run_id))
    assert first.exit_code == 0, first.output
    paths = store.paths(run_id)
    final_bytes = paths.source_final.read_bytes()
    child = SourceTranscriptV2.model_validate_json(paths.source_human_edit.read_bytes())
    changed_cue = child.cues[0].model_copy(update={"text": "바뀐 최종 전사"})
    changed_child = bind_canonical_digests(
        child.model_copy(update={"cues": (changed_cue, *child.cues[1:])})
    )
    write_model_atomic(paths.source_human_edit, changed_child)

    # When
    result = runner.invoke(app, _finalization_arguments(store, run_id))

    # Then
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
    assert paths.source_final.read_bytes() == final_bytes
    assert paths.source_human_edit.read_bytes() != final_bytes


def test_finalize_source_edit_rejects_save_as_descendant(tmp_path: Path) -> None:
    """A descendant fork cannot replace the assigned direct child."""
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
    paths = store.paths(run_id)
    child = SourceTranscriptV2.model_validate_json(paths.source_human_edit.read_bytes())
    descendant = bind_canonical_digests(
        child.model_copy(
            update={
                "revision_id": "save-as-descendant",
                "supersedes_revision_id": child.revision_id,
            }
        )
    )
    write_model_atomic(paths.source_human_edit, descendant)

    # When
    result = runner.invoke(app, _finalization_arguments(store, run_id))

    # Then
    assert result.exit_code == CLI_ERROR_EXIT
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.SOURCE_EDIT_FAILED
    assert not paths.source_final.exists()


def test_finalize_source_edit_reads_child_after_publish_lock_release(
    tmp_path: Path,
) -> None:
    """Finalization waits for a writer and selects the bytes it publishes."""
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
    paths = store.paths(run_id)
    lock_path = paths.source_human_edit.with_name(
        f"{paths.source_human_edit.name}.publish.lock"
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    go = context.Event()
    blocked = context.Event()
    result_reader, result_writer = context.Pipe(duplex=False)
    channels = LockProbeChannels(
        ready=ready,
        go=go,
        blocked=blocked,
        results=result_writer,
    )
    process = context.Process(
        target=finalize_after_lock_probe,
        args=(
            str(store.runs_dir),
            str(run_id),
            channels,
        ),
    )

    # When
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        process.start()
        assert ready.wait(5)
        go.set()
        assert blocked.wait(5)
        child = SourceTranscriptV2.model_validate_json(
            paths.source_human_edit.read_bytes()
        )
        changed_cue = child.cues[0].model_copy(update={"text": "잠금 뒤 저장"})
        changed_child = bind_canonical_digests(
            child.model_copy(update={"cues": (changed_cue, *child.cues[1:])})
        )
        write_model_atomic(paths.source_human_edit, changed_child)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    process.join(10)

    # Then
    assert process.exitcode == 0
    assert result_reader.poll(1)
    assert result_reader.recv() == changed_child.content_digest
    finalized = SourceTranscriptV2.model_validate_json(paths.source_final.read_bytes())
    assert finalized.content_digest == changed_child.content_digest
