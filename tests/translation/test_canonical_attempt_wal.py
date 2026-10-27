# Copyright 2026 Transcription Module contributors

"""Real-process death recovery for append-only translation authority."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.translation.canonical_attempt_support import (
    attempt_document,
    successful_attempt,
)
from translation_module.canonical.attempts import (
    CorruptAttemptLedgerError,
    TranslationAttemptStore,
)

PROJECT_ROOT = Path(__file__).parents[2]
DEATH_EXIT = 91

_CHILD = r"""
import os
import sys
from pathlib import Path

from tests.translation.canonical_attempt_support import (
    attempt_document,
    successful_attempt,
)
from translation_module.canonical.attempts import TranslationAttemptStore

def die() -> None:
    os._exit(91)

root = Path(sys.argv[1])
edge = sys.argv[2]
document = attempt_document()
store = TranslationAttemptStore(root)
store.append(successful_attempt(document), document)
original_replace = os.replace
original_unlink = os.unlink

def replace(source, target, *args, **kwargs) -> None:
    kind = {
        "translation-attempts.wal.json": "wal",
        "translation-attempts.jsonl": "ledger",
        "translation-attempts.integrity.json": "integrity",
    }.get(str(target))
    if edge == f"{kind}_before":
        die()
    original_replace(source, target, *args, **kwargs)
    if edge == f"{kind}_after":
        die()

def unlink(path, *args, **kwargs) -> None:
    if str(path) == "translation-attempts.wal.json" and edge == "cleanup_before":
        die()
    original_unlink(path, *args, **kwargs)
    if str(path) == "translation-attempts.wal.json" and edge == "cleanup_after":
        die()

os.replace = replace
os.unlink = unlink
store.append(successful_attempt(document, "attempt-b", 2), document)
"""


@pytest.mark.parametrize(
    "edge",
    [
        "wal_before",
        "wal_after",
        "ledger_before",
        "ledger_after",
        "integrity_before",
        "integrity_after",
        "cleanup_before",
        "cleanup_after",
    ],
)
def test_append_recovers_exactly_once_after_process_death(
    tmp_path: Path,
    edge: str,
) -> None:
    """Given death at each durable edge, load/retry yields one exact append."""
    root = tmp_path / edge
    env = {**os.environ, "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PROJECT_ROOT}"}

    died = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), edge],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert died.returncode == DEATH_EXIT
    store = TranslationAttemptStore(root)
    document = attempt_document()
    if edge == "wal_after":
        wal_bytes = (root / "translation-attempts.wal.json").read_bytes()
        assert str(root).encode() not in wal_bytes
        assert b'"path"' not in wal_bytes
    attempts = store.load()
    assert tuple(item.attempt_id for item in attempts) in {
        ("attempt-a",),
        ("attempt-a", "attempt-b"),
    }

    _ = store.append(successful_attempt(document, "attempt-b", 2), document)
    assert tuple(item.attempt_id for item in store.load()) == (
        "attempt-a",
        "attempt-b",
    )
    assert not (root / "translation-attempts.wal.json").exists()
    assert not tuple(
        path
        for path in root.iterdir()
        if path.name.startswith(
            (
                ".translation-attempts.wal.json.",
                ".translation-attempts.jsonl.",
                ".translation-attempts.integrity.json.",
            )
        )
    )


def test_foreign_bytes_during_pending_wal_fail_closed(tmp_path: Path) -> None:
    """A WAL never legitimizes ledger bytes outside its exact old/new states."""
    root = tmp_path / "foreign"
    env = {**os.environ, "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PROJECT_ROOT}"}
    died = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), "wal_after"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert died.returncode == DEATH_EXIT
    _ = (root / "translation-attempts.jsonl").write_bytes(b"foreign-ledger\n")

    with pytest.raises(CorruptAttemptLedgerError, match="conflicts"):
        _ = TranslationAttemptStore(root).load()


def test_orphan_temp_symlink_is_rejected_without_touching_target(
    tmp_path: Path,
) -> None:
    """Cleanup never follows an authority-root temporary-file symlink."""
    root = tmp_path / "symlink-store"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    _ = outside.write_text("keep", encoding="utf-8")
    orphan = root / ".translation-attempts.jsonl.forged"
    orphan.symlink_to(outside)

    with pytest.raises(CorruptAttemptLedgerError, match="temporary"):
        _ = TranslationAttemptStore(root).load()

    assert orphan.is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep"

    real_root = tmp_path / "real-store"
    real_root.mkdir()
    linked_root = tmp_path / "linked-store"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(CorruptAttemptLedgerError, match="unsafe authority root"):
        _ = TranslationAttemptStore(linked_root).load()
