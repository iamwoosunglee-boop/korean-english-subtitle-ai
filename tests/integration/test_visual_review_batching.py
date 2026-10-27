# Copyright 2026 Transcription Module contributors

"""Integration coverage for bounded visual-review provider calls."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import anyio
import pytest

from tests.integration.visual_review_batching_support import (
    EXPECTED_BATCH_COUNT,
    FRAME_COUNT,
    MAX_BATCH_FRAMES,
    OWNED_FRAME_COUNT,
    RecordingVisualReviewer,
    RetainingDrafter,
    ReuseOnlyVisualReviewer,
    prepared_store,
)
from transcription_module._visual_review_batch_models import (
    WindowVisualBatchManifest,
)
from transcription_module._visual_review_batch_validation import (
    validate_visual_review_batch_bundle,
)
from transcription_module.drafting import create_window_drafts
from transcription_module.visual_review import (
    FrameVisualObservation,
    RawVisualCandidate,
    RawVisualRole,
    WindowVisualReviewArtifact,
    WindowVisualReviewResult,
)
from transcription_module.visual_review_validation import (
    validate_visual_review_result,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.models import RunId


def _assert_manifest_tampering_is_rejected(
    manifest: WindowVisualBatchManifest,
    persisted_visual: WindowVisualReviewArtifact,
    run_id: RunId,
) -> None:
    tampered_payload = manifest.model_dump()
    tampered_payload["records"] = tampered_payload["records"][:-1]
    tampered_payload["results"] = tampered_payload["results"][:-1]
    with pytest.raises(ValueError, match="parent frame coverage"):
        _ = WindowVisualBatchManifest.model_validate(tampered_payload)
    tampered_payload = manifest.model_dump()
    tampered_payload["records"][0]["result_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="result digest"):
        _ = WindowVisualBatchManifest.model_validate(tampered_payload)
    tampered = manifest.model_copy(update={"aggregate_result_sha256": "f" * 64})
    with pytest.raises(ValueError, match="aggregate digest"):
        validate_visual_review_batch_bundle(
            tampered,
            persisted_visual,
            expected_run_id=run_id,
        )
    tampered_payload = manifest.model_dump()
    tampered_payload["results"][0]["frame_observations"][0]["visible_screen_text"] = [
        "변조"
    ]
    tampered_result = WindowVisualReviewResult.model_validate(
        tampered_payload["results"][0]
    )
    tampered_payload["records"][0]["result_sha256"] = hashlib.sha256(
        tampered_result.model_dump_json().encode()
    ).hexdigest()
    tampered = WindowVisualBatchManifest.model_validate(tampered_payload)
    with pytest.raises(ValueError, match="aggregate result"):
        validate_visual_review_batch_bundle(
            tampered,
            persisted_visual,
            expected_run_id=run_id,
        )


def test_window_visual_review_batches_large_assignment_before_provider_call(
    tmp_path: Path,
) -> None:
    """One large window reaches providers only through bounded frame batches."""
    store, run_id, paths, frames = prepared_store(tmp_path)
    reviewer = RecordingVisualReviewer()
    batch_manifest_path = paths.run_dir / "window-visual-batches.json"
    drafter = RetainingDrafter(paths)

    artifact = anyio.run(
        create_window_drafts,
        store,
        run_id,
        reviewer,
        drafter,
    )

    assert len(reviewer.assignments) > 1
    assert max(len(item.frames) for item in reviewer.assignments) <= MAX_BATCH_FRAMES
    assert batch_manifest_path.is_file()
    assert artifact.inputs[0].visual_review is not None
    assert len(artifact.inputs[0].visual_review.frame_observations) == FRAME_COUNT
    assert tuple(
        candidate.complete_text
        for candidate in artifact.inputs[0].visual_review.candidates
    ) == ("경계 후보",)
    first_batch = reviewer.assignments[0]
    overlap_candidate = WindowVisualReviewResult(
        window_index=first_batch.window_index,
        reviewer_input_digest=first_batch.reviewer_input_digest,
        frame_observations=tuple(
            FrameVisualObservation(frame_id=frame.frame_id)
            for frame in first_batch.frames
        ),
        candidates=(
            RawVisualCandidate(
                candidate_id="overlap-only",
                complete_text="중첩 후보",
                proposed_role=RawVisualRole.SPEECH_CAPTION,
                supporting_frame_ids=(first_batch.frames[OWNED_FRAME_COUNT].frame_id,),
            ),
        ),
    )
    with pytest.raises(ValueError, match="owning core"):
        validate_visual_review_result(first_batch, overlap_candidate)
    manifest = WindowVisualBatchManifest.model_validate_json(
        batch_manifest_path.read_text(encoding="utf-8")
    )
    persisted_visual = WindowVisualReviewArtifact.model_validate_json(
        paths.window_visual_review.read_text(encoding="utf-8")
    )
    validate_visual_review_batch_bundle(
        manifest,
        persisted_visual,
        expected_run_id=run_id,
    )
    assert manifest.aggregate == persisted_visual
    assert (
        manifest.aggregate_result_sha256
        == hashlib.sha256(persisted_visual.model_dump_json().encode()).hexdigest()
    )
    assert len(manifest.records) == EXPECTED_BATCH_COUNT
    assert len(manifest.results) == EXPECTED_BATCH_COUNT
    assert tuple(record.result_sha256 for record in manifest.records) == tuple(
        hashlib.sha256(result.model_dump_json().encode()).hexdigest()
        for result in manifest.results
    )
    assert (
        max(len(record.assigned_frame_ids) for record in manifest.records)
        <= MAX_BATCH_FRAMES
    )
    assert tuple(
        frame_id for record in manifest.records for frame_id in record.owned_frame_ids
    ) == tuple(frame.frame_id for frame in frames)
    _assert_manifest_tampering_is_rejected(
        manifest,
        persisted_visual,
        run_id,
    )
    paths.window_visual_review.unlink()
    recovered = anyio.run(
        create_window_drafts,
        store,
        run_id,
        ReuseOnlyVisualReviewer(),
        drafter,
    )
    assert recovered.inputs[0].visual_review == persisted_visual.results[0]
    assert paths.window_visual_review.is_file()
