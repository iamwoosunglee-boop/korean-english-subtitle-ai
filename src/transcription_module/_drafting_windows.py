# Copyright 2026 Transcription Module contributors

"""Bounded concurrent execution for independent transcript windows."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import anyio

from transcription_module._drafting_reviewed_windows import (
    draft_reviewed_windows,
)
from transcription_module._drafting_visual_assignments import (
    assignment_matches_current,
    base_window_inputs,
    validate_reusable_visual_artifact,
    visual_assignment,
)
from transcription_module._visual_review_batch_models import (
    VisualReviewBatchParent,
    VisualReviewBatchRecord,
    WindowVisualBatchManifest,
)
from transcription_module._visual_review_batch_storage import (
    publish_visual_review_bundle,
    recover_visual_review_bundle,
)
from transcription_module._visual_review_batch_validation import (
    validate_visual_review_batch_bundle,
)
from transcription_module._visual_review_batching import (
    review_visual_assignment_in_batches,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.visual_review import (
    VisualReviewerIdentity,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    WindowVisualReviewer,
    WindowVisualReviewResult,
)
from transcription_module.visual_review_validation import (
    validate_visual_review_artifact,
    validate_visual_review_result,
)
from transcription_module.window_snapshot import opened_window_evidence

if TYPE_CHECKING:
    from transcription_module._drafting_models import (
        WindowDraftArtifact,
        WindowDrafter,
    )
    from transcription_module.models import RunId


async def create_window_drafts(
    store: RunStore,
    run_id: RunId,
    visual_reviewer: WindowVisualReviewer,
    drafter: WindowDrafter,
) -> WindowDraftArtifact:
    """Draft five-minute windows from one immutable snapshot with four workers."""
    paths = store.paths(run_id)
    if (paths.run_dir / "window-visual-batches.json").exists():
        recovered = recover_visual_review_bundle(paths, run_id)
        return await create_window_drafts_from_visual_artifact(
            store,
            run_id,
            recovered,
            visual_reviewer.identity,
            drafter,
        )
    with opened_window_evidence(store, run_id) as opened:
        base_inputs = base_window_inputs(opened.evidence)
        frame_hashes = {
            snapshot.label: snapshot.sha256 for snapshot in opened.files.files
        }
        assignments = tuple(
            visual_assignment(
                data,
                visual_reviewer.identity,
                frame_hashes,
            )
            for data in base_inputs
        )
        review_results: list[WindowVisualReviewResult | None] = [None] * len(
            assignments
        )
        batch_records: list[tuple[VisualReviewBatchRecord, ...] | None] = [None] * len(
            assignments
        )
        batch_results: list[tuple[WindowVisualReviewResult, ...] | None] = [None] * len(
            assignments
        )
        review_limiter = anyio.CapacityLimiter(4)
        opened.files.assert_bound()

        async def run_visual_review(
            assignment: WindowVisualReviewAssignment,
        ) -> None:
            async with review_limiter:
                outcome = await review_visual_assignment_in_batches(
                    assignment,
                    visual_reviewer,
                )
            result = outcome.result
            validate_visual_review_result(assignment, result)
            review_results[assignment.window_index] = result
            batch_records[assignment.window_index] = outcome.records
            batch_results[assignment.window_index] = outcome.direct_results

        async with anyio.create_task_group() as tasks:
            for assignment in assignments:
                _ = tasks.start_soon(run_visual_review, assignment)

        completed_reviews = tuple(
            result for result in review_results if result is not None
        )
        if len(completed_reviews) != len(assignments):
            message = "a visual reviewer returned no result"
            raise RuntimeError(message)
        visual_artifact = WindowVisualReviewArtifact(
            run_id=run_id,
            assignments=assignments,
            results=completed_reviews,
        )
        validate_visual_review_artifact(visual_artifact)
        manifest = WindowVisualBatchManifest(
            run_id=run_id,
            parents=tuple(
                VisualReviewBatchParent(
                    window_index=assignment.window_index,
                    reviewer_input_digest=assignment.reviewer_input_digest,
                    frame_ids=tuple(frame.frame_id for frame in assignment.frames),
                )
                for assignment in assignments
            ),
            aggregate=visual_artifact,
            aggregate_result_sha256=hashlib.sha256(
                visual_artifact.model_dump_json().encode()
            ).hexdigest(),
            records=tuple(
                record
                for records in batch_records
                if records is not None
                for record in records
            ),
            results=tuple(
                result
                for results in batch_results
                if results is not None
                for result in results
            ),
        )
        publish_visual_review_bundle(
            paths,
            visual_artifact,
            manifest,
        )
        return await draft_reviewed_windows(
            paths,
            opened,
            base_inputs,
            visual_artifact,
            drafter,
        )


async def create_window_drafts_from_visual_artifact(
    store: RunStore,
    run_id: RunId,
    visual_artifact: WindowVisualReviewArtifact,
    selected_reviewer: VisualReviewerIdentity,
    drafter: WindowDrafter,
) -> WindowDraftArtifact:
    """Reuse one complete fresh raw artifact without invoking its reviewer."""
    paths = store.paths(run_id)
    with opened_window_evidence(store, run_id) as opened:
        base_inputs = base_window_inputs(opened.evidence)
        validate_reusable_visual_artifact(visual_artifact)
        by_index = {item.window_index: item for item in base_inputs}
        selected_inputs = tuple(
            by_index[assignment.window_index]
            for assignment in visual_artifact.assignments
            if assignment.window_index in by_index
        )
        if (
            visual_artifact.run_id != run_id
            or len(selected_inputs) != len(visual_artifact.assignments)
            or any(
                assignment.reviewer != selected_reviewer
                for assignment in visual_artifact.assignments
            )
            or any(
                not assignment_matches_current(
                    paths.run_dir,
                    data,
                    assignment,
                    selected_reviewer,
                    opened.files.files,
                )
                for data, assignment in zip(
                    selected_inputs,
                    visual_artifact.assignments,
                    strict=True,
                )
            )
        ):
            message = "visual review artifact is stale for current window inputs"
            raise ValueError(message)
        manifest_path = paths.run_dir / "window-visual-batches.json"
        if manifest_path.exists():
            validate_visual_review_batch_bundle(
                WindowVisualBatchManifest.model_validate_json(
                    manifest_path.read_bytes()
                ),
                visual_artifact,
                expected_run_id=run_id,
            )
        write_model_atomic(paths.window_visual_review, visual_artifact)
        return await draft_reviewed_windows(
            paths,
            opened,
            selected_inputs,
            visual_artifact,
            drafter,
        )


__all__ = ["create_window_drafts"]
