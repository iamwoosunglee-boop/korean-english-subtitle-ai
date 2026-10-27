# Copyright 2026 Transcription Module contributors

"""Namespace-race and descriptor-authority checkpoint regressions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import transcription_module._media_checkpoint_fs as checkpoint_fs
import transcription_module._media_checkpoint_staging as staging_module
from tests.integration.media_checkpoint_support import (
    COMPLETED_AT,
    CONTRACTS,
    SOURCE_URL,
    stage_files,
)
from transcription_module.media_checkpoint import (
    CheckpointStorageError,
    MediaCheckpointStore,
    StageName,
    StageWriterClosedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from typing import BinaryIO


def _install_namespace_attack(
    monkeypatch: pytest.MonkeyPatch,
    point_name: str,
    attack: Callable[[], None],
) -> Callable[[], bool]:
    attacked = False

    def fault(point: checkpoint_fs.NamespacePoint) -> None:
        nonlocal attacked
        value = getattr(point, "value", str(point))
        if value == point_name and not attacked:
            attacked = True
            attack()

    monkeypatch.setattr(checkpoint_fs, "namespace_fault", fault, raising=False)
    return lambda: attacked


def _replace_staging_while_writing(
    store: MediaCheckpointStore,
    staging: Path,
    detached: Path,
) -> None:
    with store.open_stage_file(StageName.VIDEO) as output:
        assert not isinstance(output, Path)
        _ = staging.rename(detached)
        staging.mkdir()
        _ = output.write(b"held-video")


def test_t7_av_005_staging_exposes_handles_not_writable_paths(
    tmp_path: Path,
) -> None:
    """No public lexical path grants authority to create stage bytes."""
    # Given
    root = tmp_path / "root"
    root.mkdir()
    store = MediaCheckpointStore(root)

    # Then
    for removed_api in ("path", "journal_path", "published_path", "staging_path"):
        assert not hasattr(store, removed_api)
    staging = root / ".media-staging/video"
    detached = root / ".media-staging/detached-video"
    with pytest.raises(CheckpointStorageError, match="namespace"):
        _replace_staging_while_writing(store, staging, detached)
    assert not (staging / "source.mp4").exists()
    assert (detached / "source.mp4").read_bytes() == b"held-video"
    with store.open_stage_directory(StageName.FRAMES) as directory:
        assert not isinstance(directory, Path)
        with directory.open_file("f000001.jpg") as frame:
            _ = frame.write(b"frame")
        with pytest.raises(CheckpointStorageError):
            _ = directory.open_file("../outside")
    _ = store.inspect_staged(
        StageName.FRAMES,
        CONTRACTS.frames,
        COMPLETED_AT,
    )
    assert not (tmp_path / "outside").exists()


def test_t7_av_006_intermediate_root_symlink_is_rejected_before_layout(
    tmp_path: Path,
) -> None:
    """Every absolute root component is opened with no-follow semantics."""
    # Given
    real_parent = tmp_path / "real"
    run = real_parent / "run"
    run.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    # When / Then
    with pytest.raises(CheckpointStorageError, match="unsafe checkpoint root"):
        _ = MediaCheckpointStore(alias / "run").load()
    assert not (run / ".media-checkpoint").exists()
    assert not (run / ".media-staging").exists()
    assert not (run / "media").exists()


@pytest.mark.parametrize(
    "point_name",
    [
        "namespace_after_output_copy",
        "namespace_before_receipt",
        "namespace_after_receipt",
        "namespace_before_cleanup",
        "namespace_after_cleanup",
    ],
)
def test_t7_av_007_media_parent_replacement_never_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point_name: str,
) -> None:
    """A detached publication parent forces held-fd cleanup and failure."""
    # Given
    root = tmp_path / "root"
    root.mkdir()
    store = MediaCheckpointStore(root)
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"video"),),
    )
    media = root / "media"
    detached = root / "detached-media"
    replacement_marker = media / "attacker"

    def replace_media() -> None:
        _ = media.rename(detached)
        media.mkdir()
        _ = replacement_marker.write_bytes(b"outside-authority")

    attacked = _install_namespace_attack(
        monkeypatch,
        point_name,
        replace_media,
    )

    # When / Then
    with pytest.raises(CheckpointStorageError, match="namespace"):
        _ = store.publish_video(SOURCE_URL, completion)
    assert attacked()
    assert replacement_marker.read_bytes() == b"outside-authority"
    assert not (detached / StageName.VIDEO.value).exists()
    assert (root / ".media-staging/video/source.mp4").read_bytes() == b"video"
    assert not (root / ".media-checkpoint/checkpoint.json").exists()


def test_t7_av_008_stage_swap_cannot_change_held_copy_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A swapped stage name cannot publish a symlink or attacker bytes."""
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    marker = outside / "source.mp4"
    _ = marker.write_bytes(b"outside")
    store = MediaCheckpointStore(root)
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"trusted-stage"),),
    )
    visible = root / ".media-staging/video"
    original = root / ".media-staging/original-video"

    def swap_stage() -> None:
        _ = visible.rename(original)
        visible.symlink_to(outside, target_is_directory=True)

    attacked = _install_namespace_attack(
        monkeypatch,
        "namespace_before_output_copy",
        swap_stage,
    )

    # When / Then
    with pytest.raises(CheckpointStorageError, match="namespace"):
        _ = store.publish_video(SOURCE_URL, completion)
    assert attacked()
    assert visible.is_symlink()
    assert marker.read_bytes() == b"outside"
    assert (original / "source.mp4").read_bytes() == b"trusted-stage"
    assert not (root / "media/video").exists()
    assert not (root / ".media-checkpoint/checkpoint.json").exists()


class WriterBodyError(Exception):
    """Sentinel proving exceptional writer cleanup."""


def test_t7_av_009_deferred_file_context_rejects_reused_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lazy file context cannot act on a reused writer descriptor."""
    # Given
    root = tmp_path / "root"
    attacker = tmp_path / "attacker"
    root.mkdir()
    attacker.mkdir()
    store = MediaCheckpointStore(root)
    duplicated: list[int] = []
    duplicate = os.dup

    def record_duplicate(descriptor: int) -> int:
        owned = duplicate(descriptor)
        duplicated.append(owned)
        return owned

    monkeypatch.setattr(os, "dup", record_duplicate)
    with store.open_stage_directory(StageName.FRAMES) as writer:
        delayed = writer.open_file("late.jpg")
    assert len(duplicated) == 1
    writer_fd = duplicated[0]
    with pytest.raises(OSError, match="Bad file descriptor"):
        _ = os.fstat(writer_fd)
    replacement_fd = os.open(attacker, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if replacement_fd != writer_fd:
            _ = os.dup2(replacement_fd, writer_fd)

        # When / Then
        with pytest.raises(StageWriterClosedError), delayed:
            pass
        assert not (attacker / "late.jpg").exists()
        with pytest.raises(StageWriterClosedError):
            _ = writer.open_file("direct.jpg")
        with pytest.raises(StageWriterClosedError):
            writer.sync()
    finally:
        os.close(writer_fd)
        if replacement_fd != writer_fd:
            os.close(replacement_fd)


def test_t7_av_009_writer_close_is_repeated_and_exception_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal, repeated, and exceptional close invalidate one generation."""
    # Given
    root = tmp_path / "root"
    root.mkdir()
    store = MediaCheckpointStore(root)
    duplicated: list[int] = []
    duplicate = os.dup

    def record_duplicate(descriptor: int) -> int:
        owned = duplicate(descriptor)
        duplicated.append(owned)
        return owned

    monkeypatch.setattr(os, "dup", record_duplicate)

    writers: list[staging_module.StageDirectoryWriter] = []
    delayed_contexts: list[AbstractContextManager[BinaryIO]] = []

    def fail_inside_writer() -> None:
        with store.open_stage_directory(StageName.FRAMES) as writer:
            writers.append(writer)
            delayed_contexts.append(writer.open_file("exception.jpg"))
            raise WriterBodyError

    # When / Then
    with pytest.raises(WriterBodyError):
        fail_inside_writer()
    assert len(duplicated) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        _ = os.fstat(duplicated[0])
    writer = writers[0]
    writer.close()
    writer.close()
    with pytest.raises(StageWriterClosedError), delayed_contexts[0]:
        pass
    assert not (root / ".media-staging/frames/exception.jpg").exists()
