# Copyright 2026 Transcription Module contributors

"""Apply evidence-based speaker decisions without inventing identities."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import Field, model_validator

from transcription_module.confirmation import create_confirmation_session
from transcription_module.confirmation_log import (
    append_confirmation_requests,
    load_confirmation_history,
)
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationSession,
    ConfirmationSessionStatus,
)
from transcription_module.models import (
    Cue,
    CueId,
    FrozenModel,
    RunId,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.storage import RunStore, write_model_atomic

if TYPE_CHECKING:
    from pathlib import Path


class SpeakerInferenceDecision(FrozenModel):
    """One cue's resolved speaker or its explicit ambiguity."""

    cue_id: CueId
    speaker: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1)
    candidates: tuple[str, ...] = ()

    @model_validator(mode="after")
    def resolved_decision_has_no_candidates(self) -> SpeakerInferenceDecision:
        """Keep candidates only for decisions that remain unresolved."""
        if self.speaker is not None and self.candidates:
            message = "resolved speaker decisions cannot have candidates"
            raise ValueError(message)
        return self


class SpeakerInferenceArtifact(FrozenModel):
    """Persisted whole-transcript speaker inference result."""

    run_id: RunId
    total_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    decisions: tuple[SpeakerInferenceDecision, ...]


def apply_speaker_inference(
    store: RunStore,
    run_id: RunId,
    decisions: tuple[SpeakerInferenceDecision, ...],
) -> SpeakerInferenceArtifact:
    """Apply resolved names and append only ambiguous speaker questions."""
    transcript = store.load_transcript(run_id)
    decision_by_id = {decision.cue_id: decision for decision in decisions}
    identified_cues: list[tuple[Cue, CueId]] = []
    for cue in transcript.cues:
        if cue.cue_id is None:
            message = "speaker decisions require unique permanent cue IDs"
            raise ValueError(message)
        identified_cues.append((cue, cue.cue_id))
    if len(decision_by_id) != len(decisions):
        message = "speaker decisions require unique permanent cue IDs"
        raise ValueError(message)
    cue_ids = {cue_id for _, cue_id in identified_cues}
    if set(decision_by_id) != cue_ids:
        message = "speaker decisions must cover every transcript cue"
        raise ValueError(message)

    history = load_confirmation_history(store.paths(run_id).confirmations)
    requested_fields = {(item.cue_id, item.field) for item in history.requests}
    requests = tuple(
        ConfirmationItem(
            confirmation_id=ConfirmationId(uuid4().hex),
            cue_id=cue_id,
            cue_sequence=cue.sequence,
            field=ConfirmationField.SPEAKER,
            start_tc_ms=cue.start_tc_ms,
            end_tc_ms=cue.end_tc_ms,
            current_value=cue.speaker,
            cue_speech=cue.speech,
            candidates=decision_by_id[cue_id].candidates,
            evidence=(decision_by_id[cue_id].reason,),
        )
        for cue, cue_id in identified_cues
        if (
            decision_by_id[cue_id].speaker is None
            and (cue_id, ConfirmationField.SPEAKER) not in requested_fields
        )
    )
    paths = store.paths(run_id)
    starts_session = bool(requests) and not _session_is_open(paths.confirmation_session)
    workflow_status = (
        WorkflowStatus.WAITING_CONFIRMATION_CONSENT
        if starts_session
        else transcript.workflow_status
    )
    updated = transcript.model_copy(
        update={
            "workflow_status": workflow_status,
            "cues": tuple(
                cue.model_copy(
                    update={
                        "speaker": decision_by_id[cue_id].speaker,
                        "uncertainty": (
                            Uncertainty.NEEDS_CONFIRMATION
                            if decision_by_id[cue_id].speaker is None
                            else cue.uncertainty
                        ),
                    }
                )
                for cue, cue_id in identified_cues
            ),
        }
    )
    artifact = SpeakerInferenceArtifact(
        run_id=run_id,
        total_count=len(decisions),
        resolved_count=sum(decision.speaker is not None for decision in decisions),
        unresolved_count=sum(decision.speaker is None for decision in decisions),
        decisions=decisions,
    )
    current_state = store.load_state(run_id)
    state = current_state.model_copy(
        update={
            "workflow_status": workflow_status,
            "validation_status": ValidationStatus.PENDING,
        }
        if starts_session
        else {}
    )
    store.save_transcript(updated)
    store.save_state(state)
    append_confirmation_requests(paths.confirmations, requests)
    write_model_atomic(paths.speaker_inference_artifact, artifact)
    if starts_session:
        _ = create_confirmation_session(store, run_id)
    return artifact


def _session_is_open(path: Path) -> bool:
    if not path.exists():
        return False
    session = ConfirmationSession.model_validate_json(path.read_text(encoding="utf-8"))
    return session.status in {
        ConfirmationSessionStatus.AWAITING_CONSENT,
        ConfirmationSessionStatus.ACTIVE,
    }
