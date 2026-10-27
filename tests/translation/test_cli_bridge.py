# Copyright 2026 Transcription Module contributors

"""Transactional and size boundaries for thin translation CLI bridges."""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, model_serializer

from translation_module import cli_io


class Payload(BaseModel):
    """Small serializable paired-write fixture."""

    value: str


SECOND_OPERATION = 2
MODULE_LINE_LIMIT = 250


class BrokenPayload(Payload):
    """Assignment serialization failure fixture."""

    @model_serializer
    def serialization_fails(self) -> dict[str, str]:
        """Raise before paired staging begins."""
        msg = "assignment serialization failed"
        raise ValueError(msg)


def _temps(path: Path) -> list[Path]:
    return list(path.iterdir())


def test_paired_write_rejects_same_and_symlink_aliased_outputs(tmp_path: Path) -> None:
    """Request and assignment destinations cannot identify one path."""
    output = tmp_path / "output.json"
    alias = tmp_path / "alias.json"
    alias.symlink_to(output)
    with pytest.raises(OSError, match=r"distinct|canonical CLI output paths collide"):
        cli_io.write_models_atomic(
            ((output, Payload(value="request")), (output, Payload(value="assignment")))
        )
    with pytest.raises(OSError, match=r"distinct|unsafe output entry"):
        cli_io.write_models_atomic(
            ((output, Payload(value="request")), (alias, Payload(value="assignment")))
        )
    assert not output.exists()
    assert alias.is_symlink()


def test_paired_serialization_failure_writes_nothing(tmp_path: Path) -> None:
    """Both models serialize before either destination is staged."""
    request = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    with pytest.raises(ValueError, match="serialization"):
        cli_io.write_models_atomic(
            (
                (request, Payload(value="request")),
                (assignment, BrokenPayload(value="assignment")),
            )
        )
    assert not request.exists()
    assert not assignment.exists()
    assert _temps(tmp_path) == []


@pytest.mark.parametrize("failure", ["write", "second-replace", "interrupt"])
def test_paired_write_failure_restores_both_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Write, commit, and interrupt failures preserve prior paired files."""
    request = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    _ = request.write_text("old request")
    _ = assignment.write_text("old assignment")
    if failure in {"write", "interrupt"}:
        calls = 0
        original = os.fsync

        def fail_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == SECOND_OPERATION:
                if failure == "interrupt":
                    raise KeyboardInterrupt
                msg = "assignment write failed"
                raise OSError(msg)
            original(fd)

        monkeypatch.setattr(os, "fsync", fail_fsync)
    else:
        original_replace = os.replace
        commits = 0

        def fail_replace(
            source: str,
            target: str,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            nonlocal commits
            if source.endswith(".tmp"):
                commits += 1
                if commits == SECOND_OPERATION:
                    msg = "second replace failed"
                    raise OSError(msg)
            original_replace(
                source,
                target,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(os, "replace", fail_replace)
    expected = KeyboardInterrupt if failure == "interrupt" else OSError
    with pytest.raises(expected):
        cli_io.write_models_atomic(
            (
                (request, Payload(value="new request")),
                (assignment, Payload(value="new assignment")),
            )
        )
    assert request.read_text() == "old request"
    assert assignment.read_text() == "old assignment"
    assert sorted(path.name for path in _temps(tmp_path)) == [
        "assignment.json",
        "request.json",
    ]


def test_wal_cleanup_fault_restores_both_originals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary WAL cleanup fault rolls every final back to old bytes."""
    request, assignment = tmp_path / "request.json", tmp_path / "assignment.json"
    _ = request.write_bytes(b"old request")
    _ = assignment.write_bytes(b"old assignment")
    original = os.unlink
    armed = True

    def fail_wal_cleanup(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal armed
        if path == ".cli-io-transaction.json" and armed:
            armed = False
            msg = "wal cleanup failed"
            raise OSError(msg)
        original(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail_wal_cleanup)
    with pytest.raises(OSError, match="wal cleanup failed"):
        cli_io.write_models_atomic(
            (
                (request, Payload(value="new request")),
                (assignment, Payload(value="new assignment")),
            )
        )
    assert request.read_bytes() == b"old request"
    assert assignment.read_bytes() == b"old assignment"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "assignment.json",
        "request.json",
    ]


def test_translation_product_modules_stay_small() -> None:
    """Every translation product Python module stays within 250 physical lines."""
    root = Path(__file__).parents[2] / "src"
    modules = sorted(
        (
            *(root / "translation_module").glob("*.py"),
            *(root / "transcription_module").glob("translation_*.py"),
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    line_counts = {
        path.relative_to(root).as_posix(): len(path.read_text().splitlines())
        for path in modules
    }
    assert {
        relative_path: line_count
        for relative_path, line_count in line_counts.items()
        if line_count > MODULE_LINE_LIMIT
    } == {}
