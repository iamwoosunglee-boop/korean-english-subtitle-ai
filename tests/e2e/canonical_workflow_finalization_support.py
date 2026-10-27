# Copyright 2026 Transcription Module contributors

"""Explicit final-review approval and canonical export workflow support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from transcription_module.confirmation import (
    create_confirmation_session,
    set_confirmation_consent,
)
from transcription_module.confirmation_log import append_confirmation_requests
from transcription_module.confirmation_models import (
    ConfirmationId,
    ConfirmationItem,
    ConfirmationItemKind,
    VisualInsertionAction,
)
from transcription_module.final_review import (
    FinalReviewError,
    apply_final_review,
    prepare_final_review,
)
from transcription_module.final_review_models import (
    FinalReviewAssignment,
    FinalReviewResult,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.models import (
    Cue,
    CueId,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.source_finalization import assert_source_finalizable
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.validation import validate_run
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.validation import validate_canonical_save

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e.canonical_workflow_source_support import ConfirmedWorkflowResult


@dataclass(frozen=True, slots=True)
class FinalizedWorkflowResult:
    """Finalized run and digest-verified canonical source pair."""

    store: RunStore
    context: ContentContextAsset
    source: SourceTranscriptV2
    observations: dict[str, str | bool]


def make_provisional_confirmation_run(
    tmp_path: Path,
    *,
    speaker: str | None = "한지아",
) -> tuple[RunStore, RunId, CueId]:
    """Persist one consented provisional insertion and bound evidence."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("90000000-0000-4000-8000-000000000135")
    cue_id = CueId("provisional-visual-cue")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtu.be/provisional-visual"),
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
        )
    )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            validation_status=ValidationStatus.PENDING,
            duration_ms=Milliseconds(2_000),
            cues=(
                Cue(
                    sequence=1,
                    cue_id=cue_id,
                    start_tc_ms=Milliseconds(500),
                    end_tc_ms=Milliseconds(1_500),
                    speech="화면으로 복원한 발화",
                    speaker=speaker,
                    uncertainty=Uncertainty.NEEDS_CONFIRMATION,
                ),
            ),
            vision_evidence=(),
        )
    )
    append_confirmation_requests(
        store.paths(run_id).confirmations,
        (
            ConfirmationItem(
                kind=ConfirmationItemKind.VISUAL_INSERTION,
                confirmation_id=ConfirmationId("visual-decision-1"),
                cue_id=cue_id,
                cue_sequence=1,
                field=None,
                provisional_id="provisional:1",
                actions=(
                    VisualInsertionAction.ACCEPT,
                    VisualInsertionAction.REJECT,
                ),
                start_tc_ms=Milliseconds(500),
                end_tc_ms=Milliseconds(1_500),
                current_value="화면으로 복원한 발화",
                cue_speech="화면으로 복원한 발화",
                candidates=(),
                evidence=("candidate:1", "observation:1", "vad:1"),
            ),
        ),
    )
    paths = store.paths(run_id)
    _ = paths.window_visual_review.write_text(
        '{"raw_visual":"preserved"}\n',
        encoding="utf-8",
    )
    _ = paths.window_drafts.write_text(
        '{"provisional_visual":"preserved"}\n',
        encoding="utf-8",
    )
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    return store, run_id, cue_id


def _result(assignment: FinalReviewAssignment) -> FinalReviewResult:
    return FinalReviewResult(
        run_id=assignment.run_id,
        assignment_digest=assignment.assignment_digest,
        segments=tuple(
            ReviewSegmentResult(
                segment_id=segment.segment_id,
                frame_ids=segment.frame_ids,
                cue_ids=segment.cue_ids,
                frame_observations=tuple(
                    FrameReviewObservation(
                        frame_id=frame_id,
                        visible_screen_text=(),
                        compared_cue_ids=segment.cue_ids,
                    )
                    for frame_id in segment.frame_ids
                ),
                replacements=(),
            )
            for segment in assignment.segments
        ),
    )


def _authoritative(store: RunStore, run_id: RunId) -> dict[str, bytes | None]:
    paths = store.paths(run_id)
    values = {
        "transcript": paths.transcript,
        "csv": paths.transcript_csv,
        "state": paths.state,
        "validation": paths.validation_artifact,
        "context": paths.input_context,
        "source": paths.canonical_source,
        "confirmations": paths.confirmations,
        "final_review": paths.final_review_artifact,
    }
    return {
        name: path.read_bytes() if path.exists() else None
        for name, path in values.items()
    }


def run_finalization_workflow(
    confirmed: ConfirmedWorkflowResult,
) -> FinalizedWorkflowResult:
    """Reject stale review, apply current review, and validate canonical bytes."""
    store = confirmed.store
    run_id = confirmed.run_id
    assert_source_finalizable(store.load_transcript(run_id))
    assignment = prepare_final_review(store, run_id)
    transcript = store.load_transcript(run_id)
    changed = transcript.model_copy(
        update={
            "cues": (
                *transcript.cues[:2],
                transcript.cues[2].model_copy(update={"speech": "검토 전 변경"}),
            )
        }
    )
    write_model_atomic(store.paths(run_id).transcript, changed)
    before_stale = _authoritative(store, run_id)
    with pytest.raises(FinalReviewError, match="stale"):
        _ = apply_final_review(store, run_id, _result(assignment))
    assert _authoritative(store, run_id) == before_stale

    assignment = prepare_final_review(store, run_id)
    result = _result(assignment)
    _ = apply_final_review(store, run_id, result)
    approved = _authoritative(store, run_id)
    _ = apply_final_review(store, run_id, result)
    assert _authoritative(store, run_id) == approved

    _ = validate_run(store, run_id)
    paths = store.paths(run_id)
    source_bytes = paths.canonical_source.read_bytes()
    context_bytes = paths.input_context.read_bytes()
    source = SourceTranscriptV2.model_validate_json(source_bytes)
    context = ContentContextAsset.model_validate_json(context_bytes)
    for artifact in (source, context):
        validate_canonical_save(artifact)
        verify_canonical_digests(artifact)
    assert paths.canonical_source.read_bytes() == source_bytes
    assert paths.input_context.read_bytes() == context_bytes
    return FinalizedWorkflowResult(
        store,
        context,
        source,
        {
            "rejection_preserved": True,
            "stale_preserved": True,
            "replay_preserved": True,
            "source_schema": source.schema_version,
            "context_model": type(context).__name__,
            "context_schema": context.schema_version,
        },
    )
