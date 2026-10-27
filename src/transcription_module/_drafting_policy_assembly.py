# Copyright 2026 Transcription Module contributors

"""Policy-bound whole-transcript construction and atomic publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcription_module._drafting_models import (
    DraftCue,
    UnresolvedItem,
    UnresolvedReasonCode,
)
from transcription_module._drafting_reconciliation import (
    PolicyReconciliation,
    reconcile_policy_windows,
)
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationItemKind,
    VisualInsertionAction,
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
from transcription_module.provenance_errors import CueProvenanceError
from transcription_module.speaker_notation import (
    SpeakerNotationError,
    parse_speaker_labels,
)
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.window_evidence_errors import fail_window_evidence
from transcription_module.window_snapshot import opened_window_evidence

if TYPE_CHECKING:
    from transcription_module._drafting_models import (
        WindowDraftArtifact,
    )
    from transcription_module._drafting_reconciliation_models import (
        ReconciledProvisionalInsertion,
    )
    from transcription_module._drafting_window_evidence import WindowEvidenceSnapshot
    from transcription_module.storage import RunStore


@dataclass(frozen=True, slots=True)
class PolicyConfirmationProjection:
    """Transcript plus typed requests projected from one reconciliation."""

    transcript: Transcript
    unresolved: tuple[UnresolvedItem, ...]
    visual_confirmation_items: tuple[ConfirmationItem, ...]
    provisional_cue_ids: tuple[CueId, ...]


@dataclass(frozen=True, slots=True)
class _CueSeed:
    cue: DraftCue
    identity_refs: tuple[str, ...]
    source_index: int | None
    provisional_index: int | None


def validate_policy_bound_assembly(
    store: RunStore,
    run_id: RunId,
    windows: WindowDraftArtifact,
) -> None:
    """Validate policy claims against the current persisted evidence."""
    reconciliation = reconcile_policy_windows(windows)
    with opened_window_evidence(store, run_id) as current:
        validate_current_authority(reconciliation, current.evidence, run_id)
        current.files.assert_bound()


def validate_current_authority(
    reconciliation: PolicyReconciliation,
    current: WindowEvidenceSnapshot,
    run_id: RunId,
) -> None:
    if (
        current.era is not EvidenceEra.POLICY_BOUND
        or current.media.run_id != run_id
        or current.policy_digest != reconciliation.policy_digest
        or current.source_digest != reconciliation.source_digest
    ):
        fail_window_evidence("policy assembly source authority is stale")
    frame_points = {(item.pts_ms, item.frame_id) for item in current.media.frames}
    if any(
        point not in frame_points for point in reconciliation.inspected_frame_points
    ):
        fail_window_evidence("policy assembly references an unknown source frame")


def build_policy_cues(
    run_id: RunId,
    reconciliation: PolicyReconciliation,
) -> tuple[Cue, ...]:
    identity_inputs = tuple(
        CueIdentityInput(
            era=EvidenceEra.POLICY_BOUND,
            source_evidence_ids=item.identity_refs,
            start_tc_ms=item.cue.start_tc_ms,
            end_tc_ms=item.cue.end_tc_ms,
            speech=item.cue.speech,
        )
        for item in reconciliation.cues
    )
    cue_ids = assign_cue_ids(
        run_id,
        reconciliation.policy_digest,
        identity_inputs,
    )
    unresolved_targets = {
        item.target_cue_index for cue in reconciliation.cues for item in cue.unresolved
    }
    if None in unresolved_targets:
        message = "policy unresolved target is missing"
        raise CueProvenanceError(message)
    return tuple(
        Cue(
            sequence=index + 1,
            cue_id=cue_id,
            start_tc_ms=Milliseconds(item.cue.start_tc_ms),
            end_tc_ms=Milliseconds(item.cue.end_tc_ms),
            speech=item.cue.speech,
            speaker=item.cue.speaker,
            uncertainty=(
                Uncertainty.NEEDS_CONFIRMATION
                if index in unresolved_targets
                else Uncertainty.CERTAIN
            ),
            screen_text=item.cue.screen_text,
            vision_evidence_ids=(),
        )
        for index, (item, cue_id) in enumerate(
            zip(reconciliation.cues, cue_ids, strict=True)
        )
    )


def build_policy_confirmation_projection(
    run_id: RunId,
    reconciliation: PolicyReconciliation,
    *,
    duration_ms: int,
) -> PolicyConfirmationProjection:
    """Merge authorized and provisional cues in deterministic video order."""
    seeds = [
        _CueSeed(item.cue, item.identity_refs, index, None)
        for index, item in enumerate(reconciliation.cues)
    ]
    seeds.extend(
        _CueSeed(
            DraftCue(
                start_tc_ms=item.insertion.start_tc_ms,
                end_tc_ms=item.insertion.end_tc_ms,
                speech=item.insertion.speech,
                speaker=item.insertion.speaker,
            ),
            (
                f"provisional:{item.insertion.provisional_id}",
                f"candidate:{item.insertion.candidate_id}",
                f"observation:{item.insertion.observation_id}",
                *(
                    f"vad:{identity}"
                    for identity in item.insertion.vad_interval_ids
                ),
                f"turn:{item.insertion.turn_id}",
            ),
            None,
            index,
        )
        for index, item in enumerate(reconciliation.provisional_insertions)
    )
    ordered = tuple(
        sorted(
            seeds,
            key=lambda item: (
                item.cue.start_tc_ms,
                item.cue.end_tc_ms,
                item.cue.speech,
                item.identity_refs,
            ),
        )
    )
    cue_ids = assign_cue_ids(
        run_id,
        reconciliation.policy_digest,
        tuple(
            CueIdentityInput(
                era=EvidenceEra.POLICY_BOUND,
                source_evidence_ids=item.identity_refs,
                start_tc_ms=item.cue.start_tc_ms,
                end_tc_ms=item.cue.end_tc_ms,
                speech=item.cue.speech,
            )
            for item in ordered
        ),
    )
    authorized_positions = {
        item.source_index: index
        for index, item in enumerate(ordered)
        if item.source_index is not None
    }
    unresolved = tuple(
        pending.model_copy(
            update={"target_cue_index": authorized_positions[source_index]}
        )
        for source_index, item in enumerate(reconciliation.cues)
        for pending in item.unresolved
    )
    unresolved_positions = {
        item.target_cue_index for item in unresolved
    }
    cues = tuple(
        Cue(
            sequence=index + 1,
            cue_id=cue_id,
            start_tc_ms=Milliseconds(item.cue.start_tc_ms),
            end_tc_ms=Milliseconds(item.cue.end_tc_ms),
            speech=item.cue.speech,
            speaker=item.cue.speaker,
            uncertainty=(
                Uncertainty.NEEDS_CONFIRMATION
                if index in unresolved_positions
                or item.provisional_index is not None
                else Uncertainty.CERTAIN
            ),
            screen_text=item.cue.screen_text,
            vision_evidence_ids=(),
        )
        for index, (item, cue_id) in enumerate(
            zip(ordered, cue_ids, strict=True)
        )
    )
    transcript = Transcript(
        run_id=run_id,
        workflow_status=(
            WorkflowStatus.PROCESSING
            if unresolved or reconciliation.provisional_insertions
            else WorkflowStatus.READY_FOR_VALIDATION
        ),
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(duration_ms),
        cues=cues,
        vision_evidence=(),
    )
    visual_items = tuple(
        _visual_insertion_confirmation_item(
            cue,
            reconciliation.provisional_insertions[seed.provisional_index],
        )
        for seed, cue in zip(ordered, cues, strict=True)
        if seed.provisional_index is not None
    )
    provisional_cue_ids = tuple(
        item.cue_id
        for item in visual_items
        if item.cue_id is not None
    )
    return PolicyConfirmationProjection(
        transcript,
        unresolved,
        visual_items,
        provisional_cue_ids,
    )


def _visual_insertion_confirmation_item(
    cue: Cue,
    reconciled: ReconciledProvisionalInsertion,
) -> ConfirmationItem:
    insertion = reconciled.insertion
    identity = hashlib.sha256(
        f"{cue.cue_id}\0{insertion.provisional_id}".encode()
    ).hexdigest()[:32]
    return ConfirmationItem(
        kind=ConfirmationItemKind.VISUAL_INSERTION,
        confirmation_id=ConfirmationId(f"confirmation-{identity}"),
        cue_id=cue.cue_id,
        cue_sequence=cue.sequence,
        field=None,
        provisional_id=insertion.provisional_id,
        actions=(
            VisualInsertionAction.ACCEPT,
            VisualInsertionAction.REJECT,
        ),
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        current_value=cue.speech,
        cue_speech=cue.speech,
        candidates=(),
        evidence=(
            insertion.candidate_id,
            insertion.observation_id,
            *insertion.vad_interval_ids,
            *((insertion.turn_id,) if insertion.turn_id is not None else ()),
            *insertion.disposition_evidence_ids,
        ),
    )


def confirmation_items(
    transcript: Transcript,
    unresolved: tuple[UnresolvedItem, ...],
    *,
    deferred_speaker_cue_ids: tuple[CueId, ...] = (),
) -> tuple[ConfirmationItem, ...]:
    """Build the unique business requests later authored by the command."""
    combined = (
        *unresolved,
        *_speaker_mismatch_items(
            transcript,
            unresolved,
            deferred_speaker_cue_ids,
        ),
    )
    items = tuple(confirmation_item(transcript, item) for item in combined)
    ids = tuple(item.confirmation_id for item in items)
    if len(ids) != len(set(ids)):
        message = "deterministic confirmation ID collision"
        raise CueProvenanceError(message)
    return items


def _speaker_mismatch_items(
    transcript: Transcript,
    unresolved: tuple[UnresolvedItem, ...],
    deferred_speaker_cue_ids: tuple[CueId, ...],
) -> tuple[UnresolvedItem, ...]:
    existing = {
        item.target_cue_index
        for item in unresolved
        if item.field == ConfirmationField.SPEAKER.value
    }
    items: list[UnresolvedItem] = []
    for index, cue in enumerate(transcript.cues):
        if index in existing or cue.cue_id in deferred_speaker_cue_ids:
            continue
        try:
            labels = parse_speaker_labels(cue.speaker)
        except SpeakerNotationError:
            labels = ()
        speech_lines = cue.speech.split("\n")
        if labels and (len(speech_lines) == 1 or len(labels) == len(speech_lines)):
            continue
        items.append(
            UnresolvedItem(
                at_ms=cue.start_tc_ms,
                field=ConfirmationField.SPEAKER.value,
                reason="speaker count does not match utterance line count",
                reason_code=UnresolvedReasonCode.SPEAKER_AMBIGUITY,
                target_cue_index=index,
                source_evidence_ids=(f"cue:{cue.cue_id}",),
            )
        )
    return tuple(items)


def confirmation_item(
    transcript: Transcript,
    unresolved: UnresolvedItem,
) -> ConfirmationItem:
    index = unresolved.target_cue_index
    if index is None or index >= len(transcript.cues):
        message = "policy unresolved target is outside the transcript"
        raise CueProvenanceError(message)
    cue = transcript.cues[index]
    field = ConfirmationField(unresolved.field)
    current_value = {
        ConfirmationField.SPEECH: cue.speech,
        ConfirmationField.SPEAKER: cue.speaker,
        ConfirmationField.SCREEN_TEXT: cue.screen_text,
    }[field]
    identity = json.dumps(
        {
            "cue_id": cue.cue_id,
            "field": field,
            "reason_code": unresolved.reason_code,
            "sources": unresolved.source_evidence_ids,
            "candidates": unresolved.candidates,
            "receipts": unresolved.candidate_receipt_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    confirmation_id = ConfirmationId(
        f"confirmation-{hashlib.sha256(identity).hexdigest()[:32]}"
    )
    return ConfirmationItem(
        confirmation_id=confirmation_id,
        cue_id=cue.cue_id,
        cue_sequence=cue.sequence,
        field=field,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        current_value=current_value,
        cue_speech=cue.speech,
        candidates=unresolved.candidates,
        evidence=(
            unresolved.reason,
            *unresolved.source_evidence_ids,
            *unresolved.candidate_receipt_ids,
        ),
    )


__all__ = [
    "build_policy_confirmation_projection",
    "validate_policy_bound_assembly",
]
