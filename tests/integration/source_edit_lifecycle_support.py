# Copyright 2026 Transcription Module contributors

"""Deterministic fixtures for run-owned source-edit lifecycle tests."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol

from typer.testing import CliRunner

from tests.integration.canonical_source_export_support import canonical_inputs
from transcription_module.boundaries import parse_run_id
from transcription_module.canonical_source_export import build_canonical_source_pair
from transcription_module.source_edit_lifecycle import finalize_source_edit
from transcription_module.storage import RunStore, write_model_atomic
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.source import (
    SourceFinalization,
    SourceTranscriptV2,
)

if TYPE_CHECKING:
    from transcription_module.models import RunId, Transcript


class EventSignal(Protocol):
    """Small multiprocessing event surface used by the lock worker."""

    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class StringSender(Protocol):
    """Small multiprocessing result surface used by the lock worker."""

    def send(self, item: str, /) -> None: ...


class LockProbeChannels(NamedTuple):
    """Cross-process barriers and result sink for one lock probe."""

    ready: EventSignal
    go: EventSignal
    blocked: EventSignal
    results: StringSender


runner = CliRunner()
CLI_ERROR_EXIT = 2
type InvalidChildFailure = Literal[
    "malformed",
    "wrong_document",
    "same_revision",
    "wrong_lineage",
]


def source_edit_fixture(
    tmp_path: Path,
) -> tuple[RunStore, RunId, SourceTranscriptV2, bytes, Transcript]:
    """Create one canonical origin and transcript in isolated run storage."""
    store, run_id, state, transcript, _ = canonical_inputs(tmp_path)
    context, source = build_canonical_source_pair(
        store,
        run_id,
        state=state,
        transcript=transcript,
    )
    paths = store.paths(run_id)
    write_model_atomic(paths.input_context, context)
    write_model_atomic(paths.canonical_source, source)
    write_model_atomic(paths.transcript, transcript)
    origin_bytes = paths.canonical_source.read_bytes()
    return store, run_id, source, origin_bytes, transcript


def write_invalid_child(
    child_path: Path,
    origin: SourceTranscriptV2,
    failure: InvalidChildFailure,
) -> None:
    """Write one malformed or semantically invalid occupied child."""
    child = SourceTranscriptV2.model_validate_json(child_path.read_bytes())
    variants: dict[InvalidChildFailure, bytes] = {
        "malformed": b"{",
        "wrong_document": (
            bind_canonical_digests(
                child.model_copy(update={"document_id": "other-document"})
            ).model_dump_json(indent=2)
            + "\n"
        ).encode(),
        "same_revision": (
            bind_canonical_digests(
                child.model_copy(update={"revision_id": origin.revision_id})
            ).model_dump_json(indent=2)
            + "\n"
        ).encode(),
        "wrong_lineage": (
            bind_canonical_digests(
                child.model_copy(update={"supersedes_revision_id": "other-revision"})
            ).model_dump_json(indent=2)
            + "\n"
        ).encode(),
    }
    _ = child_path.write_bytes(variants[failure])


def finalize_after_lock_probe(
    runs_dir: str,
    run_id_value: str,
    channels: LockProbeChannels,
) -> None:
    """Prove lock contention, then finalize after the writer releases."""
    store = RunStore(Path(runs_dir))
    run_id = parse_run_id(run_id_value)
    child_path = store.paths(run_id).source_human_edit
    lock_path = child_path.with_name(f"{child_path.name}.publish.lock")
    with lock_path.open("a+b") as lock_file:
        channels.ready.set()
        if not channels.go.wait(5):
            channels.results.send("go-timeout")
            return
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            channels.blocked.set()
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            channels.results.send("lock-not-blocked")
            return
    result = finalize_source_edit(
        store,
        run_id,
        SourceFinalization(
            finalized_at="2026-08-26T12:00:00Z",
            finalized_by="human-editor",
        ),
    )
    channels.results.send(result.content_digest)


__all__ = [
    "CLI_ERROR_EXIT",
    "InvalidChildFailure",
    "LockProbeChannels",
    "finalize_after_lock_probe",
    "runner",
    "source_edit_fixture",
    "write_invalid_child",
]
