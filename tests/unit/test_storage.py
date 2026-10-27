# Copyright 2026 Transcription Module contributors

"""Storage security regression tests."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from transcription_module import storage
from transcription_module.final_review_models import (
    FinalReviewAssignment,
    FinalReviewReceipt,
    ReviewSegmentAssignment,
)
from transcription_module.models import CueId, FrameId, Milliseconds, RunId


def test_run_paths_include_canonical_source_pair(tmp_path: Path) -> None:
    """Canonical handoff files use their stable exact names."""
    paths = storage.RunStore(tmp_path).paths(RunId("run-1"))

    assert paths.input_context == paths.run_dir / "context.input.json"
    assert paths.canonical_source == paths.run_dir / "source.json"


def test_final_review_artifacts_round_trip(tmp_path: Path) -> None:
    """Typed final-review artifacts persist and load without loss."""
    store = storage.RunStore(tmp_path)
    run_id = RunId("run-1")
    digest = "a" * 64
    assignment = FinalReviewAssignment(
        run_id=run_id,
        duration_ms=Milliseconds(1),
        frame_ids=(FrameId("frame-1"),),
        cue_ids=(CueId("cue-1"),),
        media_digest=digest,
        transcript_digest=digest,
        assignment_digest=digest,
        segments=(
            ReviewSegmentAssignment(
                segment_id="segment-1",
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(1),
                frame_ids=(FrameId("frame-1"),),
                cue_ids=(CueId("cue-1"),),
            ),
        ),
    )
    receipt = FinalReviewReceipt(
        run_id=run_id,
        assignment_digest=digest,
        result_digest=digest,
        media_digest=digest,
        transcript_digest=digest,
        result_cue_ids=(CueId("cue-1"),),
    )

    store.save_final_review_assignment(run_id, assignment)
    store.save_final_review_artifact(run_id, receipt)

    assert store.load_final_review_assignment(run_id) == assignment
    assert store.load_final_review_artifact(run_id) == receipt


def test_lock_run_blocks_second_thread_until_first_exits(tmp_path: Path) -> None:
    """A run lock excludes another thread until its owner leaves."""
    store = storage.RunStore(tmp_path)
    run_id = RunId("run-1")
    first_acquired = threading.Event()
    second_attempted = threading.Event()
    second_acquired = threading.Event()
    release_first = threading.Event()

    def first() -> None:
        with store.lock_run(run_id):
            first_acquired.set()
            _ = release_first.wait()

    def second() -> None:
        second_attempted.set()
        with store.lock_run(run_id):
            second_acquired.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    _ = first_acquired.wait(timeout=1)
    second_thread.start()
    _ = second_attempted.wait(timeout=1)
    assert not second_acquired.is_set()
    release_first.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)
    assert second_acquired.is_set()
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()


def test_lock_run_is_reentrant_for_same_thread(tmp_path: Path) -> None:
    """Nested lock acquisition by one thread succeeds."""
    store = storage.RunStore(tmp_path)
    run_id = RunId("run-1")

    with store.lock_run(run_id), store.lock_run(run_id):
        assert (tmp_path / str(run_id) / ".run.lock").exists()


def test_write_text_atomic_cleans_temporary_file_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted write propagates without leaving partial state."""
    output = tmp_path / "output.txt"
    real_write = os.write
    wrote_once = False

    def interrupt_during_write(descriptor: int, data: bytes) -> int:
        nonlocal wrote_once
        if wrote_once:
            raise KeyboardInterrupt
        wrote_once = True
        return real_write(descriptor, data[:1])

    monkeypatch.setattr(os, "write", interrupt_during_write)

    with pytest.raises(KeyboardInterrupt):
        storage.write_text_atomic(output, "partial")

    assert not output.exists()
    assert not tuple(tmp_path.glob(".output.txt.*.tmp"))


def test_write_text_atomic_rejects_precreated_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A precreated temporary symlink cannot redirect an atomic write."""
    output = tmp_path / "output.txt"
    temporary = tmp_path / ".output.txt.fixed.tmp"
    victim = tmp_path / "victim.txt"
    _ = victim.write_text("unchanged", encoding="utf-8")
    temporary.symlink_to(victim)
    monkeypatch.setattr(storage, "uuid4", lambda: SimpleNamespace(hex="fixed"))

    with pytest.raises(FileExistsError):
        storage.write_text_atomic(output, "attacker")

    assert victim.read_text(encoding="utf-8") == "unchanged"
    assert temporary.is_symlink()
    assert not output.exists()
