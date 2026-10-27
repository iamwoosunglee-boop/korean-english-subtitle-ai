# Copyright 2026 Transcription Module contributors

"""Validation belongs to the exact transcript bytes that were checked."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

import pytest

from tests.unit.validation_lifecycle_support import (
    approve_final_review_fixture as _approve_final_review,
)
from tests.unit.validation_lifecycle_support import (
    transcript_fixture as _transcript,
)
from tests.unit.validation_lifecycle_support import (
    validation_store_fixture as _store,
)
from transcription_module.models import (
    Cue,
    Milliseconds,
    RunId,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.storage import (
    transcript_as_csv,
    write_model_atomic,
)
from transcription_module.validation import (
    ValidationContractError,
    ValidationRunArtifact,
    validate_run,
)
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.cli_io import write_models_atomic

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


def test_validation_requires_current_complete_final_review_before_csv_access(
    tmp_path: Path,
) -> None:
    """A missing receipt fails closed before structural validation starts."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증할 문장"))
    paths = store.paths(run_id)
    paths.transcript_csv.unlink()

    with pytest.raises(
        ValidationContractError,
        match="valid final review receipt is required",
    ):
        _ = validate_run(store, run_id)

    assert not paths.validation_artifact.exists()
    assert store.load_transcript(run_id).validation_status is ValidationStatus.PENDING
    assert store.load_transcript(run_id).workflow_status is WorkflowStatus.PROCESSING
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING
    assert store.load_state(run_id).workflow_status is WorkflowStatus.PROCESSING


@pytest.mark.parametrize(
    "receipt_kind",
    ["malformed", "wrong-run", "incomplete", "stale"],
)
def test_validation_rejects_noncurrent_final_review_receipts(
    tmp_path: Path,
    receipt_kind: str,
) -> None:
    """Malformed, foreign, incomplete, and stale receipts cannot validate."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증할 문장"))
    _approve_final_review(store, run_id)
    paths = store.paths(run_id)
    receipt = store.load_final_review_artifact(run_id)
    if receipt_kind == "malformed":
        _ = paths.final_review_artifact.write_text("{", encoding="utf-8")
    elif receipt_kind == "wrong-run":
        write_model_atomic(
            paths.final_review_artifact,
            receipt.model_copy(update={"run_id": RunId("another-run")}),
        )
    elif receipt_kind == "incomplete":
        payload = receipt.model_dump(mode="json")
        del payload["transcript_digest"]
        _ = paths.final_review_artifact.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    else:
        changed = _transcript(run_id, "영수증 이후 수정한 문장")
        write_model_atomic(paths.transcript, changed)

    with pytest.raises(ValidationContractError, match="final review"):
        _ = validate_run(store, run_id)

    assert not paths.validation_artifact.exists()
    assert store.load_transcript(run_id).validation_status is ValidationStatus.PENDING
    assert store.load_transcript(run_id).workflow_status is WorkflowStatus.PROCESSING
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING
    assert store.load_state(run_id).workflow_status is WorkflowStatus.PROCESSING


def test_validation_rejects_receipt_without_applied_result(tmp_path: Path) -> None:
    """A forged receipt cannot substitute for an applied complete result."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증할 문장"))
    _approve_final_review(store, run_id)
    paths = store.paths(run_id)
    paths.final_review_result.unlink()

    with pytest.raises(ValidationContractError, match="final review"):
        _ = validate_run(store, run_id)

    assert not paths.validation_artifact.exists()
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING


def test_validate_run_waits_for_public_run_lock_during_apply_commit(
    tmp_path: Path,
) -> None:
    """Validation cannot overlap another thread's simulated apply commit."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증할 문장"))
    _approve_final_review(store, run_id)
    lock_held = threading.Event()
    release = threading.Event()
    validate_attempted = threading.Event()
    validate_done = threading.Event()
    results: list[ValidationRunArtifact] = []
    errors: list[Exception] = []

    def hold_lock() -> None:
        with store.lock_run(run_id):
            lock_held.set()
            _ = release.wait()

    def validate() -> None:
        try:
            validate_attempted.set()
            results.append(validate_run(store, run_id))
        except (OSError, ValidationContractError) as error:
            errors.append(error)
        finally:
            validate_done.set()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_held.wait(timeout=1.0)
    thread = threading.Thread(target=validate)
    thread.start()
    assert validate_attempted.wait(timeout=1.0)
    release.set()

    holder.join(timeout=1.0)
    thread.join(timeout=1.0)
    assert validate_done.is_set()
    assert not thread.is_alive()
    assert errors == []
    assert len(results) == 1
    assert results[0].validation_status is ValidationStatus.VALID
    assert results[0].structure_issues == ()
    assert results[0].quality_issues == ()
    assert store.paths(run_id).validation_artifact.exists()


def test_transcript_mutation_waits_for_validation_and_invalidates_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutation cannot interleave after receipt check and before finalization."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증할 문장"))
    _approve_final_review(store, run_id)
    validation_writing = threading.Event()
    release_validation = threading.Event()
    mutation_attempted = threading.Event()
    mutation_done = threading.Event()
    errors: list[Exception] = []
    real_write_models_atomic = write_models_atomic
    validation_path = store.paths(run_id).validation_artifact

    def pause_validation_write(
        models: tuple[tuple[Path, BaseModel], ...],
    ) -> None:
        if any(path == validation_path for path, _ in models):
            validation_writing.set()
            assert release_validation.wait(timeout=1.0)
        real_write_models_atomic(models)

    def validate() -> None:
        try:
            _ = validate_run(store, run_id)
        except (OSError, ValidationContractError) as error:
            errors.append(error)

    def mutate() -> None:
        try:
            mutation_attempted.set()
            store.save_transcript(_transcript(run_id, "검증 중 수정"))
        except OSError as error:
            errors.append(error)
        finally:
            mutation_done.set()

    monkeypatch.setattr(
        "transcription_module.validation.write_models_atomic",
        pause_validation_write,
    )
    validator = threading.Thread(target=validate)
    validator.start()
    assert validation_writing.wait(timeout=1.0)
    mutator = threading.Thread(target=mutate)
    mutator.start()
    assert mutation_attempted.wait(timeout=1.0)
    release_validation.set()
    validator.join(timeout=1.0)
    mutator.join(timeout=1.0)

    assert errors == []
    assert mutation_done.is_set()
    assert store.load_transcript(run_id).workflow_status is not WorkflowStatus.FINALIZED
    assert store.load_state(run_id).workflow_status is not WorkflowStatus.FINALIZED
    assert not store.paths(run_id).final_review_artifact.exists()


def test_validation_applies_only_until_transcript_changes(tmp_path: Path) -> None:
    """Any transcript edit invalidates its previously valid result."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증할 문장"))
    _approve_final_review(store, run_id)

    result = validate_run(store, run_id)

    assert result.validation_status is ValidationStatus.VALID
    assert store.load_transcript(run_id).validation_status is ValidationStatus.VALID
    assert store.load_state(run_id).validation_status is ValidationStatus.VALID

    store.save_transcript(_transcript(run_id, "수정한 문장"))

    assert store.load_transcript(run_id).validation_status is ValidationStatus.PENDING
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING
    assert not store.paths(run_id).validation_artifact.exists()


def test_failed_transcript_write_cannot_leave_old_validation(
    tmp_path: Path,
) -> None:
    """A failed changed-content write still clears stale validity."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증한 문장"))
    _approve_final_review(store, run_id)
    _ = validate_run(store, run_id)
    paths = store.paths(run_id)
    _ = paths.input_context.write_text("context", encoding="utf-8")
    _ = paths.canonical_source.write_text("source", encoding="utf-8")
    paths.transcript_csv.unlink()
    paths.transcript_csv.mkdir()

    with pytest.raises(OSError, match=r"[Dd]irectory"):
        store.save_transcript(_transcript(run_id, "저장에 실패할 문장"))

    assert store.load_transcript(run_id).validation_status is ValidationStatus.PENDING
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING
    assert not paths.validation_artifact.exists()
    assert not paths.input_context.exists()
    assert not paths.canonical_source.exists()


def test_changed_transcript_invalidates_canonical_source_pair(
    tmp_path: Path,
) -> None:
    """A changed transcript removes stale canonical handoff files."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "검증한 문장"))
    paths = store.paths(run_id)
    _ = paths.input_context.write_text("context", encoding="utf-8")
    _ = paths.canonical_source.write_text("source", encoding="utf-8")
    _ = paths.adapter_events.write_text("keep\n", encoding="utf-8")

    store.save_transcript(_transcript(run_id, "수정한 문장"))

    assert not paths.input_context.exists()
    assert not paths.canonical_source.exists()
    assert paths.adapter_events.exists()
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING


def test_validation_rejects_structurally_valid_repetition(
    tmp_path: Path,
) -> None:
    """Quality validation rejects pathological ASR repetition."""
    store, run_id = _store(tmp_path)
    repeated = _transcript(run_id, " ".join(["감사합니다"] * 8))
    store.save_transcript(repeated)
    _approve_final_review(store, run_id)

    with pytest.raises(ValidationContractError, match="pathological repetition"):
        _ = validate_run(store, run_id)

    artifact = ValidationRunArtifact.model_validate_json(
        store.paths(run_id).validation_artifact.read_text(encoding="utf-8")
    )
    assert artifact.validation_status is ValidationStatus.INVALID
    assert artifact.structure_issues == ()
    assert artifact.quality_issues == ("cue 1 has pathological repetition",)
    assert store.load_transcript(run_id).validation_status is ValidationStatus.INVALID
    assert store.load_state(run_id).validation_status is ValidationStatus.INVALID


def test_validation_allows_simultaneous_speakers(tmp_path: Path) -> None:
    """Time overlap remains valid when speakers talk simultaneously."""
    store, run_id = _store(tmp_path)
    transcript = _transcript(run_id, "첫 번째 화자").model_copy(
        update={
            "cues": (
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_200),
                    speech="첫 번째 화자",
                    speaker="가",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    start_tc_ms=Milliseconds(800),
                    end_tc_ms=Milliseconds(1_800),
                    speech="두 번째 화자",
                    speaker="나",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            )
        }
    )
    store.save_transcript(transcript)
    _approve_final_review(store, run_id)

    result = validate_run(store, run_id)

    assert result.validation_status is ValidationStatus.VALID
    assert result.quality_issues == ()


def test_validation_allows_sequential_repeated_utterances(tmp_path: Path) -> None:
    """Repeated chorus lines in distinct intervals are valid cues."""
    store, run_id = _store(tmp_path)
    transcript = _transcript(run_id, "후렴입니다").model_copy(
        update={
            "duration_ms": Milliseconds(3_000),
            "cues": (
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(500),
                    speech="후렴입니다",
                    speaker="가",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    start_tc_ms=Milliseconds(900),
                    end_tc_ms=Milliseconds(1_400),
                    speech="후렴입니다",
                    speaker="가",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
        }
    )
    store.save_transcript(transcript)
    _approve_final_review(store, run_id)

    assert validate_run(store, run_id).quality_issues == ()


def test_validation_allows_different_speakers_repeating_dialogue(
    tmp_path: Path,
) -> None:
    """Alternating speakers may say the same words in separate cues."""
    store, run_id = _store(tmp_path)
    transcript = _transcript(run_id, "네").model_copy(
        update={
            "duration_ms": Milliseconds(3_000),
            "cues": (
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(500),
                    speech="네",
                    speaker="가",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    start_tc_ms=Milliseconds(600),
                    end_tc_ms=Milliseconds(1_100),
                    speech="네",
                    speaker="나",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
        }
    )
    store.save_transcript(transcript)
    _approve_final_review(store, run_id)

    assert validate_run(store, run_id).quality_issues == ()


def test_validation_rejects_overlapping_duplicate(tmp_path: Path) -> None:
    """The same speaker and words in overlapping intervals remain invalid."""
    store, run_id = _store(tmp_path)
    transcript = _transcript(run_id, "중복").model_copy(
        update={
            "duration_ms": Milliseconds(3_000),
            "cues": (
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="중복",
                    speaker="가",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    start_tc_ms=Milliseconds(900),
                    end_tc_ms=Milliseconds(1_400),
                    speech="중복",
                    speaker="가",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
        }
    )
    store.save_transcript(transcript)
    _approve_final_review(store, run_id)

    with pytest.raises(ValidationContractError, match="duplicates the preceding cue"):
        _ = validate_run(store, run_id)


def test_only_validated_whole_transcript_becomes_final(
    tmp_path: Path,
) -> None:
    """Final lifecycle status belongs only to a validated whole transcript."""
    store, run_id = _store(tmp_path)
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    store.save_transcript(
        _transcript(run_id, "최종 검증 문장").model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    _approve_final_review(store, run_id)

    _ = validate_run(store, run_id)

    assert store.load_transcript(run_id).workflow_status is WorkflowStatus.FINALIZED
    assert store.load_state(run_id).workflow_status is WorkflowStatus.FINALIZED
    paths = store.paths(run_id)
    context = ContentContextAsset.model_validate_json(
        paths.input_context.read_text(encoding="utf-8")
    )
    source = SourceTranscriptV2.model_validate_json(
        paths.canonical_source.read_text(encoding="utf-8")
    )
    assert source.input_context_ref.content_digest == context.content_digest
    assert source.lifecycle_status.value == "draft"


def test_validation_preserves_sequential_speaker_line_order(tmp_path: Path) -> None:
    """Canonical multiline turns remain valid through finalization."""
    store, run_id = _store(tmp_path)
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={"workflow_status": WorkflowStatus.READY_FOR_VALIDATION}
        )
    )
    transcript = _transcript(run_id, "좋습니다.\n너무 좋아, 너무 좋아.")
    store.save_transcript(
        transcript.model_copy(
            update={
                "workflow_status": WorkflowStatus.READY_FOR_VALIDATION,
                "cues": (
                    transcript.cues[0].model_copy(update={"speaker": "루미|한지아"}),
                ),
            }
        )
    )
    _approve_final_review(store, run_id)

    artifact = validate_run(store, run_id)

    assert artifact.validation_status is ValidationStatus.VALID
    assert store.load_transcript(run_id).cues[0].speech == (
        "좋습니다.\n너무 좋아, 너무 좋아."
    )


def test_revalidation_recomputes_with_current_review_receipt(tmp_path: Path) -> None:
    """A current receipt gates validation but does not cache its result."""
    store, run_id = _store(tmp_path)
    store.save_transcript(_transcript(run_id, "재검증할 문장"))
    _approve_final_review(store, run_id)

    assert validate_run(store, run_id).validation_status is ValidationStatus.VALID

    paths = store.paths(run_id)
    _ = paths.transcript_csv.write_text("stale csv\n", encoding="utf-8")
    with pytest.raises(ValidationContractError, match="derived CSV"):
        _ = validate_run(store, run_id)
    assert store.load_state(run_id).validation_status is ValidationStatus.INVALID

    _ = paths.transcript_csv.write_text(
        transcript_as_csv(store.load_transcript(run_id)),
        encoding="utf-8",
    )
    assert validate_run(store, run_id).validation_status is ValidationStatus.VALID
