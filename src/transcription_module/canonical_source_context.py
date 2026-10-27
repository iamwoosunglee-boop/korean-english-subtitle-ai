# Copyright 2026 Transcription Module contributors

"""Preserve raw visual scene context at the canonical source handoff."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._drafting_visual_assignments import (
    validate_reusable_visual_artifact,
)
from transcription_module.artifact_io import read_confined_bytes
from transcription_module.visual_review import WindowVisualReviewArtifact
from translation_module.canonical.context import ContextDecisionStatus, ContextFact

if TYPE_CHECKING:
    from transcription_module.models import RunState
    from transcription_module.storage import RunStore
    from translation_module.source import EvidenceEntry


def load_visual_context(
    store: RunStore,
    state: RunState,
) -> WindowVisualReviewArtifact | None:
    """Load existing scene observations only for this run and episode context."""
    payload = read_confined_bytes(
        store.paths(state.run_id).window_visual_review,
        missing_ok=True,
    )
    if payload is None:
        return None
    artifact = WindowVisualReviewArtifact.model_validate_json(payload)
    validate_reusable_visual_artifact(artifact)
    if artifact.run_id != state.run_id or any(
        assignment.episode_context != state.context
        for assignment in artifact.assignments
    ):
        message = "visual context belongs to another run or stale episode context"
        raise ValueError(message)
    return artifact


def visual_context_facts(
    artifact: WindowVisualReviewArtifact | None,
    entries: tuple[EvidenceEntry, ...],
) -> tuple[ContextFact, ...]:
    """Bind scene observations to current frames without inventing cue ownership."""
    if artifact is None:
        return ()
    current = {str(entry.evidence_id): entry for entry in entries}
    for assignment in artifact.assignments:
        for frame in assignment.frames:
            entry = current.get(str(frame.frame_id))
            if entry is None or (
                entry.resource_digest != frame.sha256
                or entry.pts_ms != frame.pts_ms
                or entry.locator != frame.path
            ):
                message = f"visual context frame is stale: {frame.frame_id}"
                raise ValueError(message)
    return tuple(
        ContextFact(
            fact_id=f"visual-context:{result.window_index}:{span.context_span_id}",
            statement=span.context,
            participant_ids=(),
            scope_source_cue_ids=(),
            evidence_ids=tuple(str(frame_id) for frame_id in span.supporting_frame_ids),
            decision_status=ContextDecisionStatus.UNRESOLVED,
            blocking=False,
        )
        for result in artifact.results
        for span in result.context_spans
    )


__all__ = ["load_visual_context", "visual_context_facts"]
