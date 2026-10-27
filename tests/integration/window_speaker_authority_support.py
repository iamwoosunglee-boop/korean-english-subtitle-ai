# Copyright 2026 Transcription Module contributors

"""Current-model fixtures for independently admitted speaker-turn authority."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from transcription_module._drafting_models import DiarizationRunArtifact
from transcription_module.artifact_io import durable_replace_bytes
from transcription_module.asr import AsrRunArtifact
from transcription_module.confirmation_models import (
    ConfirmationAnswerRecord,
    ConfirmationAppliedRecord,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationRequestRecord,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import FrameId, Milliseconds, WorkflowStatus
from transcription_module.storage import write_model_atomic
from transcription_module.subtitle_inventory import EvidenceAuthorityIdentity
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
)
from transcription_module.visual_observations import (
    VisualObservation,
    VisualObservationSet,
)
from transcription_module.window_evidence_inputs import (
    WindowSourceAuthority,
    window_authority_source_digest,
)
from transcription_module.window_speaker_authority import (
    build_verified_speaker_turn_artifact,
    canonical_speaker_turn_authority_bytes,
)
from transcription_module.window_speaker_authority_models import (
    SpeakerTurnAuthorityRecord,
    SpeakerTurnAuthoritySet,
)

if TYPE_CHECKING:
    from transcription_module.models import RunId
    from transcription_module.storage import RunStore


def persist_admitted_speaker_turn_authority(store: RunStore, run_id: RunId) -> None:
    """Persist exact current evidence, admission, and its derived projection."""
    paths = store.paths(run_id)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    inventory = media.subtitle_inventory
    if inventory is None or inventory.policy_digest is None:
        message = "speaker authority fixture requires policy inventory"
        raise AssertionError(message)
    asr = AsrRunArtifact.model_validate_json(paths.asr_transcript.read_bytes())
    vad = VadEvidenceArtifact.model_validate_json(paths.vad_artifact.read_bytes())
    diarization = DiarizationRunArtifact.model_validate_json(
        paths.diarization_artifact.read_bytes()
    )
    source_digest = window_authority_source_digest(
        WindowSourceAuthority(media, asr, vad, diarization, None)
    )
    records = _authority_records()
    write_model_atomic(
        paths.visual_observations_artifact,
        _observations(run_id, inventory.policy_digest),
    )
    decision_payload = "".join(
        f"{item.model_dump_json()}\n" for item in _decision_records(records)
    ).encode()
    durable_replace_bytes(paths.speaker_turn_decisions, decision_payload)
    observation_payload = paths.visual_observations_artifact.read_bytes()
    authority = SpeakerTurnAuthoritySet(
        run_id=run_id,
        policy_digest=inventory.policy_digest,
        source_digest=source_digest,
        observation_sha256=hashlib.sha256(observation_payload).hexdigest(),
        decision_sha256=hashlib.sha256(decision_payload).hexdigest(),
        records=records,
    )
    authority_payload = canonical_speaker_turn_authority_bytes(authority)
    durable_replace_bytes(paths.speaker_turn_authority_artifact, authority_payload)
    admission = EvidenceAuthorityIdentity(
        kind="speaker_turn_authority",
        path="speaker-turn-authority.json",
        sha256=hashlib.sha256(authority_payload).hexdigest(),
    )
    admitted = inventory.model_copy(update={"evidence_authorities": (admission,)})
    write_model_atomic(
        paths.media_manifest,
        media.model_copy(update={"subtitle_inventory": admitted}),
    )
    _ = build_verified_speaker_turn_artifact(store, run_id)


def _authority_records() -> tuple[SpeakerTurnAuthorityRecord, ...]:
    specs = (
        (1, 1_000, 1_800, "SPEAKER_00", "화자A"),
        (2, 3_000, 3_800, "SPEAKER_00", "화자A"),
        (3, 3_000, 3_800, "SPEAKER_01", "화자B"),
    )
    return tuple(
        SpeakerTurnAuthorityRecord(
            turn_id=f"turn:{number}",
            start_tc_ms=start,
            end_tc_ms=end,
            cluster=cluster,
            speaker=speaker,
            verification_evidence_ids=(f"speaker-observation:{number}",),
            verification_decision_ids=(f"speaker-confirmation:{number}",),
        )
        for number, start, end, cluster, speaker in specs
    )


def _observations(run_id: RunId, policy_sha: str) -> VisualObservationSet:
    specs = (
        (1, 1_000, 1_800, "frame-1", 1_200, "vad:1"),
        (2, 3_000, 3_800, "frame-2", 3_200, "vad:2"),
        (3, 3_000, 3_800, "frame-3", 3_400, "vad:2"),
    )
    return VisualObservationSet(
        run_id=run_id,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_sha,
        observations=tuple(
            VisualObservation(
                observation_id=f"speaker-observation:{number}",
                era=EvidenceEra.POLICY_BOUND,
                policy_digest=policy_sha,
                role=VisualRole.SPEECH_SUPPORTING,
                state=EvidenceState.SUPPORTED,
                semantic_relation=SemanticRelation.EXACT,
                frame_ids=(FrameId(frame_id),),
                frame_pts_ms=(pts_ms,),
                start_tc_ms=start,
                end_tc_ms=end,
                complete_text="speaker verification",
                vad_interval_ids=(vad_id,),
                turn_ids=(f"turn:{number}",),
                caption_regime_supported=True,
                temporal_alignment_supported=True,
                speech_presence_supported=True,
                evidence_for=("persisted_speaker_decision",),
            )
            for number, start, end, frame_id, pts_ms, vad_id in specs
        ),
    )


def _decision_records(
    records: tuple[SpeakerTurnAuthorityRecord, ...],
) -> tuple[
    ConfirmationRequestRecord | ConfirmationAnswerRecord | ConfirmationAppliedRecord,
    ...,
]:
    output: list[
        ConfirmationRequestRecord | ConfirmationAnswerRecord | ConfirmationAppliedRecord
    ] = []
    for cue_sequence, record in enumerate(records, start=1):
        decision_id = ConfirmationId(record.verification_decision_ids[0])
        ordinal = len(output) + 1
        output.extend(
            (
                ConfirmationRequestRecord(
                    ordinal=ordinal,
                    item=ConfirmationItem(
                        confirmation_id=decision_id,
                        cue_sequence=cue_sequence,
                        field=ConfirmationField.SPEAKER,
                        start_tc_ms=Milliseconds(record.start_tc_ms),
                        end_tc_ms=Milliseconds(record.end_tc_ms),
                        current_value=None,
                        candidates=(record.speaker,),
                        evidence=record.verification_evidence_ids,
                    ),
                ),
                ConfirmationAnswerRecord(
                    ordinal=ordinal + 1,
                    confirmation_id=decision_id,
                    answer=record.speaker,
                ),
                ConfirmationAppliedRecord(
                    ordinal=ordinal + 2,
                    confirmation_id=decision_id,
                    applied_value=record.speaker,
                    workflow_status=WorkflowStatus.PROCESSING,
                ),
            )
        )
    return tuple(output)


__all__ = ["persist_admitted_speaker_turn_authority"]
