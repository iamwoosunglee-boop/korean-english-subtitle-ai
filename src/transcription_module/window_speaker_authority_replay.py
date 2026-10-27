# Copyright 2026 Transcription Module contributors

"""Replay independently admitted named-speaker authority."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from transcription_module._drafting_models import DiarizationStatus
from transcription_module.artifact_io import read_confined_bytes
from transcription_module.confirmation_log import project_confirmation_history
from transcription_module.confirmation_models import (
    CONFIRMATION_RECORD_ADAPTER,
    ConfirmationAnswerRecord,
    ConfirmationAppliedRecord,
    ConfirmationError,
    ConfirmationField,
    ConfirmationRequestRecord,
)
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    EvidenceState,
    VisualRole,
)
from transcription_module.visual_observations import (
    VisualObservation,
    VisualObservationSet,
)
from transcription_module.window_evidence_errors import fail_window_evidence
from transcription_module.window_evidence_models import (
    VerifiedSpeakerTurnArtifact,
    WindowVerifiedTurn,
)
from transcription_module.window_speaker_authority_models import (
    SpeakerTurnAuthorityRecord,
    SpeakerTurnAuthoritySet,
    SpeakerTurnEvidenceSnapshot,
    SpeakerTurnReplayContext,
    canonical_speaker_turn_authority_bytes,
    speaker_turn_authority_digest,
)
from transcription_module.window_speaker_evidence_validation import (
    validate_speaker_observation,
)


def expected_verified_speaker_turn_artifact(
    context: SpeakerTurnReplayContext,
    evidence: SpeakerTurnEvidenceSnapshot | None = None,
) -> VerifiedSpeakerTurnArtifact | None:
    """Derive exact named turns from one inventory-admitted authority."""
    admissions = context.inventory.evidence_authorities
    if not admissions:
        return None
    captured = evidence if evidence is not None else _read_proof(context)
    authority_sha = hashlib.sha256(captured.authority_payload).hexdigest()
    if admissions[0].sha256 != authority_sha:
        fail_window_evidence("speaker-turn authority differs from inventory admission")
    try:
        authority = SpeakerTurnAuthoritySet.model_validate_json(
            captured.authority_payload
        )
    except ValidationError:
        fail_window_evidence("speaker-turn authority is malformed")
    if captured.authority_payload != canonical_speaker_turn_authority_bytes(authority):
        fail_window_evidence("speaker-turn authority bytes are noncanonical")
    if (
        authority.run_id != context.run_id
        or authority.policy_digest != context.policy_digest
        or authority.source_digest != context.source_digest
    ):
        fail_window_evidence("speaker-turn authority has foreign source identity")
    _validate_records(context, authority.records)
    _validate_proof(context, authority, captured)
    return VerifiedSpeakerTurnArtifact(
        run_id=context.run_id,
        policy_digest=context.policy_digest,
        source_digest=context.source_digest,
        authority_path="speaker-turn-authority.json",
        authority_sha256=authority_sha,
        turns=tuple(
            WindowVerifiedTurn(
                turn_id=record.turn_id,
                start_tc_ms=record.start_tc_ms,
                end_tc_ms=record.end_tc_ms,
                cluster=record.cluster,
                speaker=record.speaker,
                authority_digest=speaker_turn_authority_digest(
                    context.run_id,
                    context.source_digest,
                    record,
                ),
                verification_evidence_ids=record.verification_evidence_ids,
                verification_decision_ids=record.verification_decision_ids,
            )
            for record in authority.records
        ),
    )


def _read_proof(context: SpeakerTurnReplayContext) -> SpeakerTurnEvidenceSnapshot:
    try:
        authority = read_confined_bytes(
            context.run_dir / "speaker-turn-authority.json"
        )
        observations = read_confined_bytes(context.run_dir / "visual-observations.json")
        decisions = read_confined_bytes(
            context.run_dir / "speaker-turn-decisions.jsonl"
        )
    except OSError:
        fail_window_evidence("admitted speaker-turn authority path is unsafe")
    if authority is None or observations is None or decisions is None:
        fail_window_evidence("admitted speaker-turn authority path is missing")
    return SpeakerTurnEvidenceSnapshot(authority, observations, decisions)


def _validate_records(
    context: SpeakerTurnReplayContext,
    records: tuple[SpeakerTurnAuthorityRecord, ...],
) -> None:
    diarization = context.diarization
    if diarization is None or diarization.status is not DiarizationStatus.AVAILABLE:
        fail_window_evidence("speaker-turn authority lacks current diarization")
    raw_turns = {
        f"turn:{ordinal}": segment
        for ordinal, segment in enumerate(diarization.segments, start=1)
    }
    for record in records:
        raw = raw_turns.get(record.turn_id)
        if (
            record.end_tc_ms > context.duration_ms
            or raw is None
            or (raw.start_tc_ms, raw.end_tc_ms, raw.cluster)
            != (record.start_tc_ms, record.end_tc_ms, record.cluster)
        ):
            fail_window_evidence(
                "speaker-turn authority differs from raw turn identity"
            )


def _validate_proof(
    context: SpeakerTurnReplayContext,
    authority: SpeakerTurnAuthoritySet,
    evidence: SpeakerTurnEvidenceSnapshot,
) -> None:
    if (
        hashlib.sha256(evidence.observation_payload).hexdigest()
        != authority.observation_sha256
        or hashlib.sha256(evidence.decision_payload).hexdigest()
        != authority.decision_sha256
    ):
        fail_window_evidence("speaker-turn verification evidence bytes are stale")
    try:
        observations = VisualObservationSet.model_validate_json(
            evidence.observation_payload
        )
        decision_records = tuple(
            CONFIRMATION_RECORD_ADAPTER.validate_json(line)
            for line in evidence.decision_payload.decode().splitlines(keepends=True)
        )
        history = project_confirmation_history(decision_records)
    except (UnicodeDecodeError, ValidationError, ConfirmationError):
        fail_window_evidence("speaker-turn verification records are malformed")
    if (
        observations.run_id != context.run_id
        or observations.era is not EvidenceEra.POLICY_BOUND
        or observations.policy_digest != context.policy_digest
    ):
        fail_window_evidence("speaker-turn observations have foreign authority")
    observed = {item.observation_id: item for item in observations.observations}
    evidence_ids = {
        value
        for record in authority.records
        for value in record.verification_evidence_ids
    }
    decision_ids = {
        value
        for record in authority.records
        for value in record.verification_decision_ids
    }
    if set(observed) != evidence_ids or {
        str(item.confirmation_id) for item in history.requests
    } != decision_ids:
        fail_window_evidence("speaker-turn authority does not exhaust admitted records")
    requests = {
        str(item.item.confirmation_id): item
        for item in decision_records
        if isinstance(item, ConfirmationRequestRecord)
    }
    answers = {
        str(item.confirmation_id): item
        for item in decision_records
        if isinstance(item, ConfirmationAnswerRecord)
    }
    applied = {
        str(item.confirmation_id): item
        for item in decision_records
        if isinstance(item, ConfirmationAppliedRecord)
    }
    if (
        len(decision_records) != len(decision_ids) * 3
        or set(requests) != decision_ids
        or set(answers) != decision_ids
        or set(applied) != decision_ids
    ):
        fail_window_evidence("speaker-turn decisions lack exact proven closure")
    decisions = (requests, answers, applied)
    for record in authority.records:
        _validate_record(context, record, observed, decisions)


type _DecisionMaps = tuple[
    dict[str, ConfirmationRequestRecord],
    dict[str, ConfirmationAnswerRecord],
    dict[str, ConfirmationAppliedRecord],
]


def _validate_record(
    context: SpeakerTurnReplayContext,
    record: SpeakerTurnAuthorityRecord,
    observations: dict[str, VisualObservation],
    decisions: _DecisionMaps,
) -> None:
    requests, answers, applied = decisions
    for evidence_id in record.verification_evidence_ids:
        observation = observations[evidence_id]
        if (
            observation.role is not VisualRole.SPEECH_SUPPORTING
            or observation.state is not EvidenceState.SUPPORTED
            or observation.start_tc_ms != record.start_tc_ms
            or observation.end_tc_ms != record.end_tc_ms
            or observation.turn_ids != (record.turn_id,)
        ):
            fail_window_evidence("speaker-turn verification evidence is inexact")
        validate_speaker_observation(context, record, observation)
    for decision_id in record.verification_decision_ids:
        item = requests[decision_id].item
        if (
            item.field is not ConfirmationField.SPEAKER
            or int(item.start_tc_ms) != record.start_tc_ms
            or int(item.end_tc_ms) != record.end_tc_ms
            or item.candidates != (record.speaker,)
            or item.evidence != record.verification_evidence_ids
            or answers[decision_id].answer != record.speaker
            or applied[decision_id].applied_value != record.speaker
        ):
            fail_window_evidence("speaker-turn decision replay differs from authority")


__all__ = ["expected_verified_speaker_turn_artifact"]
