# Copyright 2026 Transcription Module contributors

"""Small atomic-file boundary for run-owned artifacts."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


def confined_exists(path: Path) -> bool:
    """Check existence without admitting a symlink artifact."""
    if path.is_symlink():
        message = f"artifact path is a symlink: {path}"
        raise OSError(message)
    return path.exists()


def read_confined_bytes(
    path: Path,
    *,
    missing_ok: bool = False,
) -> bytes | None:
    """Read one regular non-symlink artifact."""
    if path.is_symlink():
        message = f"artifact path is a symlink: {path}"
        raise OSError(message)
    if not path.exists():
        if missing_ok:
            return None
        raise FileNotFoundError(path)
    if not path.is_file():
        message = f"artifact path is not a regular file: {path}"
        raise OSError(message)
    return path.read_bytes()


def read_confined_text(path: Path) -> str:
    """Read one regular UTF-8 artifact."""
    payload = read_confined_bytes(path)
    if payload is None:
        raise FileNotFoundError(path)
    return payload.decode()


def durable_replace_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace one run-owned artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _ = temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def durable_create_bytes(path: Path, payload: bytes) -> None:
    """Atomically publish one new run-owned artifact without replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def durable_write_model(path: Path, model: BaseModel) -> None:
    """Atomically replace one Pydantic artifact."""
    durable_replace_bytes(path, f"{model.model_dump_json(indent=2)}\n".encode())


def durable_create_model(path: Path, model: BaseModel) -> None:
    """Atomically publish one new Pydantic artifact without replacement."""
    durable_create_bytes(path, f"{model.model_dump_json(indent=2)}\n".encode())


def durable_unlink(path: Path, *, missing_ok: bool = False) -> None:
    """Remove one regular artifact without following a symlink."""
    if path.is_symlink():
        message = f"artifact path is a symlink: {path}"
        raise OSError(message)
    path.unlink(missing_ok=missing_ok)


def durable_restore(path: Path, payload: bytes | None) -> None:
    """Restore one prior artifact payload or its absence."""
    if payload is None:
        durable_unlink(path, missing_ok=True)
        return
    durable_replace_bytes(path, payload)


__all__ = [
    "confined_exists",
    "durable_create_bytes",
    "durable_create_model",
    "durable_replace_bytes",
    "durable_restore",
    "durable_unlink",
    "durable_write_model",
    "read_confined_bytes",
    "read_confined_text",
]
