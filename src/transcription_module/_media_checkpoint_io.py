# Copyright 2026 Transcription Module contributors

"""Low-level regular-file I/O through held directory descriptors."""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from transcription_module.media_checkpoint_models import (
    CheckpointStorageError,
    Sha256Digest,
)

_OPEN_DIR: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_FILE: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_UNSAFE_DIRECTORY: Final = "unsafe checkpoint directory"
_UNSAFE_FILE: Final = "unsafe checkpoint file"
_UNSAFE_COMPONENT: Final = "unsafe checkpoint path component"
_DESTINATION_EXISTS: Final = "stage destination already exists"


@dataclass(frozen=True, slots=True)
class StageFile:
    """One direct regular file observed through a held descriptor."""

    name: str
    sha256: Sha256Digest


def open_directory(parent_fd: int, name: str, *, create: bool) -> int:
    """Open one direct directory component without following symlinks."""
    component(name)
    try:
        return os.open(name, _OPEN_DIR, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            return os.open(name, _OPEN_DIR, dir_fd=parent_fd)
        except FileExistsError:
            try:
                return os.open(name, _OPEN_DIR, dir_fd=parent_fd)
            except OSError as error:
                raise CheckpointStorageError(_UNSAFE_DIRECTORY) from error
        except OSError as error:
            raise CheckpointStorageError(_UNSAFE_DIRECTORY) from error
    except OSError as error:
        raise CheckpointStorageError(_UNSAFE_DIRECTORY) from error


def scan_directory(descriptor: int) -> tuple[StageFile, ...]:
    """Digest all direct regular files through one held directory."""
    files: list[StageFile] = []
    for name in sorted(os.listdir(descriptor)):
        component(name)
        payload = read_required(descriptor, name)
        files.append(StageFile(name, hashlib.sha256(payload).hexdigest()))
    return tuple(files)


def read_optional(parent_fd: int, name: str) -> bytes | None:
    """Read a regular file or return absence without following symlinks."""
    try:
        return read_required(parent_fd, name)
    except FileNotFoundError:
        return None


def read_required(parent_fd: int, name: str) -> bytes:
    """Read one direct regular file through a held parent descriptor."""
    descriptor = open_regular(parent_fd, name)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def open_regular(parent_fd: int, name: str) -> int:
    """Open one direct regular file with no-follow semantics."""
    component(name)
    try:
        descriptor = os.open(name, _OPEN_FILE, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise CheckpointStorageError(_UNSAFE_FILE) from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CheckpointStorageError(_UNSAFE_FILE)
    return descriptor


def create_output(parent_fd: int, name: str) -> int:
    """Create one new direct regular output through a held parent."""
    component(name)
    try:
        return os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise CheckpointStorageError(_DESTINATION_EXISTS) from error


def atomic_write(parent_fd: int, name: str, payload: bytes) -> None:
    """Durably replace a fixed regular file below a held parent."""
    assert_regular_or_missing(parent_fd, name)
    temporary = f".{name}.tmp"
    remove_stale_temporary(parent_fd, temporary)
    descriptor: int | None = None
    try:
        descriptor = create_output(parent_fd, temporary)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.rename(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent_fd)


def copy_directory(source_fd: int, destination_fd: int) -> None:
    """Copy direct regular files between two held directory inodes."""
    if os.listdir(destination_fd):
        raise CheckpointStorageError(_DESTINATION_EXISTS)
    for name in sorted(os.listdir(source_fd)):
        source = open_regular(source_fd, name)
        destination: int | None = None
        try:
            destination = create_output(destination_fd, name)
            while chunk := os.read(source, 1024 * 1024):
                _write_all(destination, chunk)
            os.fsync(destination)
        finally:
            os.close(source)
            if destination is not None:
                os.close(destination)
    os.fsync(destination_fd)


def clear_directory(descriptor: int) -> None:
    """Remove only direct regular files from one held directory inode."""
    for name in os.listdir(descriptor):
        component(name)
        try:
            observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as error:
            raise CheckpointStorageError(_UNSAFE_FILE) from error
        if not stat.S_ISREG(observed.st_mode):
            raise CheckpointStorageError(_UNSAFE_FILE)
        os.unlink(name, dir_fd=descriptor)
    os.fsync(descriptor)


def remove_file(parent_fd: int, name: str) -> None:
    """Durably remove one fixed regular file when present."""
    assert_regular_or_missing(parent_fd, name)
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    os.fsync(parent_fd)


def assert_regular_or_missing(parent_fd: int, name: str) -> None:
    """Reject a non-regular visible final component."""
    try:
        descriptor = open_regular(parent_fd, name)
    except FileNotFoundError:
        return
    os.close(descriptor)


def remove_stale_temporary(parent_fd: int, name: str) -> None:
    """Remove a prior regular temporary file under the held lock."""
    assert_regular_or_missing(parent_fd, name)
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    os.fsync(parent_fd)


def component(name: str) -> None:
    """Require one non-special direct name component."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise CheckpointStorageError(_UNSAFE_COMPONENT)


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        written += os.write(descriptor, payload[written:])
