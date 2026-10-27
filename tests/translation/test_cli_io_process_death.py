# Copyright 2026 Transcription Module contributors

"""Real-process death recovery at every transactional CLI publication edge."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from translation_module._cli_io_namespace import bind_output
from translation_module._cli_io_wal import (
    build_wal,
    entry,
    internal_name,
    publish_wal,
)
from translation_module.cli_io import write_models_atomic

PROJECT_ROOT = Path(__file__).parents[2]
DEATH_EXIT = 91

_CHILD = r"""
import os
import sys
from pathlib import Path
from pydantic import BaseModel
from translation_module._cli_io_namespace import bind_output
from translation_module._cli_io_wal import (
    build_wal,
    entry,
    internal_name,
    publish_wal,
)
from translation_module.cli_io import write_models_atomic

class Payload(BaseModel):
    value: str

root = Path(sys.argv[1])
edge = sys.argv[2]
original_replace = os.replace
original_unlink = os.unlink

def die():
    os._exit(91)

def replace(source, target, **kwargs):
    kind = {
        '.cli-io-transaction.json': 'journal',
        'request.json': 'request',
        'assignment.json': 'assignment',
    }.get(str(target))
    if edge == f'{kind}_before':
        die()
    original_replace(source, target, **kwargs)
    if edge == f'{kind}_after':
        die()

def unlink(path, **kwargs):
    if str(path) == '.cli-io-transaction.json' and edge == 'cleanup_before':
        die()
    original_unlink(path, **kwargs)
    if str(path) == '.cli-io-transaction.json' and edge == 'cleanup_after':
        die()

os.replace = replace
os.unlink = unlink
write_models_atomic((
    (root / 'request.json', Payload(value='new request')),
    (root / 'assignment.json', Payload(value='new assignment')),
))
"""


class Payload(BaseModel):
    """Restart publication fixture."""

    value: str


@pytest.mark.parametrize(
    "edge",
    [
        "journal_before",
        "journal_after",
        "request_before",
        "request_after",
        "assignment_before",
        "assignment_after",
        "cleanup_before",
        "cleanup_after",
    ],
)
def test_process_death_recovers_exact_pair_without_residue(
    tmp_path: Path,
    edge: str,
) -> None:
    """Entry recovery converges every death edge to the exact retried pair."""
    # Given
    request = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    _ = request.write_bytes(b"old request")
    _ = assignment.write_bytes(b"old assignment")
    environment = {
        **os.environ,
        "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PROJECT_ROOT}",
    }

    # When
    child = subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path), edge],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Then
    assert child.returncode == DEATH_EXIT, child.stderr
    if edge == "request_after":
        assert request.read_bytes() != b"old request"
        assert assignment.read_bytes() == b"old assignment"
    if edge == "journal_before":
        write_models_atomic(
            (
                (request, Payload(value="divergent request")),
                (assignment, Payload(value="divergent assignment")),
            )
        )
    write_models_atomic(
        (
            (request, Payload(value="new request")),
            (assignment, Payload(value="new assignment")),
        )
    )
    assert Payload.model_validate_json(request.read_bytes()).value == "new request"
    assert Payload.model_validate_json(assignment.read_bytes()).value == (
        "new assignment"
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "assignment.json",
        "request.json",
    ]


@pytest.mark.parametrize("tamper", ["foreign", "symlink"])
def test_pending_wal_rejects_foreign_or_linked_final_bytes(
    tmp_path: Path,
    tamper: str,
) -> None:
    """Recovery never legitimizes bytes outside the journal's old/new states."""
    # Given
    request = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    old_request, old_assignment = b"old request", b"old assignment"
    _ = request.write_bytes(old_request)
    _ = assignment.write_bytes(old_assignment)
    new_request = f"{Payload(value='new request').model_dump_json(indent=2)}\n".encode()
    new_assignment = (
        f"{Payload(value='new assignment').model_dump_json(indent=2)}\n".encode()
    )
    with bind_output(request) as root:
        wal = build_wal(
            root,
            (
                entry(request.name, old_request, new_request),
                entry(assignment.name, old_assignment, new_assignment),
            ),
        )
        for item in wal.entries:
            root.write_new(internal_name(wal, item), item.new())
        publish_wal(root, wal)
        root.replace_name(internal_name(wal, wal.entries[0]), request.name)
    outside = tmp_path / "outside.json"
    if tamper == "foreign":
        _ = assignment.write_bytes(b"foreign")
    else:
        _ = outside.write_bytes(b"outside")
        assignment.unlink()
        assignment.symlink_to(outside)

    # When / Then
    with pytest.raises(OSError, match=r"foreign transaction|unsafe output entry"):
        write_models_atomic(
            (
                (request, Payload(value="new request")),
                (assignment, Payload(value="new assignment")),
            )
        )
    assert request.read_bytes() == new_request
    if tamper == "symlink":
        assert assignment.is_symlink()
        assert outside.read_bytes() == b"outside"
    else:
        assert assignment.read_bytes() == b"foreign"
