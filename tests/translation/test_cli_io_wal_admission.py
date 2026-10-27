# Copyright 2026 Transcription Module contributors

"""Requested-operation admission and pre-WAL cleanup."""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from pydantic import BaseModel

from translation_module._cli_io_namespace import BoundOutput, bind_output
from translation_module._cli_io_wal import (
    WAL_NAME,
    build_wal,
    entry,
    internal_name,
    publish_wal,
)
from translation_module._cli_io_wal_models import WalEntry
from translation_module.cli_io import write_models_atomic


class Payload(BaseModel):
    """Serializable WAL admission fixture."""

    value: str


def _bytes(value: str) -> bytes:
    return f"{Payload(value=value).model_dump_json(indent=2)}\n".encode()


def test_valid_pending_wal_must_match_requested_payloads(tmp_path: Path) -> None:
    """A valid WAL cannot mutate a different requested operation."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    old_a, old_b = b"old-a", b"old-b"
    _ = a.write_bytes(old_a)
    _ = b.write_bytes(old_b)
    with bind_output(a) as root:
        wal = build_wal(
            root,
            (
                entry(a.name, old_a, _bytes("pending-a")),
                entry(b.name, old_b, _bytes("pending-b")),
            ),
        )
        for item in wal.entries:
            root.write_new(internal_name(wal, item), item.new())
        publish_wal(root, wal)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(OSError, match="conflicts with requested"):
        write_models_atomic(
            ((a, Payload(value="other-a")), (b, Payload(value="other-b")))
        )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_divergent_retry_cleans_only_bounded_regular_pre_wal_staging(
    tmp_path: Path,
) -> None:
    """A divergent no-WAL retry removes only bounded regular staging."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _ = a.write_bytes(b"old-a")
    _ = b.write_bytes(b"old-b")
    orphan = tmp_path / f".cli-io-stage.{'a' * 64}.{'c' * 64}.tmp"
    wal_stage = tmp_path / f".cli-io-wal.{'b' * 64}.tmp"
    sibling = tmp_path / ".foreign.keep"
    _ = orphan.write_bytes(b"old stage")
    _ = wal_stage.write_bytes(b"old wal stage")
    _ = sibling.write_bytes(b"keep")

    write_models_atomic(
        ((a, Payload(value="new-a")), (b, Payload(value="new-b")))
    )

    assert not orphan.exists()
    assert not wal_stage.exists()
    assert sibling.read_bytes() == b"keep"
    assert not tuple(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("failure", ["write", "wal_replace"])
def test_system_exit_before_wal_publication_cleans_owned_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A divergent no-WAL retry removes only bounded regular staging."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _ = a.write_bytes(b"old-a")
    _ = b.write_bytes(b"old-b")
    if failure == "write":
        def interrupt_write(_descriptor: int, _payload: bytes) -> int:
            raise SystemExit(9)

        monkeypatch.setattr(os, "write", interrupt_write)
    else:
        original = BoundOutput.replace_name

        def interrupt_wal(target: BoundOutput, source: str, destination: str) -> None:
            if destination == WAL_NAME:
                raise SystemExit(9)
            original(target, source, destination)

        monkeypatch.setattr(BoundOutput, "replace_name", interrupt_wal)

    with pytest.raises(SystemExit):
        write_models_atomic(
            ((a, Payload(value="new-a")), (b, Payload(value="new-b")))
        )
    assert a.read_bytes() == b"old-a"
    assert b.read_bytes() == b"old-b"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.json", "b.json"]


def test_strict_wal_rejects_unknown_and_coerced_fields(tmp_path: Path) -> None:
    """Strict WAL parsing rejects extras and coercion before mutation."""
    a = tmp_path / "a.json"
    _ = a.write_bytes(b"old")
    malformed: dict[str, str | int | bool | list[str]] = {
        "version": "1",
        "operation_id": "0" * 64,
        "namespace_device": 1,
        "namespace_inode": 1,
        "entries": [],
        "unknown": True,
    }
    with bind_output(a) as root:
        root.write_new(WAL_NAME, json.dumps(malformed).encode())

    with pytest.raises(OSError, match="invalid CLI transaction WAL"):
        write_models_atomic(((a, Payload(value="new")),))
    assert a.read_bytes() == b"old"


def test_journal_temporary_cannot_name_unrelated_victim(tmp_path: Path) -> None:
    """A crafted internal name cannot grant unlink authority over a sibling."""
    a = tmp_path / "a.json"
    victim = tmp_path / "victim.txt"
    old, new = b"old", _bytes("new")
    _ = a.write_bytes(old)
    _ = victim.write_bytes(new)
    with bind_output(a) as root:
        wal = build_wal(root, (entry(a.name, old, new),))
        payload = wal.model_dump(mode="json")
        payload["entries"][0]["temporary"] = victim.name
        root.write_new(WAL_NAME, json.dumps(payload).encode())

    with pytest.raises(OSError, match="invalid CLI transaction WAL"):
        write_models_atomic(((a, Payload(value="new")),))
    assert a.read_bytes() == old
    assert victim.read_bytes() == new


@pytest.mark.parametrize(
    "relative",
    [WAL_NAME, ".cli-io-lock", "nested/.cli-io-stage.attack.tmp"],
)
def test_requested_outputs_reject_reserved_transaction_names(
    tmp_path: Path,
    relative: str,
) -> None:
    """Public targets cannot overlap WAL, lock, or internal staging names."""
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(OSError, match="reserved CLI transaction target"):
        write_models_atomic(((target, Payload(value="blocked")),))
    assert not target.exists()


def test_wal_entry_rejects_noncanonical_base64() -> None:
    """Invalid padding cannot construct an in-memory WAL entry."""
    with pytest.raises(ValueError, match="canonical Base64"):
        _ = WalEntry(
            name="a.json",
            old_bytes="b2xk====",
            new_bytes="bmV3",
            old_digest=hashlib.sha256(b"old").hexdigest(),
            new_digest=hashlib.sha256(b"new").hexdigest(),
        )


@pytest.mark.parametrize(
    ("first", "second"),
    [("A.json", "a.json"), ("é.json", "é.json")],
)
def test_pure_admission_rejects_casefold_and_nfc_aliases(
    tmp_path: Path, first: str, second: str
) -> None:
    """Canonical target collisions fail before creating nested roots."""
    root = tmp_path / "missing-root"
    with pytest.raises(OSError, match="canonical CLI output paths collide"):
        write_models_atomic(
            (
                (root / first, Payload(value="first")),
                (root / second, Payload(value="second")),
            )
        )
    assert not root.exists()


@pytest.mark.parametrize(
    "name", [".CLI-IO-TRANSACTION.JSON", ".Cli-Io-Lock"]
)
def test_reserved_case_alias_rejects_before_root_creation(
    tmp_path: Path, name: str
) -> None:
    """Reserved names use NFC casefold admission independent of filesystem."""
    root = tmp_path / "missing-root"
    with pytest.raises(OSError, match="reserved CLI transaction target"):
        write_models_atomic(((root / name, Payload(value="blocked")),))
    assert not root.exists()


@pytest.mark.parametrize(
    "raw",
    [
        r"nested\escape.json",
        r"C:\authority\escape.json",
        r"\\server\share\escape.json",
        r".\escape.json",
        r"..\escape.json",
    ],
)
def test_raw_backslash_alias_rejects_before_missing_root_creation(
    tmp_path: Path,
    raw: str,
) -> None:
    """Windows separator aliases never reach pathlib-based filesystem handling."""
    root = tmp_path / "missing-root"
    before = tuple(tmp_path.iterdir())
    with pytest.raises(OSError, match="invalid CLI output path"):
        write_models_atomic(((root / raw, Payload(value="blocked")),))
    assert tuple(tmp_path.iterdir()) == before
    assert not root.exists()
