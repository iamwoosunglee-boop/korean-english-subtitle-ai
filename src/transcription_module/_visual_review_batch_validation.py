# Copyright 2026 Transcription Module contributors

"""Reconstruct persisted visual batches and bind their aggregate."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from transcription_module._visual_review_batch_models import (
    VisualReviewBatchParent,
    VisualReviewBatchRecord,
    WindowVisualBatchManifest,
)
from transcription_module._visual_review_batching import (
    batch_record,
    merge_visual_review_batch_results,
    plan_visual_review_batches,
)
from transcription_module.visual_review_validation import (
    validate_visual_review_artifact,
)

if TYPE_CHECKING:
    from transcription_module.models import RunId
    from transcription_module.visual_review import (
        WindowVisualReviewArtifact,
        WindowVisualReviewResult,
    )


def validate_visual_review_batch_bundle(
    manifest: WindowVisualBatchManifest,
    artifact: WindowVisualReviewArtifact,
    *,
    expected_run_id: RunId,
) -> None:
    """Rebuild and bind every persisted direct result to its aggregate."""
    validate_visual_review_artifact(artifact)
    if manifest.run_id != expected_run_id or artifact.run_id != expected_run_id:
        message = "visual review batch bundle run identity differs"
        raise ValueError(message)
    if manifest.aggregate != artifact:
        message = "visual review batch aggregate artifact differs"
        raise ValueError(message)
    expected_parents = tuple(
        VisualReviewBatchParent(
            window_index=assignment.window_index,
            reviewer_input_digest=assignment.reviewer_input_digest,
            frame_ids=tuple(frame.frame_id for frame in assignment.frames),
        )
        for assignment in artifact.assignments
    )
    if manifest.parents != expected_parents:
        message = "visual review batch bundle parent assignments differ"
        raise ValueError(message)
    offset = 0
    expected_records: list[VisualReviewBatchRecord] = []
    merged_results: list[WindowVisualReviewResult] = []
    for assignment in artifact.assignments:
        batches = plan_visual_review_batches(assignment)
        direct_results = manifest.results[offset : offset + len(batches)]
        if len(direct_results) != len(batches):
            message = "visual review batch bundle direct results are incomplete"
            raise ValueError(message)
        expected_records.extend(
            batch_record(assignment, index, batch, result)
            for index, (batch, result) in enumerate(
                zip(batches, direct_results, strict=True)
            )
        )
        merged_results.append(
            merge_visual_review_batch_results(
                assignment,
                batches,
                direct_results,
            )
        )
        offset += len(batches)
    if offset != len(manifest.results) or tuple(expected_records) != manifest.records:
        message = "visual review batch bundle execution records differ"
        raise ValueError(message)
    if tuple(merged_results) != artifact.results:
        message = "visual review batch aggregate result differs"
        raise ValueError(message)
    aggregate_digest = hashlib.sha256(artifact.model_dump_json().encode()).hexdigest()
    if manifest.aggregate_result_sha256 != aggregate_digest:
        message = "visual review batch aggregate digest differs"
        raise ValueError(message)


__all__ = ["validate_visual_review_batch_bundle"]
