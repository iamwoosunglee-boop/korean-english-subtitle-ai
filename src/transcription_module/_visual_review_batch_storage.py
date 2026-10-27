# Copyright 2026 Transcription Module contributors

"""Publish and recover the atomic visual-review batch bundle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module._visual_review_batch_models import (
    WindowVisualBatchManifest,
)
from transcription_module._visual_review_batch_validation import (
    validate_visual_review_batch_bundle,
)
from transcription_module.storage import write_model_atomic
from transcription_module.visual_review import WindowVisualReviewArtifact

if TYPE_CHECKING:
    from transcription_module.models import RunId
    from transcription_module.storage_paths import RunPaths


def publish_visual_review_bundle(
    paths: RunPaths,
    artifact: WindowVisualReviewArtifact,
    manifest: WindowVisualBatchManifest,
) -> None:
    """Publish one recoverable authority before its visual projection."""
    manifest_path = paths.run_dir / "window-visual-batches.json"
    write_model_atomic(manifest_path, manifest)
    persisted_artifact = recover_visual_review_bundle(
        paths,
        artifact.run_id,
    )
    persisted_manifest = WindowVisualBatchManifest.model_validate_json(
        manifest_path.read_bytes()
    )
    validate_visual_review_batch_bundle(
        persisted_manifest,
        persisted_artifact,
        expected_run_id=artifact.run_id,
    )


def recover_visual_review_bundle(
    paths: RunPaths,
    expected_run_id: RunId,
) -> WindowVisualReviewArtifact:
    """Validate one atomic bundle and restore its derived projection."""
    manifest = WindowVisualBatchManifest.model_validate_json(
        (paths.run_dir / "window-visual-batches.json").read_bytes()
    )
    artifact = manifest.aggregate
    validate_visual_review_batch_bundle(
        manifest,
        artifact,
        expected_run_id=expected_run_id,
    )
    if (
        not paths.window_visual_review.exists()
        or WindowVisualReviewArtifact.model_validate_json(
            paths.window_visual_review.read_bytes()
        )
        != artifact
    ):
        write_model_atomic(paths.window_visual_review, artifact)
    return artifact


__all__ = [
    "publish_visual_review_bundle",
    "recover_visual_review_bundle",
]
