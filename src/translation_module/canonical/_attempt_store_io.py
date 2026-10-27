# Copyright 2026 Transcription Module contributors

"""Crash-safe descriptor-relative operations for attempt authority."""

import fcntl
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from translation_module.canonical._attempt_models import CorruptAttemptLedgerError
from translation_module.canonical._authority_namespace import (
    AuthorityNamespace,
    open_authority_namespace,
)

type AttemptStoreUpdate = tuple[str, bytes]


@contextmanager
def locked_attempt_root(
    path: Path,
    *,
    create: bool = False,
) -> Generator[AuthorityNamespace]:
    """Bind and exclusively lock one no-follow authority namespace."""
    with open_authority_namespace(path, create=create) as namespace:
        fcntl.flock(namespace.descriptor, fcntl.LOCK_EX)
        try:
            yield namespace
        finally:
            fcntl.flock(namespace.descriptor, fcntl.LOCK_UN)


@contextmanager
def locked_attempt_namespace(
    namespace: AuthorityNamespace,
) -> Generator[AuthorityNamespace]:
    """Exclusively lock an already descriptor-bound namespace."""
    fcntl.flock(namespace.descriptor, fcntl.LOCK_EX)
    try:
        yield namespace
    finally:
        fcntl.flock(namespace.descriptor, fcntl.LOCK_UN)


def cleanup_orphan_attempt_temps(namespace: AuthorityNamespace) -> None:
    """Remove confined regular fixed-name staging orphans under the lock."""
    prefixes = (
        ".translation-attempts.wal.json.",
        ".translation-attempts.jsonl.",
        ".translation-attempts.integrity.json.",
    )
    removed = False
    for name in namespace.names():
        if not name.startswith(prefixes):
            continue
        try:
            metadata = stat.S_IFMT(
                os.stat(
                    name,
                    dir_fd=namespace.descriptor,
                    follow_symlinks=False,
                ).st_mode
            )
        except FileNotFoundError:
            continue
        if metadata != stat.S_IFREG:
            message = f"unsafe attempt temporary file: {name}"
            raise CorruptAttemptLedgerError(message)
        namespace.unlink_regular(name)
        removed = True
    if removed:
        os.fsync(namespace.descriptor)


def replace_attempt_files_transactionally(
    namespace: AuthorityNamespace,
    updates: tuple[AttemptStoreUpdate, ...],
) -> None:
    """Replace fixed files through the bound no-follow namespace."""
    originals = tuple(
        namespace.read_regular(name) if namespace.exists_regular(name) else None
        for name, _ in updates
    )
    completed: list[int] = []
    try:
        for index, (name, payload) in enumerate(updates):
            namespace.write_atomic(name, payload)
            completed.append(index)
    except (OSError, KeyboardInterrupt) as trigger:
        for index in reversed(completed):
            name = updates[index][0]
            original = originals[index]
            try:
                if original is None:
                    if namespace.exists_regular(name):
                        namespace.remove(name)
                else:
                    namespace.write_atomic(name, original)
            except OSError as rollback_error:
                trigger.add_note(f"attempt store rollback failed: {rollback_error!r}")
        raise


__all__ = [
    "cleanup_orphan_attempt_temps",
    "locked_attempt_namespace",
    "locked_attempt_root",
    "replace_attempt_files_transactionally",
]
