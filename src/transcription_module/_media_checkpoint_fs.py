# Copyright 2026 Transcription Module contributors

"""Descriptor-confined fixed filesystem layout for media checkpoints."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from transcription_module._media_checkpoint_io import (
    StageFile,
    atomic_write,
    clear_directory,
    open_directory,
    read_optional,
    remove_file,
    scan_directory,
)
from transcription_module._media_checkpoint_namespace import (
    InodeBinding,
    RootNamespace,
    assert_name_binding,
    inode_binding,
    opened_root_namespace,
)
from transcription_module.media_checkpoint_models import (
    CheckpointStorageError,
    StageName,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_PRIVATE: Final = ".media-checkpoint"
_STAGING: Final = ".media-staging"
_PUBLISHED: Final = "media"
_CHECKPOINT: Final = "checkpoint.json"
_JOURNAL: Final = "publication.json"
_LOCK: Final = "lock"
_OPEN_LOCK: Final = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_UNSAFE_LOCK: Final = "unsafe checkpoint lock"
_DESTINATION_EXISTS: Final = "stage destination already exists"


class NamespacePoint(StrEnum):
    """Deterministic seams surrounding namespace-sensitive mutations."""

    BEFORE_OUTPUT_COPY = "namespace_before_output_copy"
    AFTER_OUTPUT_COPY = "namespace_after_output_copy"
    BEFORE_RECEIPT = "namespace_before_receipt"
    AFTER_RECEIPT = "namespace_after_receipt"
    BEFORE_CLEANUP = "namespace_before_cleanup"
    AFTER_CLEANUP = "namespace_after_cleanup"


def namespace_fault(point: NamespacePoint) -> None:
    """Do nothing unless a deterministic test replaces this hook."""
    del point


@dataclass(frozen=True, slots=True)
class CheckpointFiles:
    """Held fixed-layout descriptors under one exclusive root lock."""

    namespace: RootNamespace
    private_fd: int
    staging_fd: int
    published_fd: int

    @property
    def root_fd(self) -> int:
        """Return the held store-root descriptor."""
        return self.namespace.root_fd

    def assert_bound(self) -> None:
        """Require root and all visible fixed directories to remain held."""
        self.namespace.assert_bound()
        for name, child in self._layout_edges():
            assert_name_binding(self.root_fd, name, child)

    def assert_stage_bound(
        self,
        stage: StageName,
        source_fd: int | None,
        destination_fd: int | None,
    ) -> None:
        """Require visible stage names to identify supplied held inodes."""
        self.assert_bound()
        if source_fd is not None:
            assert_name_binding(self.staging_fd, stage.value, source_fd)
        if destination_fd is not None:
            assert_name_binding(self.published_fd, stage.value, destination_fd)

    def base_bindings(
        self,
    ) -> tuple[InodeBinding, InodeBinding, InodeBinding, InodeBinding]:
        """Return root/private/staging/publication inode identities."""
        return (
            inode_binding(self.root_fd),
            inode_binding(self.private_fd),
            inode_binding(self.staging_fd),
            inode_binding(self.published_fd),
        )

    def read_checkpoint(self) -> bytes | None:
        """Read exact checkpoint bytes through the held private directory."""
        return read_optional(self.private_fd, _CHECKPOINT)

    def write_checkpoint(self, payload: bytes) -> None:
        """Durably replace exact checkpoint bytes."""
        atomic_write(self.private_fd, _CHECKPOINT, payload)

    def remove_checkpoint(self) -> None:
        """Durably restore first-publication checkpoint absence."""
        remove_file(self.private_fd, _CHECKPOINT)

    def read_journal(self) -> bytes | None:
        """Read exact private journal bytes."""
        return read_optional(self.private_fd, _JOURNAL)

    def write_journal(self, payload: bytes) -> None:
        """Durably replace the private journal."""
        atomic_write(self.private_fd, _JOURNAL, payload)

    def remove_journal(self) -> None:
        """Durably remove a completed journal."""
        remove_file(self.private_fd, _JOURNAL)

    @contextmanager
    def open_staging(
        self,
        stage: StageName,
        *,
        create: bool,
        stable: bool = True,
    ) -> Generator[int]:
        """Hold one visible staging inode for the complete operation."""
        descriptor = open_directory(self.staging_fd, stage.value, create=create)
        try:
            self.assert_stage_bound(stage, descriptor, None)
            yield descriptor
            if stable:
                self.assert_stage_bound(stage, descriptor, None)
        finally:
            os.close(descriptor)

    def open_optional_stage(self, parent_fd: int, stage: StageName) -> int | None:
        """Open one direct stage directory or return lexical absence."""
        try:
            return open_directory(parent_fd, stage.value, create=False)
        except FileNotFoundError:
            return None

    def create_stage(self, parent_fd: int, stage: StageName) -> int:
        """Create and hold one new stage directory below a held parent."""
        try:
            os.mkdir(stage.value, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError as error:
            raise CheckpointStorageError(_DESTINATION_EXISTS) from error
        return open_directory(parent_fd, stage.value, create=False)

    def create_destination(self, stage: StageName) -> int:
        """Create and hold one new publication-stage directory inode."""
        return self.create_stage(self.published_fd, stage)

    def remove_stage(self, parent_fd: int, stage: StageName, descriptor: int) -> None:
        """Clear and unlink one stage only through its held descriptors."""
        assert_name_binding(parent_fd, stage.value, descriptor)
        clear_directory(descriptor)
        os.rmdir(stage.value, dir_fd=parent_fd)
        os.fsync(parent_fd)

    def scan_staging(self, stage: StageName) -> tuple[StageFile, ...] | None:
        """Observe direct staged regular files without following links."""
        return self._scan_optional(self.staging_fd, stage)

    def scan_published(self, stage: StageName) -> tuple[StageFile, ...] | None:
        """Observe direct published regular files without following links."""
        return self._scan_optional(self.published_fd, stage)

    def _scan_optional(
        self,
        parent_fd: int,
        stage: StageName,
    ) -> tuple[StageFile, ...] | None:
        descriptor = self.open_optional_stage(parent_fd, stage)
        if descriptor is None:
            return None
        try:
            assert_name_binding(parent_fd, stage.value, descriptor)
            return scan_directory(descriptor)
        finally:
            os.close(descriptor)

    def _layout_edges(self) -> tuple[tuple[str, int], ...]:
        return (
            (_PRIVATE, self.private_fd),
            (_STAGING, self.staging_fd),
            (_PUBLISHED, self.published_fd),
        )


@contextmanager
def locked_checkpoint_store(root: Path) -> Generator[CheckpointFiles]:
    """Open every root component and fixed namespace without symlinks."""
    with opened_root_namespace(root) as namespace, ExitStack() as stack:
        private_fd = open_directory(namespace.root_fd, _PRIVATE, create=True)
        _ = stack.callback(os.close, private_fd)
        staging_fd = open_directory(namespace.root_fd, _STAGING, create=True)
        _ = stack.callback(os.close, staging_fd)
        published_fd = open_directory(namespace.root_fd, _PUBLISHED, create=True)
        _ = stack.callback(os.close, published_fd)
        lock_fd = _open_lock(private_fd)
        _ = stack.callback(os.close, lock_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        files = CheckpointFiles(namespace, private_fd, staging_fd, published_fd)
        files.assert_bound()
        yield files


def _open_lock(private_fd: int) -> int:
    try:
        descriptor = os.open(_LOCK, _OPEN_LOCK, 0o600, dir_fd=private_fd)
    except OSError as error:
        raise CheckpointStorageError(_UNSAFE_LOCK) from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CheckpointStorageError(_UNSAFE_LOCK)
    return descriptor
