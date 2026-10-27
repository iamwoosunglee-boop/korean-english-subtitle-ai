# Copyright 2026 Transcription Module contributors

"""Historical multi-output rollback fault regressions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from translation_module._cli_io_namespace import BoundOutput
from translation_module._cli_io_wal import WAL_NAME
from translation_module.cli_io import write_models_atomic

if TYPE_CHECKING:
    from pathlib import Path


class Payload(BaseModel):
    """Serializable rollback fixture."""

    value: str


def test_backup_cleanup_fault_restores_both_originals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-commit durable-cleanup fault restores both original outputs."""
    request = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    _ = request.write_bytes(b"old request")
    _ = assignment.write_bytes(b"old assignment")
    trigger = OSError("cleanup")
    original = BoundOutput.unlink
    failed = False

    def fail_once(
        root: BoundOutput,
        name: str,
        *,
        missing_ok: bool = False,
    ) -> None:
        nonlocal failed
        if name == WAL_NAME and not failed:
            failed = True
            raise trigger
        original(root, name, missing_ok=missing_ok)

    monkeypatch.setattr(BoundOutput, "unlink", fail_once)
    with pytest.raises(OSError, match="cleanup") as caught:
        write_models_atomic(
            (
                (request, Payload(value="new request")),
                (assignment, Payload(value="new assignment")),
            )
        )
    assert caught.value is trigger
    assert request.read_bytes() == b"old request"
    assert assignment.read_bytes() == b"old assignment"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "assignment.json",
        "request.json",
    ]


def test_persistent_rollback_fault_preserves_trigger_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback failure preserves the trigger and a retryable durable WAL."""
    request = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    _ = request.write_bytes(b"old request")
    _ = assignment.write_bytes(b"old assignment")
    trigger = OSError("commit")
    original = BoundOutput.replace_name
    commit_failed = False

    def fail_commit_and_rollback(
        root: BoundOutput,
        source: str,
        destination: str,
    ) -> None:
        nonlocal commit_failed
        if destination == assignment.name and not commit_failed:
            commit_failed = True
            raise trigger
        if commit_failed and destination == request.name:
            message = "persistent rollback failure"
            raise OSError(message)
        original(root, source, destination)

    monkeypatch.setattr(BoundOutput, "replace_name", fail_commit_and_rollback)
    with pytest.raises(OSError, match="commit") as caught:
        write_models_atomic(
            (
                (request, Payload(value="new request")),
                (assignment, Payload(value="new assignment")),
            )
        )
    assert caught.value is trigger
    assert any("rollback failed" in note for note in caught.value.__notes__)
    assert (tmp_path / WAL_NAME).is_file()
    assert assignment.read_bytes() == b"old assignment"

    monkeypatch.setattr(BoundOutput, "replace_name", original)
    write_models_atomic(
        (
            (request, Payload(value="new request")),
            (assignment, Payload(value="new assignment")),
        )
    )
    assert Payload.model_validate_json(request.read_bytes()).value == "new request"
    restored = Payload.model_validate_json(assignment.read_bytes())
    assert restored.value == "new assignment"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "assignment.json",
        "request.json",
    ]
