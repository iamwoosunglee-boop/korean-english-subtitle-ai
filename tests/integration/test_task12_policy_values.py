# Copyright 2026 Transcription Module contributors

"""Machine-consumed values emitted by deterministic policy assembly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

from tests.integration.task12_assembly_support import (
    assemble_without_agent,
    persist_policy_windows,
    policy_assembly_fixture,
)
from tests.integration.window_keep_claim_support import (
    automatic_keep_draft,
    simultaneous_unresolved_draft,
)
from transcription_module._drafting_models import (
    UnresolvedItem,
    UnresolvedReasonCode,
)
from transcription_module._drafting_policy_assembly import (
    build_policy_cues,
    confirmation_item,
    confirmation_items,
)
from transcription_module._drafting_reconciliation import reconcile_policy_windows
from transcription_module.confirmation_models import (
    CONFIRMATION_RECORD_ADAPTER,
    ConfirmationField,
    ConfirmationRequestRecord,
)
from transcription_module.cue_provenance import CueIdentityInput, assign_cue_ids
from transcription_module.models import (
    Cue,
    CueId,
    Milliseconds,
    RunId,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.speaker_inference import (
    SpeakerInferenceDecision,
    apply_speaker_inference,
)
from transcription_module.visual_evidence_policy import EvidenceEra

if TYPE_CHECKING:
    from pathlib import Path


def _transcript(run_id: RunId) -> Transcript:
    cue = Cue(
        sequence=1,
        cue_id=CueId("cue-machine-value"),
        start_tc_ms=Milliseconds(100),
        end_tc_ms=Milliseconds(900),
        speech="기계 계약 원문",
        speaker="화자A",
        uncertainty=Uncertainty.NEEDS_CONFIRMATION,
        screen_text="화면 문구",
    )
    return Transcript(
        run_id=run_id,
        workflow_status=WorkflowStatus.PROCESSING,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(1_000),
        cues=(cue,),
        vision_evidence=(),
    )


def _unresolved(field: str = "speech") -> UnresolvedItem:
    return UnresolvedItem(
        at_ms=100,
        field=field,
        reason="valid receipts disagree",
        candidates=("완전한 대안",),
        reason_code=UnresolvedReasonCode.VISUAL_EVIDENCE_CONFLICT,
        target_cue_index=0,
        source_evidence_ids=("asr:1", "vision:obs-1"),
        candidate_receipt_ids=("receipt-1",),
    )


def test_policy_cue_id_and_screen_text_are_exact_machine_values(
    tmp_path: Path,
) -> None:
    """Given one reconciled cue, its deterministic ID and screen text are exact."""
    fixture = policy_assembly_fixture(tmp_path)
    artifact = persist_policy_windows(
        fixture,
        (automatic_keep_draft(fixture.window_input),),
    )
    reconciliation = reconcile_policy_windows(artifact)
    reconciled = reconciliation.cues[0]
    screen_text = "서울 재회"
    with_screen = replace(
        reconciliation,
        cues=(
            replace(
                reconciled,
                cue=reconciled.cue.model_copy(update={"screen_text": screen_text}),
            ),
        ),
    )
    expected_id = assign_cue_ids(
        fixture.run_id,
        reconciliation.policy_digest,
        (
            CueIdentityInput(
                era=EvidenceEra.POLICY_BOUND,
                source_evidence_ids=reconciled.identity_refs,
                start_tc_ms=reconciled.cue.start_tc_ms,
                end_tc_ms=reconciled.cue.end_tc_ms,
                speech=reconciled.cue.speech,
            ),
        ),
    )[0]

    cue = build_policy_cues(fixture.run_id, with_screen)[0]

    assert cue.cue_id == expected_id
    assert cue.screen_text == screen_text
    assert cue.vision_evidence_ids == ()


def test_confirmation_identity_and_business_fields_are_exact() -> None:
    """Given unresolved fields, deterministic command inputs remain stable."""
    transcript = _transcript(RunId("task12-confirmation-values"))
    unresolved = _unresolved()
    identity = json.dumps(
        {
            "cue_id": transcript.cues[0].cue_id,
            "field": unresolved.field,
            "reason_code": unresolved.reason_code,
            "sources": unresolved.source_evidence_ids,
            "candidates": unresolved.candidates,
            "receipts": unresolved.candidate_receipt_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    expected_id = f"confirmation-{hashlib.sha256(identity).hexdigest()[:32]}"
    item = confirmation_item(transcript, unresolved)
    items = confirmation_items(
        transcript,
        (unresolved, _unresolved("speaker")),
    )

    assert str(item.confirmation_id) == expected_id
    assert item.cue_id == transcript.cues[0].cue_id
    assert item.current_value == transcript.cues[0].speech
    assert item.cue_speech == transcript.cues[0].speech
    assert item.evidence == (
        unresolved.reason,
        *unresolved.source_evidence_ids,
        *unresolved.candidate_receipt_ids,
    )
    assert items[0] == item
    assert tuple(value.field for value in items) == (
        ConfirmationField.SPEECH,
        ConfirmationField.SPEAKER,
    )


def test_speaker_line_mismatch_queues_one_targeted_confirmation() -> None:
    """A speaker/utterance mismatch becomes one speaker request, not late failure."""
    transcript = _transcript(RunId("task12-speaker-mismatch"))
    cue = transcript.cues[0].model_copy(
        update={
            "speech": "첫 발화\n둘째 발화",
            "speaker": "화자A",
            "uncertainty": Uncertainty.CERTAIN,
        }
    )
    transcript = transcript.model_copy(update={"cues": (cue,)})

    items = confirmation_items(transcript, ())

    assert len(items) == 1
    assert items[0].cue_id == cue.cue_id
    assert items[0].field is ConfirmationField.SPEAKER
    assert items[0].current_value == cue.speaker


def test_publication_uses_canonical_model_and_request_jsonl_bytes(
    tmp_path: Path,
) -> None:
    """Given one confirmation, canonical JSON and JSONL bytes are exact."""
    fixture = policy_assembly_fixture(tmp_path)
    draft = simultaneous_unresolved_draft(fixture.window_input)
    _ = persist_policy_windows(fixture, (draft,))

    artifact = assemble_without_agent(fixture)
    paths = fixture.store.paths(fixture.run_id)
    transcript = fixture.store.load_transcript(fixture.run_id)
    assert not paths.confirmations.exists()
    cue_id = transcript.cues[0].cue_id
    assert cue_id is not None
    _ = apply_speaker_inference(
        fixture.store,
        fixture.run_id,
        (
            SpeakerInferenceDecision(
                cue_id=cue_id,
                reason="동시 발화 소유권이 불명확함",
                candidates=("화자A", "화자B"),
            ),
        ),
    )
    transcript = fixture.store.load_transcript(fixture.run_id)
    records = tuple(
        CONFIRMATION_RECORD_ADAPTER.validate_json(line)
        for line in paths.confirmations.read_bytes().splitlines(keepends=True)
    )

    assert paths.transcript.read_bytes() == (
        f"{transcript.model_dump_json(indent=2)}\n".encode()
    )
    assert paths.whole_transcript_artifact.read_bytes() == (
        f"{artifact.model_dump_json(indent=2)}\n".encode()
    )
    assert (
        paths.confirmations.read_bytes()
        == "".join(f"{record.model_dump_json()}\n" for record in records).encode()
    )
    assert tuple(record.ordinal for record in records) == (1,)
    assert all(isinstance(record, ConfirmationRequestRecord) for record in records)
