# Copyright 2026 Transcription Module contributors

"""Descriptor publication ordering for transactional CLI outputs."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

from pydantic import BaseModel

from translation_module.cli_io import write_models_atomic

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class Payload(BaseModel):
    """Serializable durability fixture."""

    value: str


def test_each_transaction_target_fsyncs_file_then_parent_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every staged file and committed directory entry is durable in order."""
    # Given
    events: list[str] = []
    original_fsync = os.fsync
    original_replace = os.replace

    def record_fsync(descriptor: int) -> None:
        events.append(
            "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        )
        original_fsync(descriptor)

    def record_replace(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        events.append(f"replace:{target}")
        original_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "replace", record_replace)

    # When
    write_models_atomic(
        (
            (tmp_path / "request.json", Payload(value="request")),
            (tmp_path / "assignment.json", Payload(value="assignment")),
        )
    )

    # Then
    assert events == [
        "file",
        "directory",
        "file",
        "directory",
        "file",
        "directory",
        "replace:.cli-io-transaction.json",
        "directory",
        "replace:request.json",
        "directory",
        "replace:assignment.json",
        "directory",
        "directory",
    ]
