# Copyright 2026 Transcription Module contributors

"""Typed persisted contracts for bounded visual-review execution."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, model_validator

from transcription_module.models import FrameId, FrozenModel, RunId

if TYPE_CHECKING:
    from transcription_module.visual_review import (
        WindowVisualReviewArtifact,
        WindowVisualReviewResult,
    )
else:
    from transcription_module import visual_review as _visual_review

    WindowVisualReviewArtifact = _visual_review.WindowVisualReviewArtifact
    WindowVisualReviewResult = _visual_review.WindowVisualReviewResult

NonNegativeInt = Annotated[int, Field(ge=0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class VisualReviewBatchParent(FrozenModel):
    """One complete parent assignment covered by bounded provider calls."""

    window_index: NonNegativeInt
    reviewer_input_digest: Sha256Digest
    frame_ids: tuple[FrameId, ...]

    @model_validator(mode="after")
    def frames_are_unique(self) -> Self:
        """Reject repeated parent frames."""
        if len(self.frame_ids) != len(set(self.frame_ids)):
            message = "visual review parent frame IDs must be unique"
            raise ValueError(message)
        return self


class VisualReviewBatchRecord(FrozenModel):
    """One validated bounded provider execution."""

    window_index: NonNegativeInt
    batch_index: NonNegativeInt
    parent_reviewer_input_digest: Sha256Digest
    reviewer_input_digest: Sha256Digest
    assigned_frame_ids: tuple[FrameId, ...]
    owned_frame_ids: tuple[FrameId, ...]
    result_sha256: Sha256Digest

    @model_validator(mode="after")
    def frame_ownership_is_bounded(self) -> Self:
        """Reject repeated, foreign, or reordered batch ownership."""
        if len(self.assigned_frame_ids) != len(set(self.assigned_frame_ids)) or len(
            self.owned_frame_ids
        ) != len(set(self.owned_frame_ids)):
            message = "visual review batch frame IDs must be unique"
            raise ValueError(message)
        assigned_positions = {
            frame_id: index for index, frame_id in enumerate(self.assigned_frame_ids)
        }
        if any(frame_id not in assigned_positions for frame_id in self.owned_frame_ids):
            message = "visual review batch owns a foreign frame"
            raise ValueError(message)
        positions = tuple(
            assigned_positions[frame_id] for frame_id in self.owned_frame_ids
        )
        if positions != tuple(sorted(positions)):
            message = "visual review batch owned frames must be ordered"
            raise ValueError(message)
        return self


class WindowVisualBatchManifest(FrozenModel):
    """Persisted execution proof for all bounded review calls in one run."""

    schema_version: Literal["window-visual-batches.v1"] = "window-visual-batches.v1"
    run_id: RunId
    parents: tuple[VisualReviewBatchParent, ...]
    aggregate: WindowVisualReviewArtifact
    aggregate_result_sha256: Sha256Digest
    records: tuple[VisualReviewBatchRecord, ...]
    results: tuple[WindowVisualReviewResult, ...]

    @model_validator(mode="after")
    def records_cover_parents_exactly(self) -> Self:
        """Require contiguous batches and exact parent-local frame ownership."""
        _validate_aggregate(self)
        _validate_direct_results(self)
        _validate_parent_coverage(self)
        return self


def _validate_aggregate(manifest: WindowVisualBatchManifest) -> None:
    if (
        manifest.aggregate.run_id != manifest.run_id
        or manifest.aggregate_result_sha256
        != hashlib.sha256(manifest.aggregate.model_dump_json().encode()).hexdigest()
    ):
        message = "visual review batch aggregate digest differs"
        raise ValueError(message)


def _validate_direct_results(manifest: WindowVisualBatchManifest) -> None:
    if len(manifest.records) != len(manifest.results):
        message = "visual review batch records require one direct result each"
        raise ValueError(message)
    for record, result in zip(
        manifest.records,
        manifest.results,
        strict=True,
    ):
        if (
            record.window_index != result.window_index
            or record.reviewer_input_digest != result.reviewer_input_digest
            or record.result_sha256
            != hashlib.sha256(result.model_dump_json().encode()).hexdigest()
        ):
            message = "visual review batch result digest differs from its record"
            raise ValueError(message)


def _validate_parent_coverage(manifest: WindowVisualBatchManifest) -> None:
    parent_indexes = tuple(parent.window_index for parent in manifest.parents)
    if parent_indexes != tuple(sorted(set(parent_indexes))):
        message = "visual review batch parents must be unique and ordered"
        raise ValueError(message)
    keys = tuple(
        (record.window_index, record.batch_index) for record in manifest.records
    )
    if keys != tuple(sorted(set(keys))):
        message = "visual review batch records must be unique and ordered"
        raise ValueError(message)
    by_window = {parent.window_index: parent for parent in manifest.parents}
    if any(record.window_index not in by_window for record in manifest.records):
        message = "visual review batch record has a foreign parent"
        raise ValueError(message)
    for parent in manifest.parents:
        records = tuple(
            record
            for record in manifest.records
            if record.window_index == parent.window_index
        )
        if tuple(record.batch_index for record in records) != tuple(
            range(len(records))
        ):
            message = "visual review batch indexes must be contiguous"
            raise ValueError(message)
        if any(
            record.parent_reviewer_input_digest != parent.reviewer_input_digest
            or not set(record.assigned_frame_ids).issubset(parent.frame_ids)
            for record in records
        ):
            message = "visual review batch differs from its parent assignment"
            raise ValueError(message)
        owned = tuple(
            frame_id for record in records for frame_id in record.owned_frame_ids
        )
        if owned != parent.frame_ids:
            message = "visual review parent frame coverage must be exact"
            raise ValueError(message)
