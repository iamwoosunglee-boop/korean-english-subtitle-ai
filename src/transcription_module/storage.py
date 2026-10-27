# Copyright 2026 Transcription Module contributors

"""Filesystem persistence for transcription runs."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from transcription_module.artifact_io import (
    confined_exists,
    durable_unlink,
    durable_write_model,
    read_confined_text,
)
from transcription_module.models import (
    RunId,
    RunState,
    Transcript,
    ValidationStatus,
)
from transcription_module.source_edit_errors import (
    SourceEditFailure,
    SourceEditLifecycleError,
)
from transcription_module.storage_paths import RunPaths
from transcription_module.transcript_serialization import (
    CSV_COLUMNS,
    transcript_as_csv,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pydantic import BaseModel

    from transcription_module.final_review_models import (
        FinalReviewAssignment,
        FinalReviewReceipt,
        FinalReviewResult,
    )


_RUN_LOCKS: dict[str, threading.RLock] = {}
_RUN_LOCKS_GUARD = threading.Lock()
_RUN_FLOCK_DEPTH = threading.local()


def _run_lock(path: Path) -> threading.RLock:
    """Return the process-local lock for a run lock path."""
    key = str(path)
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(key, threading.RLock())



@dataclass(frozen=True, slots=True)
class RunStore:
    """Persist immutable snapshots for runs below one root."""

    runs_dir: Path

    def create(self, state: RunState) -> RunPaths:
        """Create a run directory and its initial state."""
        paths = self.paths(state.run_id)
        paths.run_dir.mkdir(parents=True, exist_ok=False)
        write_model_atomic(paths.state, state)
        return paths

    def paths(self, run_id: RunId) -> RunPaths:
        """Resolve paths for an already parsed run ID."""
        return RunPaths(self.runs_dir / str(run_id))

    @contextmanager
    def lock_run(self, run_id: RunId) -> Generator[None]:
        """Exclusively lock one run across processes and threads."""
        lock_path = self.paths(run_id).run_dir / ".run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _run_lock(lock_path), lock_path.open("a+b") as lock_file:
            depths = cast("dict[str, int]", getattr(_RUN_FLOCK_DEPTH, "values", {}))
            depth = depths.get(str(lock_path), 0)
            if depth == 0:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            depths[str(lock_path)] = depth + 1
            _RUN_FLOCK_DEPTH.values = depths
            try:
                yield
            finally:
                depths[str(lock_path)] -= 1
                if depth == 0:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load_state(self, run_id: RunId) -> RunState:
        """Load and validate persisted run state."""
        return RunState.model_validate_json(
            self.paths(run_id).state.read_text(encoding="utf-8")
        )

    def save_state(self, state: RunState) -> None:
        """Atomically replace the persisted run state."""
        write_model_atomic(self.paths(state.run_id).state, state)

    def load_transcript(self, run_id: RunId) -> Transcript:
        """Load and validate the canonical transcript."""
        return Transcript.model_validate_json(
            read_confined_text(self.paths(run_id).transcript)
        )

    def load_final_review_assignment(self, run_id: RunId) -> FinalReviewAssignment:
        """Load the typed final-review assignment."""
        from transcription_module.final_review_models import (  # noqa: PLC0415
            FinalReviewAssignment,
        )

        return FinalReviewAssignment.model_validate_json(
            read_confined_text(self.paths(run_id).final_review_assignment)
        )

    def save_final_review_assignment(
        self, run_id: RunId, assignment: FinalReviewAssignment
    ) -> None:
        """Atomically persist a typed final-review assignment."""
        durable_write_model(self.paths(run_id).final_review_assignment, assignment)

    def load_final_review_artifact(self, run_id: RunId) -> FinalReviewReceipt:
        """Load the typed final-review receipt."""
        from transcription_module.final_review_models import (  # noqa: PLC0415
            FinalReviewReceipt,
        )

        return FinalReviewReceipt.model_validate_json(
            read_confined_text(self.paths(run_id).final_review_artifact)
        )

    def save_final_review_artifact(
        self, run_id: RunId, artifact: FinalReviewReceipt
    ) -> None:
        """Atomically persist a typed final-review receipt."""
        durable_write_model(self.paths(run_id).final_review_artifact, artifact)

    def load_final_review_result(self, run_id: RunId) -> FinalReviewResult:
        """Load the accepted complete final-review result."""
        from transcription_module.final_review_models import (  # noqa: PLC0415
            FinalReviewResult,
        )

        return FinalReviewResult.model_validate_json(
            read_confined_text(self.paths(run_id).final_review_result)
        )

    def save_final_review_result(
        self,
        run_id: RunId,
        result: FinalReviewResult,
    ) -> None:
        """Atomically persist the accepted complete final-review result."""
        durable_write_model(self.paths(run_id).final_review_result, result)

    def save_transcript(
        self,
        transcript: Transcript,
        *,
        preserve_final_review_assignment: bool = False,
    ) -> None:
        """Invalidate prior validation and review before publishing content."""
        with self.lock_run(transcript.run_id):
            self._save_transcript_unlocked(
                transcript,
                preserve_final_review_assignment=preserve_final_review_assignment,
            )

    def _save_transcript_unlocked(
        self,
        transcript: Transcript,
        *,
        preserve_final_review_assignment: bool = False,
    ) -> None:
        """Publish transcript content while the caller owns the run lock."""
        paths = self.paths(transcript.run_id)
        if confined_exists(paths.source_human_edit):
            raise SourceEditLifecycleError(SourceEditFailure.TRANSCRIPT_MUTATION)
        transcript_exists = confined_exists(paths.transcript)
        final_review_paths = (
            paths.final_review_assignment,
            paths.final_review_result,
            paths.final_review_artifact,
        )
        for authority_path in final_review_paths:
            _ = confined_exists(authority_path)
        if not preserve_final_review_assignment:
            durable_unlink(paths.final_review_assignment, missing_ok=True)
        durable_unlink(paths.final_review_result, missing_ok=True)
        durable_unlink(paths.final_review_artifact, missing_ok=True)
        paths.input_context.unlink(missing_ok=True)
        paths.canonical_source.unlink(missing_ok=True)
        state = self.load_state(transcript.run_id)
        self.save_state(
            state.model_copy(update={"validation_status": ValidationStatus.PENDING})
        )
        paths.validation_artifact.unlink(missing_ok=True)
        if transcript_exists:
            current = self.load_transcript(transcript.run_id)
            durable_write_model(
                paths.transcript,
                current.model_copy(
                    update={"validation_status": ValidationStatus.PENDING}
                ),
            )
        pending = transcript.model_copy(
            update={"validation_status": ValidationStatus.PENDING}
        )
        write_text_atomic(paths.transcript_csv, transcript_as_csv(pending))
        durable_write_model(paths.transcript, pending)

    def append_adapter_event(self, run_id: RunId, event: str) -> None:
        """Append one fixture-adapter event without rewriting history."""
        path = self.paths(run_id).adapter_events
        path.parent.mkdir(parents=True, exist_ok=True)
        record = json.dumps({"event": event}, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as output:
            _ = output.write(f"{record}\n")


def write_model_atomic(path: Path, model: BaseModel) -> None:
    """Serialize one validated model through an atomic file replacement."""
    write_text_atomic(path, f"{model.model_dump_json(indent=2)}\n")


def write_text_atomic(path: Path, content: str) -> None:
    """Replace a UTF-8 text file without exposing partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        created = True
        data = content.encode("utf-8")
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _ = temporary.replace(path)
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            if created:
                temporary.unlink(missing_ok=True)




__all__ = [
    "CSV_COLUMNS",
    "RunPaths",
    "RunStore",
    "transcript_as_csv",
    "write_model_atomic",
    "write_text_atomic",
]
