# Copyright 2026 Transcription Module contributors

"""No-follow descriptor binding for canonical authority directories."""

from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import AbstractContextManager, suppress
from typing import TYPE_CHECKING, Never, final, override

from translation_module.canonical._attempt_models import CorruptAttemptLedgerError

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


def _raise_boundary(message: str, cause: OSError | None = None) -> Never:
    error = CorruptAttemptLedgerError(message)
    if cause is None:
        raise error
    raise error from cause


def _ensure_directory(descriptor: int) -> None:
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        _raise_boundary("authority root is not a real directory")


@final
class AuthorityNamespace(AbstractContextManager["AuthorityNamespace"]):
    """One immutable directory-descriptor namespace binding."""

    def __init__(self, descriptor: int, display_path: Path) -> None:
        self._descriptor: int = descriptor
        self.display_path: Path = display_path

    @property
    def descriptor(self) -> int:
        """Return the bound directory descriptor for locking and fsync."""
        return self._descriptor

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        os.close(self._descriptor)

    def child(self, name: str, *, create: bool = False) -> AuthorityNamespace:
        """Open one regular child directory without following links."""
        _validate_name(name)
        if create:
            try:
                os.mkdir(name, dir_fd=self._descriptor)
            except FileExistsError:
                created = False
            else:
                created = True
            if created:
                os.fsync(self._descriptor)
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=self._descriptor)
        except OSError as error:
            message = f"unsafe authority directory: {name}"
            _raise_boundary(message, error)
        return AuthorityNamespace(descriptor, self.display_path / name)

    def exists_regular(self, name: str) -> bool:
        """Return whether a confined regular file exists; reject other types."""
        _validate_name(name)
        try:
            metadata = os.stat(name, dir_fd=self._descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(metadata.st_mode):
            message = f"unsafe authority file: {name}"
            _raise_boundary(message)
        return True

    def read_regular(self, name: str) -> bytes:
        """Read one regular file relative to the bound directory."""
        return self._read_regular(name, None)

    def read_regular_bounded(self, name: str, max_bytes: int) -> bytes:
        """Read at most one bounded regular file relative to this directory."""
        return self._read_regular(name, max_bytes)

    def _read_regular(self, name: str, max_bytes: int | None) -> bytes:
        _validate_name(name)
        try:
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=self._descriptor)
        except OSError as error:
            message = f"authority file is unavailable: {name}"
            _raise_boundary(message, error)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                message = f"unsafe authority file: {name}"
                _raise_boundary(message)
            chunks: list[bytes] = []
            total = 0
            while True:
                size = (
                    64 * 1024
                    if max_bytes is None
                    else min(64 * 1024, max_bytes + 1 - total)
                )
                if size <= 0:
                    break
                chunk = os.read(descriptor, size)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                message = f"authority file exceeds {max_bytes} bytes: {name}"
                _raise_boundary(message)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def write_atomic(self, name: str, payload: bytes) -> None:
        """Durably replace one confined regular file without following links."""
        _validate_name(name)
        _ = self.exists_regular(name)
        temporary = f".{name}.{secrets.token_hex(12)}"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._descriptor,
        )
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(
                temporary,
                name,
                src_dir_fd=self._descriptor,
                dst_dir_fd=self._descriptor,
            )
            os.fsync(self._descriptor)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=self._descriptor)

    def remove(self, name: str) -> None:
        """Remove one confined regular file and sync the namespace."""
        if not self.exists_regular(name):
            raise FileNotFoundError(name)
        os.unlink(name, dir_fd=self._descriptor)
        os.fsync(self._descriptor)

    def names(self) -> tuple[str, ...]:
        """List direct entries from the bound directory descriptor."""
        return tuple(entry.name for entry in os.scandir(self._descriptor))

    def unlink_regular(self, name: str) -> None:
        """Remove a direct regular entry without following links."""
        if self.exists_regular(name):
            os.unlink(name, dir_fd=self._descriptor)


def _validate_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        _raise_boundary("authority entry name is not confined")


def open_authority_namespace(
    path: Path,
    *,
    create: bool = False,
) -> AuthorityNamespace:
    """Open every root component no-follow and bind the final descriptor."""
    if ".." in path.parts:
        _raise_boundary("authority root cannot contain parent traversal")
    components = tuple(
        part for part in path.parts if part not in {path.anchor, "", "."}
    )
    descriptor = os.open(path.anchor or ".", _DIRECTORY_FLAGS)
    try:
        for index, component in enumerate(components):
            if create and index == len(components) - 1:
                try:
                    os.mkdir(component, dir_fd=descriptor)
                except FileExistsError:
                    created = False
                else:
                    created = True
                if created:
                    os.fsync(descriptor)
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                    message = f"unsafe authority root component: {component}"
                    _raise_boundary(message, error)
                raise
            os.close(descriptor)
            descriptor = child
        _ensure_directory(descriptor)
        return AuthorityNamespace(descriptor, path)
    except (CorruptAttemptLedgerError, OSError):
        os.close(descriptor)
        raise


__all__ = ["AuthorityNamespace", "open_authority_namespace"]
