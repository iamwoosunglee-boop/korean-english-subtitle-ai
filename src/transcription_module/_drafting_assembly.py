# Copyright 2026 Transcription Module contributors

"""Legacy whole-transcript assembly and confirmation projection."""

from __future__ import annotations

from typing import overload
from uuid import uuid4

from transcription_module._drafting_models import (
    DraftCue,
    UnresolvedItem,
    WholeTranscriptArtifact,
    WholeTranscriptAssembler,
    WholeTranscriptInput,
    WindowDraftArtifact,
)
from transcription_module._drafting_policy_assembly import (
    build_policy_confirmation_projection,
    confirmation_items,
    validate_policy_bound_assembly,
)
from transcription_module._drafting_reconciliation import (
    reconcile_policy_windows,
)
from transcription_module.confirmation import create_confirmation_session
from transcription_module.confirmation_log import (
    append_confirmation_requests,
)
from transcription_module.confirmation_models import (
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
)
from transcription_module.media import PreparedMediaManifest
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
)
from transcription_module.provenance_errors import CueProvenanceError
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.window_evidence_errors import fail_window_evidence


async def assemble_whole_transcript(
    store: RunStore,
    run_id: RunId,
    assembler: WholeTranscriptAssembler,
) -> WholeTranscriptArtifact:
    """Route policy evidence to reconciliation and preserve legacy assembly."""
    paths = store.paths(run_id)
    windows = WindowDraftArtifact.model_validate_json(
        paths.window_drafts.read_text(encoding="utf-8")
    )
    era = _artifact_era(windows)
    if era is EvidenceEra.POLICY_BOUND:
        return _assemble_policy_transcript(store, run_id, windows)
    if not all(draft.evidence_reviewed for draft in windows.drafts):
        message = "window drafts must be evidence-reviewed before assembly"
        raise ValueError(message)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    assembled = await assembler(
        WholeTranscriptInput(
            run_dir=paths.run_dir,
            window_drafts=windows,
            frames=media.frames,
        )
    )
    known_frame_ids = {frame.frame_id for frame in media.frames}
    if any(
        frame_id not in known_frame_ids for frame_id in assembled.inspected_frame_ids
    ):
        message = "assembler inspected an unknown frame ID"
        raise ValueError(message)
    visual_unresolved = any(
        item.field in {"speaker", "screen_text"} for item in assembled.unresolved
    )
    if visual_unresolved and not assembled.inspected_frame_ids:
        message = "visual unresolved items require at least one inspected frame"
        raise ValueError(message)
    ordered = tuple(
        sorted(
            assembled.cues,
            key=lambda cue: (cue.start_tc_ms, cue.end_tc_ms, cue.speech),
        )
    )
    target_cues = {
        id(select_cue_for_unresolved(ordered, item)) for item in assembled.unresolved
    }
    cues = tuple(
        Cue(
            sequence=sequence,
            cue_id=CueId(str(uuid4())),
            start_tc_ms=Milliseconds(cue.start_tc_ms),
            end_tc_ms=Milliseconds(cue.end_tc_ms),
            speech=cue.speech,
            speaker=cue.speaker,
            uncertainty=(
                Uncertainty.NEEDS_CONFIRMATION
                if id(cue) in target_cues
                else Uncertainty.CERTAIN
            ),
            screen_text=cue.screen_text,
            vision_evidence_ids=(),
        )
        for sequence, cue in enumerate(ordered, start=1)
    )
    workflow_status = (
        WorkflowStatus.PROCESSING
        if assembled.unresolved
        else WorkflowStatus.READY_FOR_VALIDATION
    )
    transcript = Transcript(
        run_id=run_id,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(media.duration_ms),
        cues=cues,
        vision_evidence=(),
    )
    current_state = store.load_state(run_id)
    state = RunState(
        run_id=current_state.run_id,
        source_url=current_state.source_url,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        context=current_state.context,
    )
    requests = tuple(
        confirmation_item_for_unresolved(item, cues) for item in assembled.unresolved
    )
    artifact = WholeTranscriptArtifact(
        run_id=run_id,
        window_count=len(windows.drafts),
        inspected_frame_ids=assembled.inspected_frame_ids,
        unresolved=assembled.unresolved,
    )
    store.save_transcript(transcript)
    store.save_state(state)
    append_confirmation_requests(paths.confirmations, requests)
    write_model_atomic(paths.whole_transcript_artifact, artifact)
    if requests:
        _ = create_confirmation_session(store, run_id)
    return artifact


def _assemble_policy_transcript(
    store: RunStore,
    run_id: RunId,
    windows: WindowDraftArtifact,
) -> WholeTranscriptArtifact:
    """Build and publish one deterministic policy-bound transcript."""
    paths = store.paths(run_id)
    validate_policy_bound_assembly(store, run_id, windows)
    reconciliation = reconcile_policy_windows(windows)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    projection = build_policy_confirmation_projection(
        run_id,
        reconciliation,
        duration_ms=media.duration_ms,
    )
    transcript = projection.transcript
    unresolved = projection.unresolved
    workflow_status = transcript.workflow_status
    requests = (
        *projection.visual_confirmation_items,
        *confirmation_items(
            transcript,
            tuple(
                item
                for item in unresolved
                if item.field != ConfirmationField.SPEAKER.value
            ),
            deferred_speaker_cue_ids=tuple(
                cue.cue_id for cue in transcript.cues if cue.cue_id is not None
            ),
        ),
    )
    current_state = store.load_state(run_id)
    state = current_state.model_copy(
        update={
            "workflow_status": workflow_status,
            "validation_status": ValidationStatus.PENDING,
        }
    )
    artifact = WholeTranscriptArtifact(
        run_id=run_id,
        window_count=len(windows.drafts),
        inspected_frame_ids=reconciliation.inspected_frame_ids,
        unresolved=unresolved,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=reconciliation.policy_digest,
        source_digest=reconciliation.source_digest,
        applied_corrections=tuple(item.receipt for item in reconciliation.cues),
        provisional_visual_insertions=tuple(
            item.insertion for item in reconciliation.provisional_insertions
        ),
    )
    if paths.whole_transcript_artifact.exists() or paths.transcript.exists():
        if (
            paths.whole_transcript_artifact.exists()
            and paths.transcript.exists()
            and WholeTranscriptArtifact.model_validate_json(
                paths.whole_transcript_artifact.read_bytes()
            )
            == artifact
            and _semantic_transcript(store.load_transcript(run_id))
            == _semantic_transcript(transcript)
        ):
            return artifact
        message = "deterministic policy assembly conflicts with persisted bytes"
        raise CueProvenanceError(message)
    store.save_transcript(transcript)
    store.save_state(state)
    append_confirmation_requests(paths.confirmations, requests)
    write_model_atomic(paths.whole_transcript_artifact, artifact)
    if requests:
        _ = create_confirmation_session(store, run_id)
    return artifact


def _semantic_transcript(transcript: Transcript) -> Transcript:
    """Ignore lifecycle-only changes when replaying deterministic assembly."""
    return transcript.model_copy(
        update={
            "workflow_status": WorkflowStatus.PROCESSING,
            "validation_status": ValidationStatus.PENDING,
        }
    )


def _artifact_era(windows: WindowDraftArtifact) -> EvidenceEra:
    eras = {
        *(item.era for item in windows.inputs),
        *(item.era for item in windows.drafts),
    }
    if not eras or eras == {EvidenceEra.LEGACY}:
        return EvidenceEra.LEGACY
    if eras == {EvidenceEra.POLICY_BOUND}:
        return EvidenceEra.POLICY_BOUND
    return fail_window_evidence("whole-transcript assembly cannot mix evidence eras")


@overload
def select_cue_for_unresolved(
    cues: tuple[DraftCue, ...],
    item: UnresolvedItem,
) -> DraftCue: ...


@overload
def select_cue_for_unresolved(
    cues: tuple[Cue, ...],
    item: UnresolvedItem,
) -> Cue: ...


def select_cue_for_unresolved(
    cues: tuple[DraftCue, ...] | tuple[Cue, ...],
    item: UnresolvedItem,
) -> DraftCue | Cue:
    """Select the cue that owns an unresolved timestamp."""
    return next(
        (candidate for candidate in cues if candidate.start_tc_ms == item.at_ms),
        next(
            (
                candidate
                for candidate in sorted(
                    cues,
                    key=lambda candidate: candidate.start_tc_ms,
                    reverse=True,
                )
                if candidate.start_tc_ms <= item.at_ms < candidate.end_tc_ms
            ),
            min(cues, key=lambda candidate: abs(candidate.start_tc_ms - item.at_ms)),
        ),
    )


def confirmation_item_for_unresolved(
    item: UnresolvedItem,
    cues: tuple[Cue, ...],
) -> ConfirmationItem:
    """Build a confirmation item for one unresolved cue field."""
    cue = select_cue_for_unresolved(cues, item)
    field = ConfirmationField(item.field)
    current_value = {
        ConfirmationField.SPEECH: cue.speech,
        ConfirmationField.SPEAKER: cue.speaker,
        ConfirmationField.SCREEN_TEXT: cue.screen_text,
    }[field]
    return ConfirmationItem(
        confirmation_id=ConfirmationId(str(uuid4())),
        cue_id=cue.cue_id,
        cue_sequence=cue.sequence,
        field=field,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        current_value=current_value,
        cue_speech=cue.speech,
        candidates=item.candidates,
        evidence=(item.reason,),
    )


__all__ = [
    "assemble_whole_transcript",
    "confirmation_item_for_unresolved",
    "select_cue_for_unresolved",
]
