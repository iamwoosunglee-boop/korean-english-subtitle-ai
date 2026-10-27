# Copyright 2026 Transcription Module contributors

"""Strict self-validating models for the CLI publication WAL."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)


class CorruptCliTransactionError(OSError):
    """A pending transaction contains foreign or malformed authority."""


class CliTransactionConflictError(OSError):
    """A valid pending WAL belongs to another requested operation."""


def digest(payload: bytes) -> str:
    """Return the canonical content digest."""
    return hashlib.sha256(payload).hexdigest()


class WalEntry(BaseModel):
    """Exact old/new states for one root-relative final entry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )
    name: str
    old_bytes: str | None
    new_bytes: str
    old_digest: str | None
    new_digest: str

    @field_validator("old_bytes", "new_bytes")
    @classmethod
    def base64_is_canonical(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            decoded = base64.b64decode(value, validate=True)
        except binascii.Error as error:
            message = "invalid canonical Base64"
            raise ValueError(message) from error
        if base64.b64encode(decoded).decode() != value:
            message = "invalid canonical Base64"
            raise ValueError(message)
        return value

    def old(self) -> bytes | None:
        """Decode the exact old state."""
        return None if self.old_bytes is None else base64.b64decode(self.old_bytes)

    def new(self) -> bytes:
        """Decode the exact new state."""
        return base64.b64decode(self.new_bytes)


class TransactionWal(BaseModel):
    """One stable operation identity and its ordered target states."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )
    version: Literal[1] = 1
    operation_id: str
    namespace_device: int
    namespace_inode: int
    entries: tuple[WalEntry, ...]

    @model_validator(mode="after")
    def authority_is_self_consistent(self) -> Self:
        if not self.entries or len({entry.name for entry in self.entries}) != len(
            self.entries
        ):
            message = "invalid transaction target identities"
            raise CorruptCliTransactionError(message)
        for item in self.entries:
            old = item.old()
            if (
                not item.name
                or "/" in item.name
                or "\\" in item.name
                or digest(item.new()) != item.new_digest
                or (old is None and item.old_digest is not None)
                or (old is not None and digest(old) != item.old_digest)
            ):
                message = "invalid transaction entry binding"
                raise CorruptCliTransactionError(message)
        namespace = (self.namespace_device, self.namespace_inode)
        if self.operation_id != operation_id(self.entries, namespace):
            message = "invalid transaction operation identity"
            raise CorruptCliTransactionError(message)
        return self


def operation_id(entries: tuple[WalEntry, ...], namespace: tuple[int, int]) -> str:
    """Bind sorted new payloads to one held authority namespace."""
    payload = json.dumps(
        (namespace, tuple(sorted((item.name, item.new_digest) for item in entries))),
        separators=(",", ":"),
    ).encode()
    return digest(payload)
