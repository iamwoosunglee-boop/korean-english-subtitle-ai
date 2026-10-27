# Copyright 2026 Transcription Module contributors

"""Speaker inference fills known speakers and queues only ambiguous ones."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.task12_assembly_support import (
    assemble_without_agent,
    persist_policy_windows,
    policy_assembly_fixture,
)
from tests.integration.window_keep_claim_support import (
    speakerless_keep_draft,
)
from transcription_module.confirmation_log import (
    append_confirmation_requests,
    load_confirmation_history,
)
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.models import (
    Cue,
    CueId,
    EpisodeContext,
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
    apply_speaker_inference,
)
from transcription_module.storage import RunStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("resolved", [True, False])
def test_policy_assembly_defers_speaker_request_until_inference(
    tmp_path: Path,
    *,
    resolved: bool,
) -> None:
    """Assembly queues no speaker request before mandatory inference."""
    fixture = policy_assembly_fixture(tmp_path)
    _ = persist_policy_windows(
        fixture,
        (speakerless_keep_draft(fixture.window_input),),
    )

    _ = assemble_without_agent(fixture, [])

    before = load_confirmation_history(
        fixture.store.paths(fixture.run_id).confirmations
    )
    assert all(item.field is not ConfirmationField.SPEAKER for item in before.requests)
    cue_id = fixture.store.load_transcript(fixture.run_id).cues[0].cue_id
    assert cue_id is not None
    decision = (
        SpeakerInferenceDecision(
            cue_id=cue_id,
            speaker="화자A",
            reason="독립 화자 근거가 일치함",
        )
        if resolved
        else SpeakerInferenceDecision(
            cue_id=cue_id,
            reason="두 화자의 근거가 충돌함",
            candidates=("화자A", "화자B"),
        )
    )

    _ = apply_speaker_inference(
        fixture.store,
        fixture.run_id,
        (decision,),
    )

    after = load_confirmation_history(fixture.store.paths(fixture.run_id).confirmations)
    speaker_requests = tuple(
        item for item in after.requests if item.field is ConfirmationField.SPEAKER
    )
    assert len(speaker_requests) == (0 if resolved else 1)


def test_speaker_inference_applies_known_and_queues_ambiguous(
    tmp_path: Path,
) -> None:
    """Resolved names persist while ambiguous names follow existing requests."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("00000000-0000-0000-0000-000000000016")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=speaker-inference"
            ),
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            validation_status=ValidationStatus.PENDING,
            context=EpisodeContext(raw_text="등장인물 정보"),
        )
    )
    first_id = CueId("10000000-0000-0000-0000-000000000016")
    second_id = CueId("20000000-0000-0000-0000-000000000016")
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.NEEDS_CONFIRMATION,
            validation_status=ValidationStatus.PENDING,
            duration_ms=Milliseconds(2_000),
            cues=(
                Cue(
                    sequence=1,
                    cue_id=first_id,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="첫 발화",
                    uncertainty=Uncertainty.NEEDS_CONFIRMATION,
                ),
                Cue(
                    sequence=2,
                    cue_id=second_id,
                    start_tc_ms=Milliseconds(1_000),
                    end_tc_ms=Milliseconds(2_000),
                    speech="둘째 발화",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
            vision_evidence=(),
        )
    )
    append_confirmation_requests(
        store.paths(run_id).confirmations,
        (
            ConfirmationItem(
                confirmation_id=ConfirmationId("existing-speech"),
                cue_id=first_id,
                cue_sequence=1,
                field=ConfirmationField.SPEECH,
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(1_000),
                current_value="첫 발화",
                candidates=(),
                evidence=("기존 발화 확인",),
            ),
            ConfirmationItem(
                confirmation_id=ConfirmationId("existing-speaker"),
                cue_id=second_id,
                cue_sequence=2,
                field=ConfirmationField.SPEAKER,
                start_tc_ms=Milliseconds(1_000),
                end_tc_ms=Milliseconds(2_000),
                current_value=None,
                candidates=("박도현", "한지아"),
                evidence=("기존 화자 확인",),
            ),
        ),
    )

    artifact = apply_speaker_inference(
        store,
        run_id,
        (
            SpeakerInferenceDecision(
                cue_id=first_id,
                speaker="오태우",
                reason="화면과 입모양이 일치함",
            ),
            SpeakerInferenceDecision(
                cue_id=second_id,
                reason="화면 밖에서 두 사람이 겹쳐 말함",
                candidates=("박도현", "한지아"),
            ),
        ),
    )

    transcript = store.load_transcript(run_id)
    history = load_confirmation_history(store.paths(run_id).confirmations)
    persisted = SpeakerInferenceArtifact.model_validate_json(
        store.paths(run_id).speaker_inference_artifact.read_text(encoding="utf-8")
    )
    assert transcript.cues[0].speaker == "오태우"
    assert transcript.cues[0].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert transcript.cues[1].speaker is None
    assert transcript.cues[1].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert [item.field for item in history.requests] == [
        ConfirmationField.SPEECH,
        ConfirmationField.SPEAKER,
    ]
    assert [item.confirmation_id for item in history.requests] == [
        ConfirmationId("existing-speech"),
        ConfirmationId("existing-speaker"),
    ]
    assert artifact.resolved_count == 1
    assert artifact.unresolved_count == 1
    assert persisted == artifact
