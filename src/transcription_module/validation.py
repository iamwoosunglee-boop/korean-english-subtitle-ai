# Copyright 2026 Transcription Module contributors

"""Canonical JSON and derived CSV validation stage."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import override

from transcription_module.artifact_io import confined_exists
from transcription_module.canonical_source_export import (
    CanonicalSourceExportError,
    build_canonical_source_pair,
)
from transcription_module.final_review import (
    FinalReviewError,
    assert_current_final_review,
)
from transcription_module.models import (
    RunId,
    RunState,
    Transcript,
    ValidationRunArtifact,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.source_edit_errors import (
    SourceEditFailure,
    SourceEditLifecycleError,
)
from transcription_module.storage import RunStore, transcript_as_csv, write_model_atomic
from transcription_module.validation_findings import validation_findings
from translation_module.cli_io import write_models_atomic


@dataclass(slots=True)
class ValidationContractError(Exception):
    """Canonical transcript artifacts violate their persisted contract."""

    message: str

    @override
    def __str__(self) -> str:
        return self.message


def working_transcript_digest(transcript: Transcript) -> str:
    """Digest semantic source bytes while ignoring lifecycle-only fields."""
    semantic = transcript.model_copy(
        update={
            "workflow_status": WorkflowStatus.PROCESSING,
            "validation_status": ValidationStatus.PENDING,
        }
    )
    return hashlib.sha256(semantic.model_dump_json().encode()).hexdigest()


def _validate_run_unlocked(
    store: RunStore,
    run_id: RunId,
) -> ValidationRunArtifact:
    """Validate current review bytes and publish one canonical result."""
    if confined_exists(store.paths(run_id).source_human_edit):
        raise SourceEditLifecycleError(SourceEditFailure.REVALIDATION)
    try:
        _ = assert_current_final_review(store, run_id)
    except FinalReviewError as error:
        raise ValidationContractError(str(error)) from error
    transcript = store.load_transcript(run_id)
    state = store.load_state(run_id)
    paths = store.paths(run_id)
    expected_csv = transcript_as_csv(transcript)
    actual_csv = paths.transcript_csv.read_text(encoding="utf-8")
    structure_issues = (
        ("derived CSV does not match canonical transcript JSON",)
        if actual_csv != expected_csv
        else ()
    )
    findings = validation_findings(transcript)
    quality_issues = tuple(finding.issue for finding in findings)
    status = (
        ValidationStatus.INVALID
        if structure_issues or quality_issues
        else ValidationStatus.VALID
    )
    artifact = ValidationRunArtifact(
        run_id=run_id,
        validation_status=status,
        transcript_path=str(paths.transcript),
        csv_path=str(paths.transcript_csv),
        cue_count=len(transcript.cues),
        structure_issues=structure_issues,
        quality_issues=quality_issues,
        findings=findings,
    )
    blocking = tuple(
        finding.issue for finding in findings if finding.code.blocks_source_finalization
    )
    if blocking:
        write_model_atomic(paths.validation_artifact, artifact)
        raise ValidationContractError("; ".join(blocking))
    workflow = (
        WorkflowStatus.FINALIZED
        if status is ValidationStatus.VALID
        and transcript.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
        else transcript.workflow_status
    )
    validated_transcript = transcript.model_copy(
        update={"validation_status": status, "workflow_status": workflow}
    )
    validated_state = RunState(
        run_id=state.run_id,
        source_url=state.source_url,
        workflow_status=workflow,
        validation_status=status,
        context=state.context,
    )
    if status is ValidationStatus.VALID and workflow is WorkflowStatus.FINALIZED:
        try:
            context, source = build_canonical_source_pair(
                store,
                run_id,
                state=validated_state,
                transcript=validated_transcript,
            )
        except (CanonicalSourceExportError, OSError, ValueError) as error:
            raise ValidationContractError(str(error)) from error
        write_models_atomic(
            (
                (paths.input_context, context),
                (paths.canonical_source, source),
                (paths.validation_artifact, artifact),
                (paths.transcript, validated_transcript),
                (paths.state, validated_state),
            )
        )
    else:
        write_models_atomic(
            (
                (paths.validation_artifact, artifact),
                (paths.transcript, validated_transcript),
                (paths.state, validated_state),
            )
        )
    if status is ValidationStatus.INVALID:
        raise ValidationContractError("; ".join((*structure_issues, *quality_issues)))
    return artifact


def validate_run(store: RunStore, run_id: RunId) -> ValidationRunArtifact:
    """Validate current artifacts while owning the run lock."""
    with store.lock_run(run_id):
        return _validate_run_unlocked(store, run_id)


__all__ = [
    "ValidationRunArtifact",
    "validation_findings",
    "working_transcript_digest",
]
