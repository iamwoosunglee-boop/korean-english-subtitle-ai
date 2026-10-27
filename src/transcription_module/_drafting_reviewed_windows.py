# Copyright 2026 Transcription Module contributors

"""Draft and publish windows from one validated visual artifact."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from transcription_module._drafting_models import (
    WindowDraft,
    WindowDraftArtifact,
    WindowDrafter,
    WindowDraftInput,
)
from transcription_module._drafting_post_reconciliation import (
    reconcile_post_drafter_window,
)
from transcription_module.storage import write_model_atomic
from transcription_module.window_evidence import (
    validate_window_draft,
    validate_window_draft_artifact,
)

if TYPE_CHECKING:
    from transcription_module.storage_paths import RunPaths
    from transcription_module.visual_review import WindowVisualReviewArtifact
    from transcription_module.window_snapshot import OpenedWindowEvidence


async def draft_reviewed_windows(
    paths: RunPaths,
    opened: OpenedWindowEvidence,
    base_inputs: tuple[WindowDraftInput, ...],
    visual_artifact: WindowVisualReviewArtifact,
    drafter: WindowDrafter,
) -> WindowDraftArtifact:
    """Run bounded drafters and publish one validated window artifact."""
    opened.files.assert_bound()
    inputs = tuple(
        data.model_copy(update={"visual_review": result})
        for data, result in zip(
            base_inputs,
            visual_artifact.results,
            strict=True,
        )
    )
    drafts: list[WindowDraft | None] = [None] * len(inputs)
    positions = {data.window_index: index for index, data in enumerate(inputs)}
    limiter = anyio.CapacityLimiter(4)

    async def run_one(data: WindowDraftInput) -> None:
        async with limiter:
            draft = await drafter(data)
        draft = reconcile_post_drafter_window(data, draft)
        validate_window_draft(data, draft)
        drafts[positions[data.window_index]] = draft

    async with anyio.create_task_group() as tasks:
        for data in inputs:
            _ = tasks.start_soon(run_one, data)

    completed = tuple(draft for draft in drafts if draft is not None)
    if len(completed) != len(inputs):
        message = "a window drafter returned no result"
        raise RuntimeError(message)
    artifact = WindowDraftArtifact(
        run_id=visual_artifact.run_id,
        inputs=inputs,
        drafts=completed,
    )
    validate_window_draft_artifact(
        artifact,
        expected_run_id=visual_artifact.run_id,
    )
    opened.files.assert_bound()
    write_model_atomic(paths.window_drafts, artifact)
    return artifact


__all__ = ["draft_reviewed_windows"]
