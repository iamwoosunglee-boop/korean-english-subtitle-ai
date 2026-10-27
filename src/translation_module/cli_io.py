# Copyright 2026 Transcription Module contributors

"""Secure bounded CLI JSON I/O and transactional model writes."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import ExitStack
from typing import TYPE_CHECKING
from uuid import uuid4

from translation_module._cli_io_admission import (
    cleanup_unjournaled,
    require_matching,
    validate_requested_paths,
)
from translation_module._cli_io_namespace import BoundOutput, bind_output
from translation_module._cli_io_wal import (
    build_wal,
    entry,
    internal_name,
    load_wal,
    publish_wal,
    recover_pending,
    rollback,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

MAX_JSON_BYTES = 8 * 1024 * 1024


def read_json(path: Path) -> bytes:
    """Read bounded JSON bytes from a regular non-symlink file."""
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            msg = f"JSON input must be a regular file: {path}"
            raise OSError(msg)
        if metadata.st_size > MAX_JSON_BYTES:
            msg = f"JSON input is too large: {path}"
            raise OSError(msg)
        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_JSON_BYTES:
            msg = f"JSON input is too large: {path}"
            raise OSError(msg)
        return payload
    finally:
        os.close(descriptor)


def write_model_atomic(path: Path, model: BaseModel) -> None:
    """Write one validated model without exposing partial bytes."""
    write_models_atomic(((path, model),))


def write_models_atomic(outputs: tuple[tuple[Path, BaseModel], ...]) -> None:
    """Recover, journal, and atomically converge every model to one state."""
    paths = tuple(path for path, _ in outputs)
    validate_requested_paths(paths)
    payloads = tuple(
        f"{model.model_dump_json(indent=2)}\n".encode() for _, model in outputs
    )
    if not paths:
        return
    with ExitStack() as stack:
        targets = tuple(stack.enter_context(bind_output(path)) for path in paths)
        identities = tuple(target.identity for target in targets)
        if len(identities) != len(set(identities)):
            msg = "output paths must be distinct, unaliased, non-symlink paths"
            raise OSError(msg)
        roots = {(device, inode) for device, inode, _ in identities}
        if len(roots) != 1:
            msg = "transaction outputs must share one authority root"
            raise OSError(msg)
        root = targets[0]
        fcntl.flock(root.descriptor, fcntl.LOCK_EX)
        try:
            pending = load_wal(root)
            requested = tuple(
                (target.name, payload)
                for target, payload in zip(targets, payloads, strict=True)
            )
            if pending is not None:
                require_matching(root, pending, requested)
                recover_pending(root)
            _publish(root, targets, payloads)
        finally:
            fcntl.flock(root.descriptor, fcntl.LOCK_UN)


def _publish(
    root: BoundOutput,
    targets: tuple[BoundOutput, ...],
    payloads: tuple[bytes, ...],
) -> None:
    originals = tuple(target.read_original() for target in targets)
    entries = tuple(
        entry(target.name, old, new)
        for target, old, new in zip(targets, originals, payloads, strict=True)
    )
    wal = build_wal(root, entries)
    cleanup_unjournaled(root, wal.operation_id)
    staged: list[str] = []
    wal_published = False
    try:
        for target, payload, item in zip(
            targets, payloads, entries, strict=True
        ):
            staged.append(_stage(target, payload, internal_name(wal, item)))
        publish_wal(root, wal)
        wal_published = True
        for target, temporary in zip(targets, staged, strict=True):
            target.replace(temporary)
        recover_pending(root)
    except (OSError, ValueError) as trigger:
        try:
            if wal_published:
                rollback(root, wal)
        except (OSError, ValueError) as rollback_error:
            trigger.add_note(f"transaction rollback failed: {rollback_error!r}")
        raise
    finally:
        if not wal_published:
            for target, temporary in zip(targets, staged, strict=False):
                target.unlink(temporary, missing_ok=True)


def restore_original(path: Path, original: bytes | None) -> None:
    """Restore one original destination during paired rollback."""
    with bind_output(path) as target:
        if target.read_original() == original:
            return
        if original is None:
            target.unlink(target.name, missing_ok=True)
            return
        temporary = _stage(
            target, original, f".{target.name}.{uuid4().hex}.tmp"
        )
        try:
            target.replace(temporary)
        finally:
            target.unlink(temporary, missing_ok=True)


def _stage(target: BoundOutput, payload: bytes, temporary: str) -> str:
    staged = target.read_name(temporary, missing_ok=True)
    if staged is not None:
        msg = f"current CLI staging path already exists: {target.name}"
        raise OSError(msg)
    target.write_new(temporary, payload)
    return temporary


__all__ = [
    "MAX_JSON_BYTES",
    "read_json",
    "restore_original",
    "write_model_atomic",
    "write_models_atomic",
]
