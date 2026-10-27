# Copyright 2026 Transcription Module contributors

"""Descriptor-confined validation transaction publication."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import transcription_module.validation as validation_module
from tests.unit.validation_lifecycle_support import (
    approve_final_review_fixture,
    transcript_fixture,
    validation_store_fixture,
)
from transcription_module.models import WorkflowStatus
from transcription_module.validation import validate_run
from translation_module.cli_io import write_models_atomic

if TYPE_CHECKING:
    from pydantic import BaseModel

FINAL_ENTRY_CHECK = 2


def test_final_validation_rejects_last_moment_transcript_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation cannot replace a transcript link inserted at final commit."""
    # Given
    store, run_id = validation_store_fixture(tmp_path)
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    store.save_transcript(
        transcript_fixture(run_id, "검증 링크 교체").model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    approve_final_review_fixture(store, run_id)
    target = store.paths(run_id).transcript
    outside = tmp_path / "outside-validation-transcript.json"
    payload = target.read_bytes()
    _ = outside.write_bytes(payload)
    original_stat = os.stat
    original_replace = Path.replace
    confined_checks = 0
    swapped = False
    armed = False
    original_transaction = write_models_atomic

    def swap_on_final_lstat(
        path: str,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal confined_checks, swapped
        if armed and path == target.name and not follow_symlinks:
            confined_checks += 1
            if confined_checks == FINAL_ENTRY_CHECK and not swapped:
                target.unlink()
                target.symlink_to(outside)
                swapped = True
        return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def swap_on_path_replace(source: Path, destination: Path) -> Path:
        nonlocal swapped
        if source.suffix == ".tmp" and destination == target and not swapped:
            target.unlink(missing_ok=True)
            target.symlink_to(outside)
            swapped = True
        return original_replace(source, destination)

    def arm_transaction(
        outputs: tuple[tuple[Path, BaseModel], ...],
    ) -> None:
        nonlocal armed
        armed = True
        original_transaction(outputs)

    monkeypatch.setattr(os, "stat", swap_on_final_lstat)
    monkeypatch.setattr(Path, "replace", swap_on_path_replace)
    monkeypatch.setattr(validation_module, "write_models_atomic", arm_transaction)

    # When / Then
    with pytest.raises(OSError, match="unsafe output entry"):
        _ = validate_run(store, run_id)
    assert target.is_symlink()
    assert outside.read_bytes() == payload
