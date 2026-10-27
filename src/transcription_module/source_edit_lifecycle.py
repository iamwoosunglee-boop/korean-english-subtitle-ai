# Copyright 2026 Transcription Module contributors

"""Run-owned canonical source human-edit lifecycle."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from transcription_module.artifact_io import (
    confined_exists,
    durable_create_model,
    read_confined_bytes,
)
from transcription_module.models import FrozenModel, RunId
from transcription_module.source_edit_errors import (
    SourceEditFailure,
    SourceEditLifecycleError,
)
from translation_module.canonical.common import DocumentLifecycleStatus
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.digest import (
    bind_canonical_digests,
    verify_canonical_digests,
)
from translation_module.canonical.source import SourceFinalization, SourceTranscriptV2
from translation_module.canonical.validation import (
    finalize_source,
    validate_canonical_save,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from transcription_module.storage import RunStore


class SourceEditStartResult(FrozenModel):
    """Machine-consumed result for one active source-edit lifecycle."""

    run_id: RunId
    state: Literal["editing"]
    next_action: Literal["edit_source"]
    origin_path: str
    source_path: str
    document_id: str
    origin_revision_id: str
    revision_id: str


class SourceEditFinalizeResult(FrozenModel):
    """Machine-consumed result for one finalized human-edit source."""

    run_id: RunId
    state: Literal["finalized"]
    next_action: Literal["prepare_translation"]
    source_path: str
    document_id: str
    revision_id: str
    content_digest: str
    record_digest: str


def _load_source(payload: bytes) -> SourceTranscriptV2:
    source = SourceTranscriptV2.model_validate_json(payload)
    validate_canonical_save(source)
    verify_canonical_digests(source)
    return source


def _load_context(payload: bytes) -> ContentContextAsset:
    context = ContentContextAsset.model_validate_json(payload)
    validate_canonical_save(context)
    verify_canonical_digests(context)
    return context


def _assert_context_binding(
    source: SourceTranscriptV2,
    context: ContentContextAsset,
) -> None:
    reference = source.input_context_ref
    if (
        reference.asset_id != context.asset_id
        or reference.revision_id != context.revision_id
        or reference.content_digest != context.content_digest
    ):
        raise SourceEditLifecycleError(SourceEditFailure.INVALID_CONTEXT)


def _assert_origin(source: SourceTranscriptV2, run_id: RunId) -> None:
    if source.document_id != str(run_id):
        raise SourceEditLifecycleError(SourceEditFailure.WRONG_RUN)
    if (
        source.lifecycle_status is not DocumentLifecycleStatus.DRAFT
        or source.finalization is not None
    ):
        raise SourceEditLifecycleError(SourceEditFailure.INVALID_ORIGIN)


def _assert_child(
    child: SourceTranscriptV2,
    origin: SourceTranscriptV2,
    context: ContentContextAsset,
) -> None:
    if child.document_id != origin.document_id:
        raise SourceEditLifecycleError(SourceEditFailure.WRONG_CHILD_DOCUMENT)
    if child.revision_id == origin.revision_id:
        raise SourceEditLifecycleError(SourceEditFailure.REUSED_REVISION)
    if child.supersedes_revision_id != origin.revision_id:
        raise SourceEditLifecycleError(SourceEditFailure.WRONG_CHILD_LINEAGE)
    if (
        child.lifecycle_status is not DocumentLifecycleStatus.DRAFT
        or child.finalization is not None
    ):
        raise SourceEditLifecycleError(SourceEditFailure.INVALID_CHILD_LIFECYCLE)
    _assert_context_binding(child, context)


def _result(
    store: RunStore,
    run_id: RunId,
    origin: SourceTranscriptV2,
    child: SourceTranscriptV2,
) -> SourceEditStartResult:
    paths = store.paths(run_id)
    return SourceEditStartResult(
        run_id=run_id,
        state="editing",
        next_action="edit_source",
        origin_path=str(paths.canonical_source),
        source_path=str(paths.source_human_edit),
        document_id=child.document_id,
        origin_revision_id=origin.revision_id,
        revision_id=child.revision_id,
    )


def _finalize_result(
    store: RunStore,
    run_id: RunId,
    finalized: SourceTranscriptV2,
) -> SourceEditFinalizeResult:
    return SourceEditFinalizeResult(
        run_id=run_id,
        state="finalized",
        next_action="prepare_translation",
        source_path=str(store.paths(run_id).source_final),
        document_id=finalized.document_id,
        revision_id=finalized.revision_id,
        content_digest=finalized.content_digest,
        record_digest=finalized.record_digest,
    )


def start_source_edit(store: RunStore, run_id: RunId) -> SourceEditStartResult:
    """Create or reopen the one run-owned direct human-edit child."""
    with store.lock_run(run_id):
        paths = store.paths(run_id)
        origin_payload = read_confined_bytes(paths.canonical_source)
        context_payload = read_confined_bytes(paths.input_context)
        if origin_payload is None or context_payload is None:
            raise FileNotFoundError(paths.canonical_source)
        origin = _load_source(origin_payload)
        context = _load_context(context_payload)
        _assert_origin(origin, run_id)
        _assert_context_binding(origin, context)
        child_payload = read_confined_bytes(paths.source_human_edit, missing_ok=True)
        if child_payload is not None:
            child = _load_source(child_payload)
            _assert_child(child, origin, context)
            return _result(store, run_id, origin, child)
        child = bind_canonical_digests(
            origin.model_copy(
                update={
                    "revision_id": str(uuid4()),
                    "supersedes_revision_id": origin.revision_id,
                }
            )
        )
        validate_canonical_save(child)
        durable_create_model(paths.source_human_edit, child)
        return _result(store, run_id, origin, child)


def assert_legacy_source_export_available(
    store: RunStore,
    run_id: RunId,
) -> None:
    """Reject legacy run source export after canonical human editing starts."""
    if confined_exists(store.paths(run_id).source_human_edit):
        raise SourceEditLifecycleError(SourceEditFailure.LEGACY_EXPORT)


@contextmanager
def _lock_source_publication(path: Path) -> Generator[None]:
    lock_path = path.with_name(f"{path.name}.publish.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def finalize_source_edit(
    store: RunStore,
    run_id: RunId,
    finalization: SourceFinalization,
    *,
    expected_content_digest: str | None = None,
) -> SourceEditFinalizeResult:
    """Finalize the exact run-owned human-edit child create-once."""
    with store.lock_run(run_id):
        paths = store.paths(run_id)
        with _lock_source_publication(paths.source_human_edit):
            origin_payload = read_confined_bytes(paths.canonical_source)
            context_payload = read_confined_bytes(paths.input_context)
            child_payload = read_confined_bytes(paths.source_human_edit)
            if (
                origin_payload is None
                or context_payload is None
                or child_payload is None
            ):
                raise FileNotFoundError(paths.source_human_edit)
            origin = _load_source(origin_payload)
            context = _load_context(context_payload)
            child = _load_source(child_payload)
            _assert_origin(origin, run_id)
            _assert_context_binding(origin, context)
            _assert_child(child, origin, context)
            if (
                expected_content_digest is not None
                and child.content_digest != expected_content_digest
            ):
                raise SourceEditLifecycleError(SourceEditFailure.HUMAN_EDIT_CHANGED)
            finalized = finalize_source(
                child,
                finalization,
                input_context=context,
            )
            validate_canonical_save(finalized)
            existing_payload = read_confined_bytes(
                paths.source_final,
                missing_ok=True,
            )
            if existing_payload is not None:
                existing = _load_source(existing_payload)
                if existing != finalized:
                    raise SourceEditLifecycleError(
                        SourceEditFailure.FINALIZATION_CONFLICT
                    )
                return _finalize_result(store, run_id, existing)
            durable_create_model(paths.source_final, finalized)
            return _finalize_result(store, run_id, finalized)


__all__ = [
    "SourceEditFinalizeResult",
    "SourceEditStartResult",
    "assert_legacy_source_export_available",
    "finalize_source_edit",
    "start_source_edit",
]
