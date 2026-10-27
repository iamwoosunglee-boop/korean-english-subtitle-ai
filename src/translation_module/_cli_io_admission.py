# Copyright 2026 Transcription Module contributors

"""Requested-operation admission for pending CLI transactions."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from translation_module._cli_io_wal import (
    CliTransactionConflictError,
    CorruptCliTransactionError,
    TransactionWal,
)

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module._cli_io_namespace import BoundOutput

_STAGING_PARTS = 3
_STAGE_NAME_PARTS = 5
_WAL_NAME_PARTS = 4
_STAGE_NAME = re.compile(
    r"\.cli-io-(?:stage|rollback)\.([0-9a-f]{64})\.([0-9a-f]{64})\.tmp"
)
_WAL_STAGE_NAME = re.compile(r"\.cli-io-wal\.([0-9a-f]{64})\.tmp")
_SHA256_HEX_LENGTH = 64
_JSON_LEAF = re.compile(r"^[a-z0-9][a-z0-9._-]*\.json$")
_WINDOWS_DEVICES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_WINDOWS_FORBIDDEN = frozenset('< > : " | ? *'.replace(" ", ""))

def require_matching(
    root: BoundOutput,
    wal: TransactionWal,
    requested: tuple[tuple[str, bytes], ...],
) -> None:
    """Reject a valid WAL not owned by this exact requested operation."""
    device, inode, _ = root.identity
    expected = tuple(
        sorted(
            (name, hashlib.sha256(payload).hexdigest())
            for name, payload in requested
        )
    )
    actual = tuple(sorted((item.name, item.new_digest) for item in wal.entries))
    if (
        (wal.namespace_device, wal.namespace_inode) != (device, inode)
        or actual != expected
    ):
        message = "pending CLI transaction conflicts with requested outputs"
        raise CliTransactionConflictError(message)


def validate_requested_paths(paths: tuple[Path, ...]) -> None:
    """Purely reject normalized aliases and reserved components."""
    identities: list[tuple[str, ...]] = []
    invalid_leaves: list[str] = []
    for path in paths:
        raw = str(path)
        posix = PurePosixPath(raw)
        windows = PureWindowsPath(raw)
        components = tuple(part for part in path.parts if part != path.anchor)
        leaf = path.name
        invalid_component = any(
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or any(
                unicodedata.category(character).startswith("C")
                for character in part
            )
            or any(character in _WINDOWS_FORBIDDEN for character in part)
            or unicodedata.normalize("NFKC", part)
            .casefold()
            .rstrip(". ")
            .split(".", maxsplit=1)[0]
            in _WINDOWS_DEVICES
            for part in components
        )
        if (
            "\\" in raw
            or raw.startswith("//")
            or windows.drive
            or windows.anchor.startswith("\\\\")
            or posix.parts[:1] == ("//",)
            or invalid_component
        ):
            message = f"invalid CLI output path: {raw}"
            raise CliTransactionConflictError(message)
        identity = tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in path.parts
            if part not in {path.anchor, "", "."}
        )
        if any(
            part.startswith(".cli-io") or part == ".run.lock"
            for part in identity
        ):
            message = f"reserved CLI transaction target: {path}"
            raise CliTransactionConflictError(message)
        identities.append(identity)
        if (
            unicodedata.normalize("NFC", leaf) != leaf
            or leaf.casefold() != leaf
            or _JSON_LEAF.fullmatch(leaf) is None
        ):
            invalid_leaves.append(raw)
    if len(identities) != len(set(identities)):
        message = "canonical CLI output paths collide"
        raise CliTransactionConflictError(message)
    if invalid_leaves:
        message = f"invalid CLI output path: {invalid_leaves[0]}"
        raise CliTransactionConflictError(message)


def cleanup_unjournaled(root: BoundOutput, operation_id: str) -> None:
    """Remove only different-operation regular reserved staging entries."""
    for name in root.names():
        stage = _STAGE_NAME.fullmatch(name)
        wal_stage = _WAL_STAGE_NAME.fullmatch(name)
        if stage is None and wal_stage is None:
            if name.casefold().startswith(".cli-io"):
                message = f"foreign CLI staging name: {name}"
                raise CorruptCliTransactionError(message)
            continue
        if stage is not None:
            embedded_operation = stage.group(1)
        elif wal_stage is not None:
            embedded_operation = wal_stage.group(1)
        else:
            continue
        if embedded_operation == operation_id:
            message = f"current CLI staging path already exists: {name}"
            raise CliTransactionConflictError(message)
        root.unlink(name)


__all__ = [
    "cleanup_unjournaled",
    "require_matching",
    "validate_requested_paths",
]
