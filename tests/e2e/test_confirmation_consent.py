# Copyright 2026 Transcription Module contributors

"""Confirmation requires explicit consent and separates suggestions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from transcription_module.confirmation import (
    apply_confirmation_answer,
    create_confirmation_session,
    enqueue_review_confirmation_conflicts,
    get_next_confirmation,
    record_confirmation_suggestion,
    set_confirmation_consent,
)
from transcription_module.confirmation_log import (
    append_confirmation_requests,
    load_confirmation_history,
)
from transcription_module.confirmation_models import (
    ConfirmationError,
    ConfirmationErrorCode,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationQueueStatus,
    ConfirmationSessionStatus,
)
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.models import (
    Cue,
    CueId,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.speaker_inference import (
    SpeakerInferenceArtifact,
    SpeakerInferenceDecision,
)
from transcription_module.storage import RunStore, write_model_atomic

PROJECT_ROOT = Path(__file__).parents[2]
FIRST_CONFIRMATION_NUMBER = 1
SECOND_CONFIRMATION_NUMBER = 2
CONFIRMATION_TOTAL = 3


def _confirmation_run(
    tmp_path: Path,
    candidates: tuple[str, ...] = ("사용자 답변",),
    *,
    field: ConfirmationField = ConfirmationField.SPEECH,
    speaker_artifact: bool = True,
) -> tuple[RunStore, RunId]:
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000013")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=confirmation-consent"
            ),
            workflow_status=WorkflowStatus.WAITING_CONFIRMATION_CONSENT,
        )
    )
    cue_id = CueId("10000000-0000-0000-0000-000000000013")
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.WAITING_CONFIRMATION_CONSENT,
            validation_status=ValidationStatus.PENDING,
            duration_ms=Milliseconds(2_000),
            cues=(
                Cue(
                    sequence=1,
                    cue_id=cue_id,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="판단 전 문장",
                    speaker=(
                        "판단 전 화자"
                        if field is ConfirmationField.SPEAKER
                        else None
                    ),
                    uncertainty=Uncertainty.NEEDS_CONFIRMATION,
                ),
            ),
            vision_evidence=(),
        )
    )
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={
                "workflow_status": WorkflowStatus.WAITING_CONFIRMATION_CONSENT
            }
        )
    )
    append_confirmation_requests(
        store.paths(run_id).confirmations,
        (
            ConfirmationItem(
                confirmation_id=ConfirmationId("question-1"),
                cue_id=cue_id,
                cue_sequence=1,
                field=field,
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(1_000),
                current_value=(
                    "판단 전 화자"
                    if field is ConfirmationField.SPEAKER
                    else "판단 전 문장"
                ),
                cue_speech="판단 전 문장",
                candidates=candidates,
                evidence=("두 후보가 충돌함",),
            ),
        ),
    )
    if speaker_artifact:
        write_model_atomic(
            store.paths(run_id).speaker_inference_artifact,
            SpeakerInferenceArtifact(
                run_id=run_id,
                total_count=1,
                resolved_count=1,
                unresolved_count=0,
                decisions=(
                    SpeakerInferenceDecision(
                        cue_id=cue_id,
                        speaker="고정 화자",
                        reason="fixture 결정",
                    ),
                ),
            ),
        )
    return store, run_id


def _out_of_order_items() -> tuple[ConfirmationItem, ConfirmationItem]:
    return (
        ConfirmationItem(
            confirmation_id=ConfirmationId("question-later"),
            cue_sequence=3,
            field=ConfirmationField.SPEECH,
            start_tc_ms=Milliseconds(1_500),
            end_tc_ms=Milliseconds(1_900),
            current_value="뒤 질문",
            cue_speech="뒤 질문",
            candidates=("뒤 답변",),
            evidence=("fixture",),
        ),
        ConfirmationItem(
            confirmation_id=ConfirmationId("question-earlier"),
            cue_sequence=2,
            field=ConfirmationField.SPEECH,
            start_tc_ms=Milliseconds(1_000),
            end_tc_ms=Milliseconds(1_500),
            current_value="앞 질문",
            cue_speech="앞 질문",
            candidates=("앞 답변",),
            evidence=("fixture",),
        ),
    )


def test_confirmation_items_follow_video_time_not_append_order(
    tmp_path: Path,
) -> None:
    """A later append cannot move a later video interval ahead."""
    store, run_id = _confirmation_run(tmp_path)
    append_confirmation_requests(
        store.paths(run_id).confirmations,
        _out_of_order_items(),
    )
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    first = get_next_confirmation(store, run_id)

    _ = apply_confirmation_answer(
        store,
        run_id,
        first.confirmation_id,
        "사용자 답변",
    )

    assert get_next_confirmation(store, run_id).confirmation_id == (
        ConfirmationId("question-earlier")
    )


def test_next_confirmation_reports_current_total_and_remaining_counts(
    tmp_path: Path,
) -> None:
    """The CLI reports stable progress counts for the active item."""
    store, run_id = _confirmation_run(tmp_path)
    append_confirmation_requests(
        store.paths(run_id).confirmations,
        _out_of_order_items(),
    )
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    command = [
        "uv",
        "run",
        "transcription-cli",
        "next-confirmation",
        str(run_id),
        "--runs-dir",
        str(tmp_path / "runs"),
    ]

    first_result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    first_status = ConfirmationQueueStatus.model_validate_json(
        first_result.stdout
    )
    first_item = get_next_confirmation(store, run_id)
    _ = apply_confirmation_answer(
        store,
        run_id,
        first_item.confirmation_id,
        "사용자 답변",
    )
    second_result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    second_status = ConfirmationQueueStatus.model_validate_json(
        second_result.stdout
    )

    assert first_result.returncode == 0, first_result.stderr
    assert first_status.current_number == FIRST_CONFIRMATION_NUMBER
    assert first_status.total_count == CONFIRMATION_TOTAL
    assert first_status.remaining_count == CONFIRMATION_TOTAL
    assert second_result.returncode == 0, second_result.stderr
    assert second_status.current_number == SECOND_CONFIRMATION_NUMBER
    assert second_status.total_count == CONFIRMATION_TOTAL
    assert second_status.remaining_count == SECOND_CONFIRMATION_NUMBER


def test_start_confirmation_requires_prior_speaker_inference(
    tmp_path: Path,
) -> None:
    """The confirmation entry point refuses runs without speaker inference."""
    store, run_id = _confirmation_run(tmp_path, speaker_artifact=False)
    runs_dir = tmp_path / "runs"
    command = [
        "uv",
        "run",
        "transcription-cli",
        "start-confirmation",
        str(run_id),
        "--runs-dir",
        str(runs_dir),
    ]

    blocked = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert blocked.returncode != 0
    stderr_json = blocked.stderr[blocked.stderr.index("{"):]
    error = ErrorEnvelope.model_validate_json(stderr_json)
    assert error.error.code is ErrorCode.SPEAKER_INFERENCE_REQUIRED
    assert not store.paths(run_id).confirmation_session.exists()

    write_model_atomic(
        store.paths(run_id).speaker_inference_artifact,
        SpeakerInferenceArtifact(
            run_id=run_id,
            total_count=1,
            resolved_count=1,
            unresolved_count=0,
            decisions=(
                SpeakerInferenceDecision(
                    cue_id=CueId("10000000-0000-0000-0000-000000000013"),
                    speaker="고정 화자",
                    reason="fixture 결정",
                ),
            ),
        ),
    )

    allowed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert allowed.returncode == 0, allowed.stderr
    assert store.paths(run_id).confirmation_session.exists()


def test_legacy_abbreviated_candidates_are_not_exposed(
    tmp_path: Path,
) -> None:
    """Old audit records stay intact while incomplete candidates stay hidden."""
    candidates = ("완전하지 않은 후보...", "완전한 후보")
    store, run_id = _confirmation_run(tmp_path, candidates)
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)

    item = get_next_confirmation(store, run_id)

    assert item.candidates == ("완전한 후보",)
    history = load_confirmation_history(store.paths(run_id).confirmations)
    assert history.requests[0].candidates == candidates


def test_consent_precedes_items_and_suggestion_does_not_answer(
    tmp_path: Path,
) -> None:
    """Consent gates item access while suggestions remain non-binding."""
    store, run_id = _confirmation_run(tmp_path)
    _ = create_confirmation_session(store, run_id)
    transcript_before = store.paths(run_id).transcript.read_bytes()

    with pytest.raises(ConfirmationError) as next_error:
        _ = get_next_confirmation(store, run_id)
    with pytest.raises(ConfirmationError) as answer_error:
        _ = apply_confirmation_answer(
            store,
            run_id,
            ConfirmationId("question-1"),
            "사용자 답변",
        )

    assert next_error.value.code is ConfirmationErrorCode.CONSENT_REQUIRED
    assert answer_error.value.code is ConfirmationErrorCode.CONSENT_REQUIRED
    assert store.paths(run_id).transcript.read_bytes() == transcript_before

    active = set_confirmation_consent(store, run_id, accepted=True)
    item = get_next_confirmation(store, run_id)
    active_transcript = store.paths(run_id).transcript.read_bytes()
    record_confirmation_suggestion(
        store,
        run_id,
        ConfirmationId("question-1"),
        "에이전트 제안",
    )

    assert active.status is ConfirmationSessionStatus.ACTIVE
    assert item.confirmation_id == ConfirmationId("question-1")
    assert item.cue_speech == "판단 전 문장"
    assert get_next_confirmation(store, run_id) == item
    assert store.paths(run_id).transcript.read_bytes() == active_transcript
    history = load_confirmation_history(store.paths(run_id).confirmations)
    assert history.answered_ids == frozenset()
    assert history.suggestions[-1].suggestion == "에이전트 제안"

    result = apply_confirmation_answer(
        store,
        run_id,
        ConfirmationId("question-1"),
        "사용자 답변",
    )

    transcript = store.load_transcript(run_id)
    assert result.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert transcript.cues[0].speech == "사용자 답변"
    assert transcript.cues[0].cue_id == CueId(
        "10000000-0000-0000-0000-000000000013"
    )
    assert transcript.validation_status is ValidationStatus.PENDING


def test_decline_preserves_transcript_and_new_attempt_gets_new_session(
    tmp_path: Path,
) -> None:
    """Decline ends one attempt without consuming later confirmation."""
    store, run_id = _confirmation_run(tmp_path)
    first = create_confirmation_session(store, run_id)
    transcript_before = store.paths(run_id).transcript.read_bytes()

    declined = set_confirmation_consent(store, run_id, accepted=False)

    assert declined.status is ConfirmationSessionStatus.DECLINED
    assert store.load_transcript(run_id).workflow_status is (
        WorkflowStatus.CONFIRMATION_DECLINED
    )
    assert store.load_transcript(run_id).cues[0].speech == "판단 전 문장"
    with pytest.raises(ConfirmationError) as ended:
        _ = get_next_confirmation(store, run_id)
    assert ended.value.code is ConfirmationErrorCode.SESSION_ENDED

    second = create_confirmation_session(store, run_id)

    assert second.session_id != first.session_id
    assert second.status is ConfirmationSessionStatus.AWAITING_CONSENT
    assert store.load_transcript(run_id).cues[0].speech == "판단 전 문장"
    assert store.paths(run_id).transcript.read_bytes() == transcript_before


@pytest.mark.parametrize(
    ("field", "user_answer", "reviewed_value"),
    [
        (ConfirmationField.SPEECH, "사용자 확정 문장", "검토 제안 문장"),
        (ConfirmationField.SPEAKER, "사용자 확정 화자", "검토 제안 화자"),
    ],
)
def test_review_conflict_requires_fresh_consent_attempt(
    tmp_path: Path,
    field: ConfirmationField,
    user_answer: str,
    reviewed_value: str,
) -> None:
    """A review cannot reuse consent granted for an applied field answer."""
    store, run_id = _confirmation_run(tmp_path, field=field)
    first_session = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    first_item = get_next_confirmation(store, run_id)
    _ = apply_confirmation_answer(
        store,
        run_id,
        first_item.confirmation_id,
        user_answer,
    )

    queued = enqueue_review_confirmation_conflicts(
        store,
        run_id,
        cue_id=CueId("10000000-0000-0000-0000-000000000013"),
        proposed_values={field: reviewed_value},
        evidence=("silent final review found contradictory evidence",),
    )

    assert len(queued) == 1
    assert queued[0].field is field
    assert queued[0].current_value == user_answer
    assert queued[0].candidates == (reviewed_value,)
    transcript = store.load_transcript(run_id)
    assert transcript.cues[0].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert transcript.workflow_status is WorkflowStatus.NEEDS_CONFIRMATION
    with pytest.raises(ConfirmationError) as ended:
        _ = get_next_confirmation(store, run_id)
    assert ended.value.code is ConfirmationErrorCode.SESSION_ENDED

    second_session = create_confirmation_session(store, run_id)
    assert second_session.session_id != first_session.session_id
    with pytest.raises(ConfirmationError) as gated:
        _ = get_next_confirmation(store, run_id)
    assert gated.value.code is ConfirmationErrorCode.CONSENT_REQUIRED
    _ = set_confirmation_consent(store, run_id, accepted=True)
    review_item = get_next_confirmation(store, run_id)
    result = apply_confirmation_answer(
        store,
        run_id,
        review_item.confirmation_id,
        user_answer,
    )

    resolved = store.load_transcript(run_id)
    assert result.workflow_status is WorkflowStatus.READY_FOR_VALIDATION
    assert getattr(resolved.cues[0], field.value) == user_answer
    assert resolved.cues[0].uncertainty is Uncertainty.CERTAIN


def test_review_conflict_deduplicates_only_unanswered_field_requests(
    tmp_path: Path,
) -> None:
    """An applied request permits a new conflict, but its duplicate does not."""
    store, run_id = _confirmation_run(tmp_path)
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    original = get_next_confirmation(store, run_id)
    _ = apply_confirmation_answer(
        store,
        run_id,
        original.confirmation_id,
        "사용자 확정 문장",
    )
    cue_id = CueId("10000000-0000-0000-0000-000000000013")
    proposed_values = {ConfirmationField.SPEECH: "검토 제안 문장"}
    evidence = ("review evidence",)

    first = enqueue_review_confirmation_conflicts(
        store,
        run_id,
        cue_id=cue_id,
        proposed_values=proposed_values,
        evidence=evidence,
    )
    transcript_after_first = store.paths(run_id).transcript.read_bytes()
    second = enqueue_review_confirmation_conflicts(
        store,
        run_id,
        cue_id=cue_id,
        proposed_values=proposed_values,
        evidence=evidence,
    )

    history = load_confirmation_history(store.paths(run_id).confirmations)
    unanswered = tuple(
        item
        for item in history.requests
        if item.confirmation_id not in history.applied_ids
    )
    assert len(first) == 1
    assert second == ()
    assert len(unanswered) == 1
    assert store.paths(run_id).transcript.read_bytes() == transcript_after_first


def test_review_conflict_uses_applied_field_authority_and_ignores_matches(
    tmp_path: Path,
) -> None:
    """Only an applied field contradiction enters the confirmation lane."""
    store, run_id = _confirmation_run(tmp_path)
    _ = create_confirmation_session(store, run_id)
    _ = set_confirmation_consent(store, run_id, accepted=True)
    original = get_next_confirmation(store, run_id)
    _ = apply_confirmation_answer(
        store,
        run_id,
        original.confirmation_id,
        "사용자 확정 문장",
    )
    transcript_before = store.paths(run_id).transcript.read_bytes()
    history_before = store.paths(run_id).confirmations.read_bytes()

    queued = enqueue_review_confirmation_conflicts(
        store,
        run_id,
        cue_id=CueId("10000000-0000-0000-0000-000000000013"),
        proposed_values={
            ConfirmationField.SPEECH: "사용자 확정 문장",
            ConfirmationField.SPEAKER: "확정된 적 없는 화자",
        },
        evidence=("review evidence",),
    )

    assert queued == ()
    assert store.paths(run_id).transcript.read_bytes() == transcript_before
    assert store.paths(run_id).confirmations.read_bytes() == history_before
