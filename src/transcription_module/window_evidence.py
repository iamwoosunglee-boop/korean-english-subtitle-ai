# Copyright 2026 Transcription Module contributors

"""Public policy-window validation and deterministic artifact replay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.visual_review_validation import (
    validate_candidate_dispositions,
    validate_provisional_visual_insertions,
)
from transcription_module.window_cue_identity import window_cue_identity
from transcription_module.window_evidence_errors import (
    WindowEvidenceError,
    fail_window_evidence,
)
from transcription_module.window_evidence_validation import (
    validate_policy_window_draft,
)

if TYPE_CHECKING:
    from transcription_module._drafting_models import (
        WindowDraft,
        WindowDraftArtifact,
        WindowDraftInput,
    )
    from transcription_module.models import RunId


def validate_window_draft(
    window_input: WindowDraftInput,
    draft: WindowDraft,
) -> None:
    """Reject unsupported policy output; retain read-only legacy behavior."""
    if draft.window_index != window_input.window_index:
        fail_window_evidence("window draft index does not match its input")
    if window_input.visual_review is not None:
        try:
            validate_candidate_dispositions(window_input.visual_review, draft)
            validate_provisional_visual_insertions(window_input, draft)
        except ValueError as error:
            message = f"window evidence validation failed: {error}"
            raise WindowEvidenceError(message) from error
    if window_input.era is EvidenceEra.LEGACY:
        return
    try:
        validate_policy_window_draft(window_input, draft)
    except WindowEvidenceError:
        raise
    except ValueError as error:
        message = f"window evidence validation failed: {error}"
        raise WindowEvidenceError(message) from error


def validate_window_draft_artifact(
    artifact: WindowDraftArtifact,
    *,
    expected_run_id: RunId | None = None,
) -> None:
    """Replay every persisted input/result pair under one exact run identity."""
    if expected_run_id is not None and artifact.run_id != expected_run_id:
        fail_window_evidence("window artifact has a foreign run identity")
    if any(
        (
            window_input.era is EvidenceEra.POLICY_BOUND
            or window_input.run_id is not None
        )
        and window_input.run_id != artifact.run_id
        for window_input in artifact.inputs
    ):
        fail_window_evidence("window artifact run differs from its input run")
    if len(artifact.inputs) != len(artifact.drafts):
        fail_window_evidence("window artifact input and draft counts differ")
    for window_input, draft in zip(artifact.inputs, artifact.drafts, strict=True):
        validate_window_draft(window_input, draft)


__all__ = [
    "WindowEvidenceError",
    "validate_window_draft",
    "validate_window_draft_artifact",
    "window_cue_identity",
]
