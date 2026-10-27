# Copyright 2026 Transcription Module contributors

"""End-to-end tests for the final-review CLI surface."""

from __future__ import annotations

import csv
import json
from typing import TYPE_CHECKING, cast

from tests.e2e import test_cli_boundaries
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.final_review import assert_current_final_review
from transcription_module.final_review_models import (
    FinalReviewAssignment,
    FinalReviewResult,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.models import (
    CueId,
    ProcessResult,
    RunId,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.storage import CSV_COLUMNS, RunStore, write_model_atomic
from transcription_module.validation import ValidationRunArtifact

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path
    from typing import Protocol

    class _CliRunner(Protocol):
        def __call__(
            self,
            *arguments: str,
            runs_dir: Path,
            use_fixture: bool = False,
        ) -> subprocess.CompletedProcess[str]: ...

    class _DraftCreator(Protocol):
        def __call__(
            self,
            runs_dir: Path,
            *,
            accept_confirmation: bool = True,
        ) -> tuple[str, Path]: ...


CLI_ERROR_EXIT_CODE = 2
_create_draft = cast("_DraftCreator", vars(test_cli_boundaries)["_create_draft"])
_run_cli = cast("_CliRunner", vars(test_cli_boundaries)["_run_cli"])


def _create_final_review_draft(runs_dir: Path) -> tuple[str, Path]:
    run_id, run_dir = _create_draft(
        runs_dir,
        accept_confirmation=False,
    )
    store = RunStore(runs_dir)
    typed_run_id = RunId(run_id)
    paths = store.paths(typed_run_id)
    transcript = store.load_transcript(typed_run_id)
    write_model_atomic(
        paths.transcript,
        transcript.model_copy(
            update={
                "cues": tuple(
                    cue.model_copy(update={"cue_id": CueId(f"cue-{cue.sequence}")})
                    for cue in transcript.cues
                )
            }
        ),
    )
    consented = _run_cli(
        "confirmation-consent",
        run_id,
        "--accept",
        runs_dir=runs_dir,
    )
    assert consented.returncode == 0, consented.stderr
    return run_id, run_dir


def _apply_clean_review_via_cli(
    runs_dir: Path,
) -> tuple[str, Path, RunStore]:
    """Create and apply one complete review through the public CLI."""
    run_id, run_dir = _create_final_review_draft(runs_dir)
    store = RunStore(runs_dir)
    typed_run_id = RunId(run_id)
    confirmed = _run_cli(
        "confirm",
        run_id,
        "cue-2-speech",
        "--answer",
        "불확실한 문장",
        runs_dir=runs_dir,
    )
    assert confirmed.returncode == 0, confirmed.stderr
    transcript = store.load_transcript(typed_run_id)
    store.save_transcript(
        transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0],
                    transcript.cues[1].model_copy(
                        update={"speaker": "확정 화자"}
                    ),
                )
            }
        )
    )
    prepared = _run_cli("prepare-final-review", run_id, runs_dir=runs_dir)
    assert prepared.returncode == 0, prepared.stderr
    assignment = FinalReviewAssignment.model_validate_json(
        store.paths(typed_run_id).final_review_assignment.read_text(
            encoding="utf-8"
        )
    )
    result = FinalReviewResult(
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
            )
            for segment in assignment.segments
        ),
    )
    result_path = runs_dir.parent / "complete-review.json"
    _ = result_path.write_text(result.model_dump_json(), encoding="utf-8")
    applied = _run_cli(
        "apply-final-review",
        run_id,
        str(result_path),
        runs_dir=runs_dir,
    )
    assert applied.returncode == 0, applied.stderr
    return run_id, run_dir, store


def test_final_review_cli_help(tmp_path: Path) -> None:
    """Expose both final-review commands through the public CLI."""
    runs_dir = tmp_path / "runs"
    help_result = _run_cli("prepare-final-review", "--help", runs_dir=runs_dir)
    assert help_result.returncode == 0
    assert "prepare-final-review" in help_result.stdout
    apply_help = _run_cli("apply-final-review", "--help", runs_dir=runs_dir)
    assert apply_help.returncode == 0
    assert "apply-final-review" in apply_help.stdout


def test_final_review_cli_prepare_apply(tmp_path: Path) -> None:
    """Prepare and apply a valid final-review result through the CLI."""
    runs_dir = tmp_path / "runs"
    run_id, run_dir = _create_final_review_draft(runs_dir)
    store = RunStore(runs_dir)
    typed_run_id = RunId(run_id)
    confirmed = _run_cli(
        "confirm",
        run_id,
        "cue-2-speech",
        "--answer",
        "불확실한 문장",
        runs_dir=runs_dir,
    )
    assert confirmed.returncode == 0, confirmed.stderr
    assert (
        ProcessResult.model_validate_json(confirmed.stdout).workflow_status
        is WorkflowStatus.READY_FOR_VALIDATION
    )
    transcript = store.load_transcript(typed_run_id)
    store.save_transcript(
        transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0],
                    transcript.cues[1].model_copy(
                        update={"speaker": "확정 화자"}
                    ),
                )
            }
        )
    )

    prepared = _run_cli("prepare-final-review", run_id, runs_dir=runs_dir)
    assert prepared.returncode == 0, prepared.stderr
    assignment_path = run_dir / "final-review-assignment.json"
    assert assignment_path.exists()
    assert json.loads(prepared.stdout)["assignment_path"] == str(assignment_path)

    assignment = FinalReviewAssignment.model_validate_json(
        assignment_path.read_text(encoding="utf-8")
    )
    result = FinalReviewResult(
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
    result_path = tmp_path / "result.json"
    _ = result_path.write_text(result.model_dump_json(), encoding="utf-8")
    applied = _run_cli(
        "apply-final-review", run_id, str(result_path), runs_dir=runs_dir
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["status"] == "applied"
    final_review_path = run_dir / "final-review.json"
    assert final_review_path.exists()
    final_review = store.load_final_review_artifact(typed_run_id)

    validated = _run_cli("validate", run_id, runs_dir=runs_dir)

    assert validated.returncode == 0, validated.stderr
    validation_path = run_dir / "validation.json"
    assert validation_path.exists()
    validation = ValidationRunArtifact.model_validate_json(
        validation_path.read_text(encoding="utf-8")
    )
    assert ValidationRunArtifact.model_validate_json(validated.stdout) == validation
    assert validation.validation_status is ValidationStatus.VALID
    assert validation.structure_issues == ()
    assert validation.quality_issues == ()

    state = store.load_state(typed_run_id)
    transcript = store.load_transcript(typed_run_id)
    assert state.workflow_status is WorkflowStatus.FINALIZED
    assert state.validation_status is ValidationStatus.VALID
    assert transcript.workflow_status is WorkflowStatus.FINALIZED
    assert transcript.validation_status is ValidationStatus.VALID
    assert assert_current_final_review(store, typed_run_id) == final_review

    with (run_dir / "transcript.csv").open(
        encoding="utf-8", newline=""
    ) as output:
        reader = csv.DictReader(output)
        assert reader.fieldnames == list(CSV_COLUMNS)
        csv_rows = list(reader)
    assert csv_rows == [
        {
            "sequence": str(cue.sequence),
            "cue_id": str(cue.cue_id or ""),
            "start_tc_ms": str(cue.start_tc_ms),
            "end_tc_ms": str(cue.end_tc_ms),
            "speech": cue.speech,
            "speaker": cue.speaker or "",
            "uncertainty": cue.uncertainty.value,
            "screen_text": cue.screen_text or "",
            "vision_evidence_ids": "|".join(cue.vision_evidence_ids),
        }
        for cue in transcript.cues
    ]


def test_cli_validate_fails_closed_without_final_review(tmp_path: Path) -> None:
    """The real validate command rejects an otherwise prepared draft."""
    runs_dir = tmp_path / "runs"
    run_id, _ = _create_final_review_draft(runs_dir)
    store = RunStore(runs_dir)
    typed_run_id = RunId(run_id)

    failed = _run_cli("validate", run_id, runs_dir=runs_dir)

    assert failed.returncode == CLI_ERROR_EXIT_CODE
    assert (
        ErrorEnvelope.model_validate_json(failed.stderr).error.code
        is ErrorCode.VALIDATION_FAILED
    )
    paths = store.paths(typed_run_id)
    assert not paths.validation_artifact.exists()
    assert not paths.final_review_artifact.exists()
    assert not paths.final_review_result.exists()
    assert (
        store.load_state(typed_run_id).workflow_status
        is not WorkflowStatus.FINALIZED
    )


def test_cli_validate_rejects_post_review_transcript_mutation(
    tmp_path: Path,
) -> None:
    """A public transcript mutation invalidates review before real validation."""
    runs_dir = tmp_path / "runs"
    run_id, _, store = _apply_clean_review_via_cli(runs_dir)
    typed_run_id = RunId(run_id)
    paths = store.paths(typed_run_id)
    assert paths.final_review_artifact.exists()
    assert paths.final_review_result.exists()
    transcript = store.load_transcript(typed_run_id)
    store.save_transcript(
        transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0].model_copy(
                        update={"speech": "검수 뒤 변경된 문장"}
                    ),
                    *transcript.cues[1:],
                )
            }
        )
    )

    failed = _run_cli("validate", run_id, runs_dir=runs_dir)

    assert failed.returncode == CLI_ERROR_EXIT_CODE
    assert (
        ErrorEnvelope.model_validate_json(failed.stderr).error.code
        is ErrorCode.VALIDATION_FAILED
    )
    assert not paths.validation_artifact.exists()
    assert not paths.final_review_artifact.exists()
    assert not paths.final_review_result.exists()
    assert (
        store.load_state(typed_run_id).workflow_status
        is not WorkflowStatus.FINALIZED
    )


def test_final_review_cli_rejects_malformed_and_wrong_run(tmp_path: Path) -> None:
    """Reject malformed results and results belonging to another run."""
    runs_dir = tmp_path / "runs"
    run_id, _ = _create_final_review_draft(runs_dir)
    malformed = tmp_path / "malformed.json"
    _ = malformed.write_text("{broken", encoding="utf-8")
    failed = _run_cli(
        "apply-final-review", run_id, str(malformed), runs_dir=runs_dir
    )
    assert failed.returncode == CLI_ERROR_EXIT_CODE
    assert (
        ErrorEnvelope.model_validate_json(failed.stderr).error.code
        is ErrorCode.VALIDATION_FAILED
    )

    _ = _run_cli("prepare-final-review", run_id, runs_dir=runs_dir)
    assignment = FinalReviewAssignment.model_validate_json(
        (tmp_path / "runs" / run_id / "final-review-assignment.json").read_text()
    )
    wrong = FinalReviewResult(
        run_id=RunId("00000000-0000-4000-8000-000000000000"),
        assignment_digest=assignment.assignment_digest,
        segments=tuple(
            ReviewSegmentResult(
                segment_id=s.segment_id,
                frame_ids=s.frame_ids,
                cue_ids=s.cue_ids,
                frame_observations=tuple(
                    FrameReviewObservation(
                        frame_id=frame_id,
                        visible_screen_text=(),
                        compared_cue_ids=s.cue_ids,
                    )
                    for frame_id in s.frame_ids
                ),
            )
            for s in assignment.segments
        ),
    )
    wrong_path = tmp_path / "wrong.json"
    _ = wrong_path.write_text(wrong.model_dump_json())
    failed = _run_cli(
        "apply-final-review",
        run_id,
        str(wrong_path),
        runs_dir=runs_dir,
    )
    assert failed.returncode == CLI_ERROR_EXIT_CODE
    assert (
        ErrorEnvelope.model_validate_json(failed.stderr).error.code
        is ErrorCode.VALIDATION_FAILED
    )
