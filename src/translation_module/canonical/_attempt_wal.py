# Copyright 2026 Transcription Module contributors

"""Path-free write-ahead recovery for canonical attempt appends."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError

from translation_module.canonical._attempt_models import CorruptAttemptLedgerError
from translation_module.canonical.common import CanonicalModel

if TYPE_CHECKING:
    from translation_module.canonical._authority_namespace import AuthorityNamespace

WAL_NAME = "translation-attempts.wal.json"
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class AttemptAppendWal(CanonicalModel):
    """Exact path-free bytes and before/after identities for one append."""

    schema_version: Literal["translation-attempt-append-wal.v1"] = (
        "translation-attempt-append-wal.v1"
    )
    prior_ledger_digest: Digest
    prior_integrity_digest: Digest
    record: str = Field(min_length=1)
    expected_ledger_digest: Digest
    integrity_record: str = Field(min_length=1)
    expected_integrity_digest: Digest


def _digest(payload: bytes | None) -> str:
    return hashlib.sha256(payload or b"").hexdigest()


def prepare_append_wal(
    prior_ledger: bytes,
    prior_integrity: bytes | None,
    record: bytes,
    integrity: bytes,
) -> AttemptAppendWal:
    """Bind exact append bytes to both prior and expected store identities."""
    if not record.endswith(b"\n"):
        message = "attempt append WAL record must end with a newline"
        raise CorruptAttemptLedgerError(message)
    return AttemptAppendWal(
        prior_ledger_digest=_digest(prior_ledger),
        prior_integrity_digest=_digest(prior_integrity),
        record=record.decode(),
        expected_ledger_digest=_digest(prior_ledger + record),
        integrity_record=integrity.decode(),
        expected_integrity_digest=_digest(integrity),
    )


def persist_append_wal(
    namespace: AuthorityNamespace,
    wal: AttemptAppendWal,
) -> None:
    """Durably publish one append intent before authority files change."""
    payload = f"{wal.model_dump_json(indent=2)}\n".encode()
    namespace.write_atomic(WAL_NAME, payload)


def _load_wal(namespace: AuthorityNamespace) -> AttemptAppendWal | None:
    if not namespace.exists_regular(WAL_NAME):
        return None
    try:
        return AttemptAppendWal.model_validate_json(
            namespace.read_regular(WAL_NAME)
        )
    except (UnicodeDecodeError, ValidationError) as error:
        message = "attempt append WAL is invalid"
        raise CorruptAttemptLedgerError(message) from error


def _matches(payload: bytes | None, digest: str) -> bool:
    return _digest(payload) == digest


def recover_append_wal(
    namespace: AuthorityNamespace,
    ledger_name: str,
    integrity_name: str,
) -> None:
    """Redo a valid old/new append state within one bound namespace."""
    wal = _load_wal(namespace)
    if wal is None:
        return
    record = wal.record.encode()
    integrity = wal.integrity_record.encode()
    if _digest(integrity) != wal.expected_integrity_digest:
        message = "attempt append WAL integrity bytes mismatch"
        raise CorruptAttemptLedgerError(message)
    ledger = (
        namespace.read_regular(ledger_name)
        if namespace.exists_regular(ledger_name)
        else None
    )
    authority = (
        namespace.read_regular(integrity_name)
        if namespace.exists_regular(integrity_name)
        else None
    )
    ledger_old = _matches(ledger, wal.prior_ledger_digest)
    ledger_new = _matches(ledger, wal.expected_ledger_digest)
    authority_old = _matches(authority, wal.prior_integrity_digest)
    authority_new = _matches(authority, wal.expected_integrity_digest)
    if ledger_old and authority_old:
        prior = ledger or b""
        if _digest(prior + record) != wal.expected_ledger_digest:
            message = "attempt append WAL record does not bind expected ledger"
            raise CorruptAttemptLedgerError(message)
        namespace.write_atomic(ledger_name, prior + record)
        ledger_new = True
    elif ledger_new and ledger is not None:
        if not ledger.endswith(record) or _digest(ledger[: -len(record)]) != (
            wal.prior_ledger_digest
        ):
            message = "attempt append WAL record is not the ledger suffix"
            raise CorruptAttemptLedgerError(message)
    if ledger_new and authority_old:
        namespace.write_atomic(integrity_name, integrity)
        authority_new = True
    if not ledger_new or not authority_new:
        message = "attempt append WAL conflicts with persisted authority"
        raise CorruptAttemptLedgerError(message)
    namespace.remove(WAL_NAME)


__all__ = [
    "WAL_NAME",
    "AttemptAppendWal",
    "persist_append_wal",
    "prepare_append_wal",
    "recover_append_wal",
]
