# Copyright 2026 Transcription Module contributors

"""Canonical publication during final validation."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast

import transcription_module.validation as validation_module
from tests.unit.validation_lifecycle_support import (
    approve_final_review_fixture,
    transcript_fixture,
    validation_store_fixture,
)
from transcription_module.models import RunId, RunState, Transcript, WorkflowStatus
from transcription_module.validation import ValidationContractError, validate_run
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.source import SourceTranscriptV2

EXPECTED_CONCURRENT_BUILDS = 2

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from transcription_module.storage import RunStore


def test_final_revalidation_repairs_missing_canonical_source(tmp_path: Path) -> None:
    """Final validation deterministically repairs a missing generated source."""
    store, run_id = validation_store_fixture(tmp_path)
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    store.save_transcript(
        transcript_fixture(run_id, "복구할 문장").model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    approve_final_review_fixture(store, run_id)
    _ = validate_run(store, run_id)
    paths = store.paths(run_id)
    expected_context = paths.input_context.read_bytes()
    expected_source = paths.canonical_source.read_bytes()
    paths.canonical_source.unlink()

    _ = validate_run(store, run_id)

    assert paths.input_context.read_bytes() == expected_context
    assert paths.canonical_source.read_bytes() == expected_source


def test_finalized_validations_serialize_canonical_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent finalized validations cannot publish mixed generations."""
    store, run_id = validation_store_fixture(tmp_path)
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    store.save_transcript(
        transcript_fixture(run_id, "동시 최종 검증").model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    approve_final_review_fixture(store, run_id)
    first_build_started = threading.Event()
    release_first_build = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    original_build = cast(
        "Callable[..., tuple[ContentContextAsset, SourceTranscriptV2]]",
        vars(validation_module)["build_canonical_source_pair"],
    )
    build_count = 0
    errors: list[BaseException] = []

    def pause_first_build(
        build_store: RunStore,
        build_run_id: RunId,
        *,
        state: RunState,
        transcript: Transcript,
    ) -> tuple[ContentContextAsset, SourceTranscriptV2]:
        nonlocal build_count
        build_count += 1
        if build_count == 1:
            first_build_started.set()
            assert release_first_build.wait(timeout=2.0)
        return original_build(
            build_store,
            build_run_id,
            state=state,
            transcript=transcript,
        )

    def validate() -> None:
        try:
            _ = validate_run(store, run_id)
        except (OSError, ValidationContractError) as error:
            errors.append(error)

    def validate_second() -> None:
        try:
            second_started.set()
            validate()
        finally:
            second_finished.set()

    monkeypatch.setattr(
        validation_module,
        "build_canonical_source_pair",
        pause_first_build,
    )
    first = threading.Thread(target=validate)
    first.start()
    assert first_build_started.wait(timeout=2.0)
    second = threading.Thread(target=validate_second)
    second.start()
    assert second_started.wait(timeout=2.0)
    release_first_build.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    assert build_count == EXPECTED_CONCURRENT_BUILDS
    paths = store.paths(run_id)
    context = ContentContextAsset.model_validate_json(
        paths.input_context.read_text(encoding="utf-8")
    )
    source = SourceTranscriptV2.model_validate_json(
        paths.canonical_source.read_text(encoding="utf-8")
    )
    assert source.input_context_ref.content_digest == context.content_digest
