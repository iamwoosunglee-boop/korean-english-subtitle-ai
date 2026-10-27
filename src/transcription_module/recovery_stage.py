# Copyright 2026 Transcription Module contributors

"""Apply VAD gaps to canonical state as resumable confirmation cues."""

from __future__ import annotations

from transcription_module.confirmation import create_confirmation_session
from transcription_module.confirmation_log import append_confirmation_requests
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.models import (
    Cue,
    FrozenModel,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.recovery import (
    CueCoverageInterval,
    MissingSpeechInterval,
    VadSpeechInterval,
    find_missing_speech,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.vad_evidence import VadDetector, ensure_vad_evidence


class RecoveryRunArtifact(FrozenModel):
    """Persisted VAD evidence and the uncovered intervals it produced."""

    run_id: RunId
    vad_intervals: tuple[VadSpeechInterval, ...]
    missing_intervals: tuple[MissingSpeechInterval, ...]
    missing_count: int


def detect_and_queue_vad_recovery(
    store: RunStore,
    run_id: RunId,
    *,
    detector: VadDetector | None = None,
) -> RecoveryRunArtifact:
    """Reuse the run's exact VAD evidence and queue uncovered speech."""
    evidence = ensure_vad_evidence(
        store,
        run_id,
        detector=detector,
    )
    return queue_vad_recovery(store, run_id, evidence.speech_intervals)


def queue_vad_recovery(
    store: RunStore,
    run_id: RunId,
    vad_intervals: tuple[VadSpeechInterval, ...],
) -> RecoveryRunArtifact:
    """Insert uncovered intervals and queue typed speech confirmations."""
    transcript = store.load_transcript(run_id)
    state = store.load_state(run_id)
    missing = find_missing_speech(
        vad_intervals,
        tuple(
            CueCoverageInterval(
                start_tc_ms=cue.start_tc_ms,
                end_tc_ms=cue.end_tc_ms,
            )
            for cue in transcript.cues
        ),
    )
    artifact = RecoveryRunArtifact(
        run_id=run_id,
        vad_intervals=vad_intervals,
        missing_intervals=missing,
        missing_count=len(missing),
    )
    paths = store.paths(run_id)
    if not missing:
        write_model_atomic(paths.recovery_artifact, artifact)
        return artifact

    ordered: list[Cue | MissingSpeechInterval] = [
        *transcript.cues,
        *missing,
    ]
    ordered.sort(key=lambda item: int(item.start_tc_ms))
    cues: list[Cue] = []
    items: list[ConfirmationItem] = []
    for sequence, item in enumerate(ordered, start=1):
        if isinstance(item, Cue):
            cues.append(_renumber_cue(item, sequence))
            continue
        cues.append(_missing_cue(item, sequence))
        items.append(_confirmation_item(item, sequence))

    workflow_status = WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    updated = Transcript(
        run_id=transcript.run_id,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        duration_ms=transcript.duration_ms,
        cues=tuple(cues),
        vision_evidence=transcript.vision_evidence,
    )
    updated_state = RunState(
        run_id=state.run_id,
        source_url=state.source_url,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        context=state.context,
    )
    store.save_transcript(updated)
    store.save_state(updated_state)
    append_confirmation_requests(paths.confirmations, tuple(items))
    if not paths.confirmation_session.exists():
        _ = create_confirmation_session(store, run_id)
    write_model_atomic(paths.recovery_artifact, artifact)
    return artifact


def _renumber_cue(cue: Cue, sequence: int) -> Cue:
    return Cue(
        sequence=sequence,
        cue_id=cue.cue_id,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        speech=cue.speech,
        speaker=cue.speaker,
        uncertainty=cue.uncertainty,
        screen_text=cue.screen_text,
        vision_evidence_ids=cue.vision_evidence_ids,
    )


def _missing_cue(interval: MissingSpeechInterval, sequence: int) -> Cue:
    return Cue(
        sequence=sequence,
        start_tc_ms=interval.start_tc_ms,
        end_tc_ms=interval.end_tc_ms,
        speech="미확정 발화",
        speaker=None,
        uncertainty=Uncertainty.NEEDS_CONFIRMATION,
        screen_text=None,
        vision_evidence_ids=(),
    )


def _confirmation_item(
    interval: MissingSpeechInterval,
    sequence: int,
) -> ConfirmationItem:
    return ConfirmationItem(
        confirmation_id=ConfirmationId(
            f"vad-{int(interval.start_tc_ms)}-{int(interval.end_tc_ms)}"
        ),
        cue_sequence=sequence,
        field=ConfirmationField.SPEECH,
        start_tc_ms=interval.start_tc_ms,
        end_tc_ms=interval.end_tc_ms,
        current_value=None,
        cue_speech="미확정 발화",
        candidates=(),
        evidence=("ffmpeg-vad uncovered ASR cue coverage",),
    )
