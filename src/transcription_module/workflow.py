# Copyright 2026 Transcription Module contributors

"""Core walking-skeleton workflow independent from CLI rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from transcription_module.confirmation import create_confirmation_session
from transcription_module.confirmation_log import append_confirmation_requests
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.models import (
    EpisodeContext,
    FixtureRun,
    ProcessResult,
    RunId,
    RunState,
    StartResult,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.storage import RunStore


def start_run(source_url: YouTubeVideoUrl, store: RunStore) -> StartResult:
    """Create an inert run that waits for explicit episode context."""
    run_id = RunId(str(uuid4()))
    state = RunState(
        run_id=run_id,
        source_url=source_url,
        workflow_status=WorkflowStatus.WAITING_CONTEXT,
    )
    _ = store.create(state)
    return StartResult(
        run_id=run_id,
        workflow_status=WorkflowStatus.WAITING_CONTEXT,
    )


def apply_context_to_run(
    run_id: RunId,
    context: EpisodeContext,
    store: RunStore,
) -> ProcessResult:
    """Persist context and advance a real run without invoking adapters."""
    state = store.load_state(run_id)
    processing_state = RunState(
        run_id=state.run_id,
        source_url=state.source_url,
        workflow_status=WorkflowStatus.PROCESSING,
        context=context,
    )
    store.save_state(processing_state)
    return ProcessResult(
        run_id=run_id,
        workflow_status=WorkflowStatus.PROCESSING,
        validation_status=ValidationStatus.PENDING,
    )


def process_fixture_after_context(
    run_id: RunId,
    context: EpisodeContext,
    fixture_dir: Path,
    store: RunStore,
) -> ProcessResult:
    """Apply explicit context and process one deterministic fixture."""
    state = store.load_state(run_id)
    fixture = FixtureRun.model_validate_json(
        (fixture_dir / "run.json").read_text(encoding="utf-8")
    )
    store.append_adapter_event(run_id, "fixture-loaded")
    needs_confirmation = any(
        cue.uncertainty is Uncertainty.NEEDS_CONFIRMATION for cue in fixture.cues
    )
    workflow_status = (
        WorkflowStatus.WAITING_CONFIRMATION_CONSENT
        if needs_confirmation
        else WorkflowStatus.READY_FOR_VALIDATION
    )
    transcript = Transcript(
        run_id=run_id,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        duration_ms=fixture.duration_ms,
        cues=fixture.cues,
        vision_evidence=fixture.vision_evidence,
    )
    confirmation_items = tuple(
        ConfirmationItem(
            confirmation_id=ConfirmationId(f"cue-{cue.sequence}-speech"),
            cue_sequence=cue.sequence,
            field=ConfirmationField.SPEECH,
            start_tc_ms=cue.start_tc_ms,
            end_tc_ms=cue.end_tc_ms,
            current_value=cue.speech,
            cue_speech=cue.speech,
            candidates=(),
            evidence=tuple(str(item) for item in cue.vision_evidence_ids),
        )
        for cue in transcript.cues
        if cue.uncertainty is Uncertainty.NEEDS_CONFIRMATION
    )
    final_state = RunState(
        run_id=run_id,
        source_url=state.source_url,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        context=context,
    )
    append_confirmation_requests(
        store.paths(run_id).confirmations,
        confirmation_items,
    )
    store.save_transcript(transcript)
    store.save_state(final_state)
    if confirmation_items:
        _ = create_confirmation_session(store, run_id)
    return ProcessResult(
        run_id=run_id,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
    )
