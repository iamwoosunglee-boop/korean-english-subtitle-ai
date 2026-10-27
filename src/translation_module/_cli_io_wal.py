# Copyright 2026 Transcription Module contributors

"""Durable relative-identity WAL for multi-model CLI publication."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from translation_module._cli_io_wal_models import (
    CliTransactionConflictError,
    CorruptCliTransactionError,
    TransactionWal,
    WalEntry,
    digest,
    operation_id,
)

if TYPE_CHECKING:
    from translation_module._cli_io_namespace import BoundOutput

WAL_NAME = ".cli-io-transaction.json"
_STAGING_PARTS = 3
_SHA256_HEX_LENGTH = 64


def entry(name: str, old: bytes | None, new: bytes) -> WalEntry:
    """Bind one relative target to exact old/new bytes and digests."""
    return WalEntry(
        name=name,
        old_bytes=None if old is None else base64.b64encode(old).decode(),
        new_bytes=base64.b64encode(new).decode(),
        old_digest=None if old is None else digest(old),
        new_digest=digest(new),
    )


def build_wal(root: BoundOutput, entries: tuple[WalEntry, ...]) -> TransactionWal:
    """Build one namespace-bound self-identifying transaction record."""
    device, inode, _ = root.identity
    namespace = (device, inode)
    return TransactionWal(
        operation_id=operation_id(entries, namespace),
        namespace_device=device,
        namespace_inode=inode,
        entries=entries,
    )


def internal_name(wal: TransactionWal, item: WalEntry) -> str:
    """Recompute the sole target-bound reserved staging name."""
    binding = digest(f"{item.name}:{item.new_digest}".encode())
    return f".cli-io-stage.{wal.operation_id}.{binding}.tmp"

def _rollback_name(wal: TransactionWal, item: WalEntry) -> str:
    binding = digest(f"{item.name}:{item.new_digest}".encode())
    return f".cli-io-rollback.{wal.operation_id}.{binding}.tmp"


def publish_wal(root: BoundOutput, wal: TransactionWal) -> None:
    """Durably publish the WAL before any final replacement."""
    temporary = f".cli-io-wal.{wal.operation_id}.tmp"
    payload = f"{wal.model_dump_json()}\n".encode()
    staged = root.read_name(temporary, missing_ok=True)
    if staged is not None:
        message = "current WAL staging path already exists"
        raise CliTransactionConflictError(message)
    published = False
    try:
        root.write_new(temporary, payload)
        root.replace_name(temporary, WAL_NAME)
        published = True
    finally:
        if not published:
            root.unlink(temporary, missing_ok=True)


def load_wal(root: BoundOutput) -> TransactionWal | None:
    """Load and validate one pending WAL through the held root."""
    payload = root.read_name(WAL_NAME, missing_ok=True)
    if payload is None:
        return None
    try:
        return TransactionWal.model_validate_json(payload)
    except (ValidationError, ValueError) as error:
        message = "invalid CLI transaction WAL"
        raise CorruptCliTransactionError(message) from error


def recover_pending(root: BoundOutput) -> None:
    """Roll forward any partially committed WAL, else retain all old states."""
    wal = load_wal(root)
    if wal is None:
        return
    states = tuple(_state(root, item) for item in wal.entries)
    _validate_staged(root, wal)
    roll_forward = "new" in states
    _apply(root, wal, roll_forward=roll_forward)


def rollback(root: BoundOutput, wal: TransactionWal) -> None:
    """Restore every old state after an ordinary caught failure."""
    _ = tuple(_state(root, item) for item in wal.entries)
    _apply(root, wal, roll_forward=False)


def _validate_staged(root: BoundOutput, wal: TransactionWal) -> None:
    for item in wal.entries:
        staged = root.read_name(internal_name(wal, item), missing_ok=True)
        rollback_staged = root.read_name(_rollback_name(wal, item), missing_ok=True)
        if (
            (staged is not None and staged != item.new())
            or (rollback_staged is not None and rollback_staged != item.old())
        ):
            message = f"foreign staged bytes: {item.name}"
            raise CorruptCliTransactionError(message)

def _state(root: BoundOutput, item: WalEntry) -> Literal["old", "new"]:
    current = root.read_name(item.name, missing_ok=True)
    if current == item.new():
        return "new"
    if current == item.old():
        return "old"
    message = f"foreign transaction bytes: {item.name}"
    raise CorruptCliTransactionError(message)


def _apply(root: BoundOutput, wal: TransactionWal, *, roll_forward: bool) -> None:
    for item in wal.entries:
        desired = item.new() if roll_forward else item.old()
        current = root.read_name(item.name, missing_ok=True)
        if current == desired:
            continue
        if desired is None:
            root.unlink(item.name, missing_ok=True)
            continue
        if roll_forward:
            root.unlink(_rollback_name(wal, item), missing_ok=True)
        temporary = (
            internal_name(wal, item)
            if roll_forward
            else _rollback_name(wal, item)
        )
        staged = root.read_name(temporary, missing_ok=True)
        if staged is not None and staged != desired:
            message = f"foreign staged bytes: {item.name}"
            raise CorruptCliTransactionError(message)
        if staged is None:
            root.write_new(temporary, desired)
        root.replace_name(temporary, item.name)
    for item in wal.entries:
        root.unlink(internal_name(wal, item), missing_ok=True)
        root.unlink(_rollback_name(wal, item), missing_ok=True)
    root.unlink(WAL_NAME)


__all__ = [
    "WAL_NAME",
    "CliTransactionConflictError",
    "CorruptCliTransactionError",
    "TransactionWal",
    "build_wal",
    "entry",
    "internal_name",
    "publish_wal",
    "recover_pending",
    "rollback",
]
