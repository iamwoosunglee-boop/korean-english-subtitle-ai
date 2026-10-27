# Copyright (c) 2026
"""End-to-end recovery and resumable-confirmation behavior."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

import pytest

from transcription_module.confirmation import (
    apply_next_confirmation_answer,
    create_confirmation_session,
    set_confirmation_consent,
)
from transcription_module.confirmation_log import (
    append_confirmation_requests,
    next_confirmation_item,
)
from transcription_module.confirmation_models import (
    CONFIRMATION_RECORD_ADAPTER,
    ConfirmationError,
    ConfirmationErrorCode,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.models import (
    Cue,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.recovery import (
    CueCoverageInterval,
    LargeV3Recovery,
    MissingSpeechInterval,
    RecoveredSpeech,
    RecoveryContext,
    UnresolvedSpeech,
    VadSpeechInterval,
    decide_large_v3_recovery,
    find_missing_speech,
)
from transcription_module.recovery_stage import queue_vad_recovery
from transcription_module.storage import RunStore

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_INSERTED_SEQUENCE = 2


def make_store(tmp_path: Path) -> tuple[RunStore, RunId, Path]:
    """Create a valid draft with two unresolved cues."""
    runs_dir = tmp_path / "runs"
    store = RunStore(runs_dir)
    run_id = RunId("00000000-0000-0000-0000-000000000004")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=recovery-fixture"
            ),
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            validation_status=ValidationStatus.VALID,
        )
    )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            validation_status=ValidationStatus.VALID,
            duration_ms=Milliseconds(8_000),
            cues=(
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(2_000),
                    speech="앞 문장",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    start_tc_ms=Milliseconds(2_500),
                    end_tc_ms=Milliseconds(4_000),
                    speech="현재 초안",
                    uncertainty=Uncertainty.NEEDS_CONFIRMATION,
                ),
                Cue(
                    sequence=3,
                    start_tc_ms=Milliseconds(5_000),
                    end_tc_ms=Milliseconds(6_500),
                    speech="다음 초안",
                    uncertainty=Uncertainty.NEEDS_CONFIRMATION,
                ),
            ),
            vision_evidence=(),
        )
    )
    return store, run_id, runs_dir / str(run_id)


def make_items() -> tuple[ConfirmationItem, ConfirmationItem]:
    """Return ordered speech and speaker confirmation fixtures."""
    return (
        ConfirmationItem(
            confirmation_id=ConfirmationId("recover-2500"),
            cue_sequence=2,
            field=ConfirmationField.SPEECH,
            start_tc_ms=Milliseconds(2_500),
            end_tc_ms=Milliseconds(4_000),
            current_value="현재 초안",
            candidates=("복원된 문장",),
            evidence=("large-v3 context: 앞 문장 / 다음 초안",),
        ),
        ConfirmationItem(
            confirmation_id=ConfirmationId("speaker-5000"),
            cue_sequence=3,
            field=ConfirmationField.SPEAKER,
            start_tc_ms=Milliseconds(5_000),
            end_tc_ms=Milliseconds(6_500),
            current_value=None,
            candidates=("민수", "미확인 인물"),
            evidence=("vision frame f000123",),
        ),
    )


def activate_confirmation(store: RunStore, run_id: RunId) -> None:
    """Start and explicitly accept one test confirmation attempt."""
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)


def test_missing_vad_coverage_routes_contextual_large_v3_result() -> None:
    """Subtract cue coverage and route confident versus unresolved output."""
    vad = (
        VadSpeechInterval(
            start_tc_ms=Milliseconds(1_800),
            end_tc_ms=Milliseconds(3_200),
        ),
    )
    cues = (
        CueCoverageInterval(
            start_tc_ms=Milliseconds(1_500),
            end_tc_ms=Milliseconds(2_000),
        ),
        CueCoverageInterval(
            start_tc_ms=Milliseconds(3_000),
            end_tc_ms=Milliseconds(3_500),
        ),
    )

    missing = find_missing_speech(vad, cues)
    context = RecoveryContext(
        missing_interval=missing[0],
        preceding_speech="앞 문장",
        following_speech="뒤 문장",
    )
    recovered = decide_large_v3_recovery(
        context,
        LargeV3Recovery(
            speech="가운데 복원 문장",
            confident=True,
            evidence=("large-v3 decoded with both neighbors",),
        ),
    )
    unresolved = decide_large_v3_recovery(
        context,
        LargeV3Recovery(
            speech="후보 문장",
            confident=False,
            evidence=("two plausible decodes",),
        ),
    )

    assert missing == (
        MissingSpeechInterval(
            start_tc_ms=Milliseconds(2_000),
            end_tc_ms=Milliseconds(3_000),
        ),
    )
    assert recovered == RecoveredSpeech(
        interval=missing[0],
        speech="가운데 복원 문장",
        evidence=("large-v3 decoded with both neighbors",),
    )
    assert unresolved == UnresolvedSpeech(
        interval=missing[0],
        current_value="",
        candidates=("후보 문장",),
        evidence=("two plausible decodes",),
    )


def test_vad_gap_becomes_ordered_confirmation_cue(tmp_path: Path) -> None:
    """A real-stage VAD gap is inserted and queued for one-at-a-time input."""
    runs_dir = tmp_path / "runs"
    store = RunStore(runs_dir)
    run_id = RunId("00000000-0000-0000-0000-000000000006")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=recovery-stage"
            ),
            workflow_status=WorkflowStatus.PROCESSING,
            validation_status=ValidationStatus.VALID,
        )
    )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.PROCESSING,
            validation_status=ValidationStatus.VALID,
            duration_ms=Milliseconds(3_000),
            cues=(
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="앞 문장",
                    uncertainty=Uncertainty.CERTAIN,
                ),
                Cue(
                    sequence=2,
                    start_tc_ms=Milliseconds(2_000),
                    end_tc_ms=Milliseconds(3_000),
                    speech="뒤 문장",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
            vision_evidence=(),
        )
    )

    artifact = queue_vad_recovery(
        store,
        run_id,
        (
            VadSpeechInterval(
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(3_000),
            ),
        ),
    )

    transcript = store.load_transcript(run_id)
    pending = next_confirmation_item(
        runs_dir / str(run_id) / "confirmations.jsonl"
    )
    assert artifact.missing_count == 1
    assert [cue.sequence for cue in transcript.cues] == [1, 2, 3]
    assert transcript.cues[1].speech == "미확정 발화"
    assert transcript.cues[1].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert (
        transcript.workflow_status
        is WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    )
    assert pending is not None
    assert pending.cue_sequence == EXPECTED_INSERTED_SEQUENCE
    assert pending.start_tc_ms == Milliseconds(1_000)


def test_confirmation_answers_one_item_then_resumes_and_finalizes(
    tmp_path: Path,
) -> None:
    """Answer one item per call, resume, regenerate CSV, and finalize."""
    store, run_id, run_dir = make_store(tmp_path)
    append_confirmation_requests(run_dir / "confirmations.jsonl", make_items())
    activate_confirmation(store, run_id)

    first = next_confirmation_item(run_dir / "confirmations.jsonl")
    first_result = apply_next_confirmation_answer(store, run_id, "복원된 문장")
    resumed = next_confirmation_item(run_dir / "confirmations.jsonl")

    assert first == make_items()[0]
    assert first_result.workflow_status is WorkflowStatus.NEEDS_CONFIRMATION
    assert resumed == make_items()[1]
    assert store.load_transcript(run_id).cues[1].speech == "복원된 문장"

    second_result = apply_next_confirmation_answer(store, run_id, "민수")

    transcript = store.load_transcript(run_id)
    with (run_dir / "transcript.csv").open(encoding="utf-8", newline="") as output:
        csv_rows = list(csv.DictReader(output))
    records = [
        CONFIRMATION_RECORD_ADAPTER.validate_json(line)
        for line in (run_dir / "confirmations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert second_result.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert transcript.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert transcript.cues[2].speaker == "민수"
    assert all(cue.uncertainty is Uncertainty.CERTAIN for cue in transcript.cues)
    assert [row["speech"] for row in csv_rows] == [
        cue.speech for cue in transcript.cues
    ]
    assert [record.kind for record in records] == [
        "request",
        "request",
        "answer",
        "applied",
        "answer",
        "applied",
    ]
    assert [record.ordinal for record in records] == list(range(1, 7))


def test_no_speech_answer_removes_cue_and_preserves_pending_item(
    tmp_path: Path,
) -> None:
    """Remove a false-positive VAD cue without breaking later confirmation."""
    store, run_id, run_dir = make_store(tmp_path)
    append_confirmation_requests(run_dir / "confirmations.jsonl", make_items())
    activate_confirmation(store, run_id)

    first_result = apply_next_confirmation_answer(store, run_id, "없음")

    resumed = next_confirmation_item(run_dir / "confirmations.jsonl")
    transcript = store.load_transcript(run_id)
    assert first_result.workflow_status is WorkflowStatus.NEEDS_CONFIRMATION
    assert resumed == make_items()[1]
    assert [cue.sequence for cue in transcript.cues] == [1, 2]
    assert [cue.speech for cue in transcript.cues] == ["앞 문장", "다음 초안"]

    second_result = apply_next_confirmation_answer(store, run_id, "민수")

    transcript = store.load_transcript(run_id)
    assert second_result.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert transcript.cues[1].speaker == "민수"


@pytest.mark.parametrize(
    ("answer", "expected_code"),
    [
        ("", ConfirmationErrorCode.EMPTY_ANSWER),
        ("   ", ConfirmationErrorCode.EMPTY_ANSWER),
    ],
)
def test_empty_confirmation_answer_preserves_draft(
    tmp_path: Path,
    answer: str,
    expected_code: ConfirmationErrorCode,
) -> None:
    """Reject empty answers before mutating the current draft."""
    store, run_id, run_dir = make_store(tmp_path)
    append_confirmation_requests(run_dir / "confirmations.jsonl", make_items())
    activate_confirmation(store, run_id)
    transcript_before = (run_dir / "transcript.json").read_bytes()
    state_before = (run_dir / "state.json").read_bytes()

    with pytest.raises(ConfirmationError) as raised:
        _ = apply_next_confirmation_answer(store, run_id, answer)

    assert raised.value.code is expected_code
    assert (run_dir / "transcript.json").read_bytes() == transcript_before
    assert (run_dir / "state.json").read_bytes() == state_before


def test_duplicate_confirmation_answer_preserves_final_draft(tmp_path: Path) -> None:
    """Reject another answer after the only requested item was applied."""
    store, run_id, run_dir = make_store(tmp_path)
    append_confirmation_requests(run_dir / "confirmations.jsonl", make_items()[:1])
    activate_confirmation(store, run_id)
    _ = apply_next_confirmation_answer(store, run_id, "복원된 문장")
    transcript_before = (run_dir / "transcript.json").read_bytes()
    state_before = (run_dir / "state.json").read_bytes()

    with pytest.raises(ConfirmationError) as raised:
        _ = apply_next_confirmation_answer(store, run_id, "다른 답")

    assert raised.value.code is ConfirmationErrorCode.DUPLICATE_ANSWER
    assert (run_dir / "transcript.json").read_bytes() == transcript_before
    assert (run_dir / "state.json").read_bytes() == state_before


def test_corrupt_confirmation_history_preserves_draft(tmp_path: Path) -> None:
    """Reject an answer referencing an unknown confirmation ID."""
    store, run_id, run_dir = make_store(tmp_path)
    append_confirmation_requests(run_dir / "confirmations.jsonl", make_items())
    activate_confirmation(store, run_id)
    with (run_dir / "confirmations.jsonl").open("a", encoding="utf-8") as output:
        _ = output.write(
            '{"kind":"answer","ordinal":3,"confirmation_id":"unknown",'
        )
        _ = output.write('"answer":"x"}\n')
    transcript_before = (run_dir / "transcript.json").read_bytes()
    state_before = (run_dir / "state.json").read_bytes()

    with pytest.raises(ConfirmationError) as raised:
        _ = apply_next_confirmation_answer(store, run_id, "복원된 문장")

    assert raised.value.code is ConfirmationErrorCode.INVALID_CONFIRMATION_ID
    assert (run_dir / "transcript.json").read_bytes() == transcript_before
    assert (run_dir / "state.json").read_bytes() == state_before
