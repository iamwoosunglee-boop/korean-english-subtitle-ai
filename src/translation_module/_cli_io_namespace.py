# Copyright 2026 Transcription Module contributors

"""Descriptor-held output namespaces for transactional CLI publication."""

from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


class UnsafeOutputEntryError(OSError):
    """A transactional output entry is not a regular file."""

    def __init__(self, path: Path) -> None:
        """Identify the unsafe entry without following it."""
        self.path: Path = path
        super().__init__(errno.ELOOP, f"unsafe output entry: {path}", path)


@dataclass(frozen=True, slots=True)
class BoundOutput:
    """One final filename held beneath a no-follow parent descriptor."""

    descriptor: int
    path: Path

    @property
    def name(self) -> str:
        """Return the validated final entry name."""
        return self.path.name

    @property
    def identity(self) -> tuple[int, int, str]:
        """Identify aliases by held parent inode and final name."""
        metadata = os.fstat(self.descriptor)
        return metadata.st_dev, metadata.st_ino, self.name

    def _regular_or_missing(self, name: str) -> bool:
        try:
            metadata = os.stat(
                name,
                dir_fd=self.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafeOutputEntryError(self.path)
        return True

    def read_original(self) -> bytes | None:
        """Read exact prior bytes without following the final entry."""
        return self.read_name(self.name, missing_ok=True)

    def read_name(self, name: str, *, missing_ok: bool = False) -> bytes | None:
        """Read one root-relative regular entry without following links."""
        _validate_name(name)
        if not self._regular_or_missing(name):
            if missing_ok:
                return None
            raise FileNotFoundError(name)
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=self.descriptor)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise UnsafeOutputEntryError(self.path)
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 64 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def write_new(self, name: str, payload: bytes) -> None:
        """Create and fsync one confined regular staging entry."""
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self.descriptor,
        )
        complete = False
        try:
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(self.descriptor)
            complete = True
        finally:
            if not complete:
                os.unlink(name, dir_fd=self.descriptor)
                os.fsync(self.descriptor)

    def replace(self, source: str) -> None:
        """Replace the checked final entry and fsync its held parent."""
        self.replace_name(source, self.name)

    def replace_name(self, source: str, destination: str) -> None:
        """Replace one checked root-relative entry and fsync the root."""
        _validate_name(source)
        _validate_name(destination)
        if not self._regular_or_missing(source):
            raise FileNotFoundError(source)
        _ = self._regular_or_missing(destination)
        os.replace(
            source,
            destination,
            src_dir_fd=self.descriptor,
            dst_dir_fd=self.descriptor,
        )
        os.fsync(self.descriptor)

    def names(self) -> tuple[str, ...]:
        """List entries from the held authority root."""
        return tuple(entry.name for entry in os.scandir(self.descriptor))

    def unlink(self, name: str, *, missing_ok: bool = False) -> None:
        """Unlink one checked confined regular entry and fsync its parent."""
        _validate_name(name)
        if not self._regular_or_missing(name):
            if missing_ok:
                return
            raise FileNotFoundError(name)
        os.unlink(name, dir_fd=self.descriptor)
        os.fsync(self.descriptor)


def _validate_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise OSError(errno.EINVAL, "output entry is not root-relative")


def _validate(path: Path) -> None:
    if not path.name or path.name in {".", ".."} or ".." in path.parts:
        raise UnsafeOutputEntryError(path)


@contextmanager
def bind_output(path: Path) -> Generator[BoundOutput]:
    """Traverse/create every parent component and hold the final namespace."""
    _validate(path)
    components = tuple(
        part for part in path.parent.parts if part not in {path.anchor, "", "."}
    )
    descriptor = os.open(path.anchor or ".", _DIRECTORY_FLAGS)
    try:
        for component in components:
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, dir_fd=descriptor)
                except FileExistsError:
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                else:
                    os.fsync(descriptor)
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield BoundOutput(descriptor, path)
    finally:
        os.close(descriptor)


__all__ = ["BoundOutput", "UnsafeOutputEntryError", "bind_output"]
