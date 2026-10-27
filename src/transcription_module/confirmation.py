# Copyright 2026 Transcription Module contributors

"""Apply one confirmation answer to canonical transcript state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Never
from uuid import uuid4

from transcription_module.confirmation_log import (
    append_confirmation_records,
    append_confirmation_requests,
    load_confirmation_history,
    next_confirmation_item,
)
from transcription_module.confirmation_models import (
    ConfirmationAnswerRecord,
    ConfirmationAppliedRecord,
    ConfirmationError,
    ConfirmationErrorCode,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationItemKind,
    ConfirmationRecord,
    ConfirmationRequestRecord,
    ConfirmationRetiredRecord,
    ConfirmationSession,
    ConfirmationSessionId,
    ConfirmationSessionStatus,
    ConfirmationSuggestionRecord,
    ConfirmationVisualInsertionAnswerRecord,
    ConfirmationVisualInsertionAppliedRecord,
    VisualInsertionAction,
)
from transcription_module.models import (
    Cue,
    CueId,
    ProcessResult,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.storage import write_model_atomic

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from transcription_module.confirmation_log import ConfirmationHistory
    from transcription_module.storage import RunStore


@dataclass(frozen=True, slots=True)
class _VisualInsertionTransition:
    transcript: Transcript
    workflow_status: WorkflowStatus
    speaker_request: ConfirmationItem | None
    retired_requests: tuple[ConfirmationItem, ...]


@dataclass(frozen=True, slots=True)
class _ConfirmationContext:
    store: RunStore
    run_id: RunId


@dataclass(frozen=True, slots=True)
class _VisualApplicationPublication:
    item: ConfirmationItem
    action: VisualInsertionAction
    before_digest: str
    after_digest: str
    scope_digest: str
    transition: _VisualInsertionTransition


def enqueue_review_confirmation_conflicts(
    store: RunStore,
    run_id: RunId,
    *,
    cue_id: CueId,
    proposed_values: Mapping[ConfirmationField, str | None],
    evidence: tuple[str, ...],
) -> tuple[ConfirmationItem, ...]:
    """Queue contradictory review fields that a user previously confirmed."""
    paths = store.paths(run_id)
    history = load_confirmation_history(paths.confirmations)
    requests_by_id = {
        request.confirmation_id: request for request in history.requests
    }
    applied_fields = {
        (request.cue_id, request.field)
        for confirmation_id, request in requests_by_id.items()
        if confirmation_id in history.applied_ids and request.cue_id is not None
    }
    unanswered_fields = {
        (request.cue_id, request.field)
        for request in history.requests
        if request.confirmation_id not in history.resolved_ids
    }
    transcript = store.load_transcript(run_id)
    cue = next(item for item in transcript.cues if item.cue_id == cue_id)
    queued = tuple(
        ConfirmationItem(
            confirmation_id=ConfirmationId(uuid4().hex),
            cue_id=cue_id,
            cue_sequence=cue.sequence,
            field=field,
            start_tc_ms=cue.start_tc_ms,
            end_tc_ms=cue.end_tc_ms,
            current_value=_field_value(cue, field),
            cue_speech=cue.speech,
            candidates=(_confirmation_candidate(proposed_values[field]),),
            evidence=evidence,
        )
        for field in ConfirmationField
        if (
            field in proposed_values
            and (cue_id, field) in applied_fields
            and (cue_id, field) not in unanswered_fields
            and proposed_values[field] != _field_value(cue, field)
        )
    )
    if not queued:
        return ()

    append_confirmation_requests(paths.confirmations, queued)
    store.save_transcript(
        transcript.model_copy(
            update={
                "workflow_status": WorkflowStatus.NEEDS_CONFIRMATION,
                "validation_status": ValidationStatus.PENDING,
                "cues": tuple(
                    item.model_copy(
                        update={"uncertainty": Uncertainty.NEEDS_CONFIRMATION}
                    )
                    if item.cue_id == cue_id
                    else item
                    for item in transcript.cues
                ),
            }
        )
    )
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={
                "workflow_status": WorkflowStatus.NEEDS_CONFIRMATION,
                "validation_status": ValidationStatus.PENDING,
            }
        )
    )
    return queued


def create_confirmation_session(
    store: RunStore,
    run_id: RunId,
) -> ConfirmationSession:
    """Start a new consent attempt without exposing its first item."""
    paths = store.paths(run_id)
    if next_confirmation_item(paths.confirmations) is None:
        raise ConfirmationError(
            ConfirmationErrorCode.NO_PENDING_ITEM,
            "no unanswered confirmation remains",
        )
    if paths.confirmation_session.exists():
        current = ConfirmationSession.model_validate_json(
            paths.confirmation_session.read_text(encoding="utf-8")
        )
        if current.status in {
            ConfirmationSessionStatus.AWAITING_CONSENT,
            ConfirmationSessionStatus.ACTIVE,
        }:
            raise ConfirmationError(
                ConfirmationErrorCode.SESSION_ACTIVE,
                "a confirmation session is already open",
            )
    session = ConfirmationSession(
        session_id=ConfirmationSessionId(str(uuid4())),
        status=ConfirmationSessionStatus.AWAITING_CONSENT,
        source_digest=_working_digest(store.load_transcript(run_id)),
        scope_digest=_confirmation_scope_digest(store, run_id),
    )
    write_model_atomic(paths.confirmation_session, session)
    _set_workflow(store, run_id, WorkflowStatus.WAITING_CONFIRMATION_CONSENT)
    return session


def set_confirmation_consent(
    store: RunStore,
    run_id: RunId,
    *,
    accepted: bool,
) -> ConfirmationSession:
    """Accept or decline the current attempt before item access."""
    session = _load_session(store, run_id)
    if session.status is not ConfirmationSessionStatus.AWAITING_CONSENT:
        raise ConfirmationError(
            ConfirmationErrorCode.SESSION_ENDED,
            "confirmation consent is no longer pending",
        )
    updated = session.model_copy(
        update={
            "status": (
                ConfirmationSessionStatus.ACTIVE
                if accepted
                else ConfirmationSessionStatus.DECLINED
            ),
            "source_digest": (
                _working_digest(store.load_transcript(run_id))
                if accepted
                else session.source_digest
            ),
            "scope_digest": (
                _confirmation_scope_digest(store, run_id)
                if accepted
                else session.scope_digest
            ),
        }
    )
    write_model_atomic(store.paths(run_id).confirmation_session, updated)
    _set_workflow(
        store,
        run_id,
        (
            WorkflowStatus.NEEDS_CONFIRMATION
            if accepted
            else WorkflowStatus.CONFIRMATION_DECLINED
        ),
    )
    return updated


def get_next_confirmation(
    store: RunStore,
    run_id: RunId,
) -> ConfirmationItem:
    """Return the next item only inside an active consented session."""
    _require_active_session(store, run_id)
    item = next_confirmation_item(store.paths(run_id).confirmations)
    if item is None:
        raise ConfirmationError(
            ConfirmationErrorCode.NO_PENDING_ITEM,
            "no unanswered confirmation remains",
        )
    return item.model_copy(
        update={
            "candidates": tuple(
                candidate
                for candidate in item.candidates
                if "..." not in candidate and "…" not in candidate
            )
        }
    )


def record_confirmation_suggestion(
    store: RunStore,
    run_id: RunId,
    confirmation_id: ConfirmationId,
    suggestion: str,
) -> None:
    """Record an agent suggestion without answering or applying it."""
    item = get_next_confirmation(store, run_id)
    if item.confirmation_id != confirmation_id:
        raise ConfirmationError(
            ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
            "suggestion does not reference the current confirmation",
        )
    normalized = suggestion.strip()
    if not normalized:
        raise ConfirmationError(
            ConfirmationErrorCode.EMPTY_ANSWER,
            "confirmation suggestion must not be empty",
        )
    history = load_confirmation_history(store.paths(run_id).confirmations)
    append_confirmation_records(
        store.paths(run_id).confirmations,
        (
            ConfirmationSuggestionRecord(
                ordinal=len(history.records) + 1,
                confirmation_id=confirmation_id,
                suggestion=normalized,
            ),
        ),
    )


def apply_confirmation_answer(
    store: RunStore,
    run_id: RunId,
    confirmation_id: ConfirmationId,
    answer: str,
) -> ProcessResult:
    """Apply one answer, regenerate JSON/CSV, and preserve append-only history."""
    with store.lock_run(run_id):
        return _apply_confirmation_answer_unlocked(
            store,
            run_id,
            confirmation_id,
            answer,
        )


def apply_visual_insertion_confirmation(
    store: RunStore,
    run_id: RunId,
    confirmation_id: ConfirmationId,
    action: VisualInsertionAction,
) -> ProcessResult:
    """Apply one explicit provisional-insertion decision under the run lock."""
    with store.lock_run(run_id):
        return _apply_visual_insertion_confirmation_unlocked(
            store,
            run_id,
            confirmation_id,
            action,
        )


def _apply_visual_insertion_confirmation_unlocked(
    store: RunStore,
    run_id: RunId,
    confirmation_id: ConfirmationId,
    action: VisualInsertionAction,
) -> ProcessResult:
    path = store.paths(run_id).confirmations
    history = load_confirmation_history(path)
    transcript = store.load_transcript(run_id)
    scope_digest = _confirmation_scope_digest(store, run_id)
    context = _ConfirmationContext(store, run_id)
    applied = next(
        (
            record
            for record in history.records
            if isinstance(record, ConfirmationVisualInsertionAppliedRecord)
            and record.confirmation_id == confirmation_id
        ),
        None,
    )
    if applied is not None:
        return _replay_visual_insertion_application(
            context,
            transcript,
            applied,
            action,
            scope_digest,
        )
    _require_active_session(store, run_id)
    item = _current_visual_insertion_item(history, confirmation_id, action)
    session = _load_session(store, run_id)
    answer = next(
        (
            record
            for record in history.records
            if isinstance(record, ConfirmationVisualInsertionAnswerRecord)
            and record.confirmation_id == confirmation_id
        ),
        None,
    )
    current_digest = _working_digest(transcript)
    if answer is None:
        _require_current_confirmation_scope(
            context,
            transcript,
            session,
            current_digest,
            scope_digest,
        )
        transition = _visual_insertion_transition(
            transcript,
            item,
            action,
            history,
            store.load_state(run_id),
        )
        expected_after = _working_digest(transition.transcript)
        answer = ConfirmationVisualInsertionAnswerRecord(
            ordinal=len(history.records) + 1,
            confirmation_id=confirmation_id,
            action=action,
            source_digest=current_digest,
            expected_source_digest_after=expected_after,
            scope_digest=scope_digest,
        )
        append_confirmation_records(
            path,
            (answer,),
        )
        history = load_confirmation_history(path)
    else:
        if answer.action is not action or answer.scope_digest != scope_digest:
            _invalidate_stale_confirmation(context, transcript, session)
        if current_digest not in {
            answer.source_digest,
            answer.expected_source_digest_after,
        }:
            _invalidate_stale_confirmation(context, transcript, session)
        transition = _visual_insertion_transition(
            transcript,
            item,
            action,
            history,
            store.load_state(run_id),
        )
        expected_after = answer.expected_source_digest_after
    _publish_visual_insertion_transition(store, run_id, transition)
    after_digest = _working_digest(transition.transcript)
    if after_digest != expected_after:
        _invalidate_stale_confirmation(
            context,
            transition.transcript,
            session,
        )
    _append_visual_insertion_application(
        path,
        history,
        _VisualApplicationPublication(
            item,
            action,
            answer.source_digest,
            after_digest,
            scope_digest,
            transition,
        ),
    )
    pending = transition.transcript.workflow_status is WorkflowStatus.NEEDS_CONFIRMATION
    write_model_atomic(
        store.paths(run_id).confirmation_session,
        session.model_copy(
            update={
                "status": (
                    session.status
                    if pending
                    else ConfirmationSessionStatus.COMPLETED
                ),
                "source_digest": after_digest,
                "scope_digest": scope_digest,
            }
        ),
    )
    return ProcessResult(
        run_id=run_id,
        workflow_status=transition.workflow_status,
        validation_status=ValidationStatus.PENDING,
    )


def _current_visual_insertion_item(
    history: ConfirmationHistory,
    confirmation_id: ConfirmationId,
    action: VisualInsertionAction,
) -> ConfirmationItem:
    item = history.next_item()
    if (
        item is None
        or item.confirmation_id != confirmation_id
        or item.kind is not ConfirmationItemKind.VISUAL_INSERTION
        or item.cue_id is None
        or item.provisional_id is None
        or action not in item.actions
    ):
        raise ConfirmationError(
            ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
            "confirmation does not reference the current visual insertion",
        )
    return item


def _visual_insertion_transition(
    transcript: Transcript,
    item: ConfirmationItem,
    action: VisualInsertionAction,
    history: ConfirmationHistory,
    state: RunState,
) -> _VisualInsertionTransition:
    target = next(
        (cue for cue in transcript.cues if cue.cue_id == item.cue_id),
        None,
    )
    dependent = tuple(
        request
        for request in history.requests
        if request.confirmation_id not in history.resolved_ids
        and request.confirmation_id != item.confirmation_id
        and request.depends_on_confirmation_id == item.confirmation_id
    )
    pending = tuple(
        request
        for request in history.requests
        if request.confirmation_id not in history.resolved_ids
        and request.confirmation_id != item.confirmation_id
        and request not in dependent
    )
    if action is VisualInsertionAction.REJECT:
        decided = tuple(
            cue for cue in transcript.cues if cue.cue_id != item.cue_id
        )
        speaker_request = None
        retired = dependent
    else:
        if target is None:
            raise ConfirmationError(
                ConfirmationErrorCode.STALE_CONFIRMATION,
                "accepted provisional insertion cue is missing",
            )
        existing_speaker = next(
            (
                request
                for request in dependent
                if request.field is ConfirmationField.SPEAKER
            ),
            None,
        )
        speaker_request = (
            _provisional_speaker_request(item, target, state=state)
            if target.speaker is None and existing_speaker is None
            else None
        )
        retained_dependent = (
            (existing_speaker,)
            if target.speaker is None and existing_speaker is not None
            else ()
        )
        pending = (*pending, *retained_dependent)
        if speaker_request is not None:
            pending = (*pending, speaker_request)
        retired = tuple(
            request for request in dependent if request not in retained_dependent
        )
        needs_confirmation = any(
            request.cue_id == target.cue_id for request in pending
        )
        decided = tuple(
            cue.model_copy(
                update={
                    "uncertainty": (
                        Uncertainty.NEEDS_CONFIRMATION
                        if needs_confirmation
                        else Uncertainty.CERTAIN
                    )
                }
            )
            if cue.cue_id == item.cue_id
            else cue
            for cue in transcript.cues
        )
    cues = tuple(
        _renumber_cue(cue, sequence)
        for sequence, cue in enumerate(decided, start=1)
    )
    workflow_status = (
        WorkflowStatus.NEEDS_CONFIRMATION
        if pending
        else WorkflowStatus.READY_FOR_VALIDATION
    )
    return _VisualInsertionTransition(
        transcript.model_copy(
            update={
                "workflow_status": workflow_status,
                "validation_status": ValidationStatus.PENDING,
                "cues": cues,
            }
        ),
        workflow_status,
        speaker_request,
        retired,
    )


def _publish_visual_insertion_transition(
    store: RunStore,
    run_id: RunId,
    transition: _VisualInsertionTransition,
) -> None:
    store.save_transcript(transition.transcript)
    state = store.load_state(run_id)
    store.save_state(
        state.model_copy(
            update={
                "workflow_status": transition.workflow_status,
                "validation_status": ValidationStatus.PENDING,
            }
        )
    )


def _append_visual_insertion_application(
    path: Path,
    history: ConfirmationHistory,
    publication: _VisualApplicationPublication,
) -> None:
    item = publication.item
    if item.provisional_id is None or item.cue_id is None:
        raise ConfirmationError(
            ConfirmationErrorCode.STALE_CONFIRMATION,
            "provisional insertion identity is missing",
        )
    next_ordinal = len(history.records) + 1
    records: list[ConfirmationRecord] = [
        ConfirmationVisualInsertionAppliedRecord(
            ordinal=next_ordinal,
            confirmation_id=item.confirmation_id,
            provisional_id=item.provisional_id,
            action=publication.action,
            cue_id=item.cue_id,
            source_digest_before=publication.before_digest,
            source_digest_after=publication.after_digest,
            scope_digest_before=publication.scope_digest,
            scope_digest_after=publication.scope_digest,
            workflow_status=publication.transition.workflow_status,
        )
    ]
    records.extend(
        ConfirmationRetiredRecord(
            ordinal=next_ordinal + offset,
            confirmation_id=request.confirmation_id,
            depends_on_confirmation_id=item.confirmation_id,
        )
        for offset, request in enumerate(
            publication.transition.retired_requests,
            start=1,
        )
    )
    if publication.transition.speaker_request is not None:
        records.append(
            ConfirmationRequestRecord(
                ordinal=next_ordinal + len(records),
                item=publication.transition.speaker_request,
            )
        )
    append_confirmation_records(path, tuple(records))


def _replay_visual_insertion_application(
    context: _ConfirmationContext,
    transcript: Transcript,
    applied: ConfirmationVisualInsertionAppliedRecord,
    action: VisualInsertionAction,
    scope_digest: str,
) -> ProcessResult:
    if applied.action is not action:
        raise ConfirmationError(
            ConfirmationErrorCode.DUPLICATE_ANSWER,
            "visual insertion already has a different decision",
        )
    if (
        applied.source_digest_after != _working_digest(transcript)
        or applied.scope_digest_after != scope_digest
    ):
        _invalidate_stale_confirmation(
            context,
            transcript,
            _load_session(context.store, context.run_id),
        )
    return ProcessResult(
        run_id=context.run_id,
        workflow_status=transcript.workflow_status,
        validation_status=transcript.validation_status,
    )


def _require_current_confirmation_scope(
    context: _ConfirmationContext,
    transcript: Transcript,
    session: ConfirmationSession,
    source_digest: str,
    scope_digest: str,
) -> None:
    if (
        session.source_digest is not None
        and session.source_digest != source_digest
    ) or (
        session.scope_digest is not None
        and session.scope_digest != scope_digest
    ):
        _invalidate_stale_confirmation(context, transcript, session)


def _invalidate_stale_confirmation(
    context: _ConfirmationContext,
    transcript: Transcript,
    session: ConfirmationSession,
) -> Never:
    context.store.save_transcript(transcript)
    write_model_atomic(
        context.store.paths(context.run_id).confirmation_session,
        session.model_copy(
            update={"status": ConfirmationSessionStatus.COMPLETED}
        ),
    )
    raise ConfirmationError(
        ConfirmationErrorCode.STALE_CONFIRMATION,
        "confirmation source or evidence scope changed after consent",
    )


def _provisional_speaker_request(
    item: ConfirmationItem,
    cue: Cue,
    *,
    state: RunState,
) -> ConfirmationItem:
    provisional_id = item.provisional_id
    if provisional_id is None or cue.cue_id is None:
        raise ConfirmationError(
            ConfirmationErrorCode.STALE_CONFIRMATION,
            "provisional insertion identity is missing",
        )
    identity = hashlib.sha256(
        f"{provisional_id}\0speaker".encode()
    ).hexdigest()[:32]
    context = state.context
    names = (
        tuple(person.display_name for person in context.roster)
        if context is not None and context.roster
        else (() if context is None else context.characters)
    )
    candidates = (*names, *(("알 수 없음",) if "알 수 없음" not in names else ()))
    return ConfirmationItem(
        confirmation_id=ConfirmationId(f"confirmation-{identity}"),
        cue_id=cue.cue_id,
        cue_sequence=cue.sequence,
        field=ConfirmationField.SPEAKER,
        depends_on_confirmation_id=item.confirmation_id,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        current_value=None,
        cue_speech=cue.speech,
        candidates=candidates,
        evidence=(
            "accepted provisional visual insertion lacks speaker authority",
            provisional_id,
            *item.evidence,
        ),
    )


def _apply_confirmation_answer_unlocked(
    store: RunStore,
    run_id: RunId,
    confirmation_id: ConfirmationId,
    answer: str,
) -> ProcessResult:
    """Apply one answer while the caller owns the run lock."""
    path = store.paths(run_id).confirmations
    history = load_confirmation_history(path)
    session = _load_session(store, run_id)
    if (
        session.status is ConfirmationSessionStatus.COMPLETED
        and confirmation_id in history.answered_ids
    ):
        raise ConfirmationError(
            ConfirmationErrorCode.DUPLICATE_ANSWER,
            "confirmation item has already been answered",
        )
    _require_active_session(store, run_id)
    dangling_answers = history.answered_ids - history.applied_ids
    if dangling_answers:
        raise ConfirmationError(
            ConfirmationErrorCode.DUPLICATE_ANSWER,
            "current confirmation already has an answer",
        )
    normalized_answer = answer.strip()
    if not normalized_answer:
        raise ConfirmationError(
            ConfirmationErrorCode.EMPTY_ANSWER,
            "confirmation answer must not be empty",
        )
    item = history.next_item()
    if item is None:
        code = (
            ConfirmationErrorCode.DUPLICATE_ANSWER
            if history.applied_ids
            else ConfirmationErrorCode.NO_PENDING_ITEM
        )
        raise ConfirmationError(code, "no unanswered confirmation remains")
    if item.confirmation_id != confirmation_id:
        raise ConfirmationError(
            ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
            "answer does not reference the current confirmation",
        )
    if item.kind is not ConfirmationItemKind.FIELD or item.field is None:
        raise ConfirmationError(
            ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
            "visual insertion requires an explicit typed action",
        )

    transcript = store.load_transcript(run_id)
    _require_current_confirmation_scope(
        _ConfirmationContext(store, run_id),
        transcript,
        session,
        _working_digest(transcript),
        _confirmation_scope_digest(store, run_id),
    )
    state = store.load_state(run_id)
    pending_after = tuple(
        request
        for request in history.requests
        if request.confirmation_id not in history.resolved_ids
        and request.confirmation_id != item.confirmation_id
    )
    if item.field is ConfirmationField.SPEECH and normalized_answer == "없음":
        answered_cues = tuple(
            cue for cue in transcript.cues if not _matches_item(cue, item)
        )
    else:
        answered_cues = tuple(
            _apply_item(cue, item, normalized_answer, pending_after)
            if _matches_item(cue, item)
            else cue
            for cue in transcript.cues
        )
    updated_cues = tuple(
        _renumber_cue(cue, sequence)
        for sequence, cue in enumerate(answered_cues, start=1)
    )
    workflow_status = (
        WorkflowStatus.NEEDS_CONFIRMATION
        if pending_after
        else WorkflowStatus.READY_FOR_VALIDATION
    )
    updated_transcript = Transcript(
        run_id=transcript.run_id,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        duration_ms=transcript.duration_ms,
        cues=updated_cues,
        vision_evidence=transcript.vision_evidence,
    )
    updated_state = RunState(
        run_id=state.run_id,
        source_url=state.source_url,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
        context=state.context,
    )
    store.save_transcript(updated_transcript)
    store.save_state(updated_state)
    next_ordinal = len(history.records) + 1
    append_confirmation_records(
        path,
        (
            ConfirmationAnswerRecord(
                ordinal=next_ordinal,
                confirmation_id=item.confirmation_id,
                answer=normalized_answer,
            ),
            ConfirmationAppliedRecord(
                ordinal=next_ordinal + 1,
                confirmation_id=item.confirmation_id,
                applied_value=normalized_answer,
                workflow_status=workflow_status,
            ),
        ),
    )
    session = _load_session(store, run_id)
    write_model_atomic(
        store.paths(run_id).confirmation_session,
        session.model_copy(
            update={
                "status": (
                    ConfirmationSessionStatus.COMPLETED
                    if not pending_after
                    else session.status
                ),
                "source_digest": _working_digest(updated_transcript),
                "scope_digest": _confirmation_scope_digest(store, run_id),
            }
        ),
    )
    return ProcessResult(
        run_id=run_id,
        workflow_status=workflow_status,
        validation_status=ValidationStatus.PENDING,
    )


def apply_next_confirmation_answer(
    store: RunStore,
    run_id: RunId,
    answer: str,
) -> ProcessResult:
    """Apply the active session's next item for legacy Python callers."""
    history = load_confirmation_history(store.paths(run_id).confirmations)
    if history.next_item() is None and history.answered_ids:
        raise ConfirmationError(
            ConfirmationErrorCode.DUPLICATE_ANSWER,
            "confirmation item has already been answered",
        )
    item = get_next_confirmation(store, run_id)
    return apply_confirmation_answer(
        store,
        run_id,
        item.confirmation_id,
        answer,
    )


def _load_session(store: RunStore, run_id: RunId) -> ConfirmationSession:
    path = store.paths(run_id).confirmation_session
    if not path.exists():
        raise ConfirmationError(
            ConfirmationErrorCode.CONSENT_REQUIRED,
            "confirmation consent is required",
        )
    return ConfirmationSession.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _require_active_session(store: RunStore, run_id: RunId) -> None:
    session = _load_session(store, run_id)
    if session.status is ConfirmationSessionStatus.AWAITING_CONSENT:
        raise ConfirmationError(
            ConfirmationErrorCode.CONSENT_REQUIRED,
            "confirmation consent is required",
        )
    if session.status is not ConfirmationSessionStatus.ACTIVE:
        raise ConfirmationError(
            ConfirmationErrorCode.SESSION_ENDED,
            "confirmation session has ended",
        )


def _set_workflow(
    store: RunStore,
    run_id: RunId,
    workflow_status: WorkflowStatus,
) -> None:
    transcript = store.load_transcript(run_id)
    state = store.load_state(run_id)
    store.save_transcript(
        transcript.model_copy(update={"workflow_status": workflow_status})
    )
    store.save_state(
        state.model_copy(
            update={
                "workflow_status": workflow_status,
                "validation_status": ValidationStatus.PENDING,
            }
        )
    )


def _field_value(cue: Cue, field: ConfirmationField) -> str | None:
    """Return one supported field without widening to arbitrary attributes."""
    match field:
        case ConfirmationField.SPEECH:
            return cue.speech
        case ConfirmationField.SPEAKER:
            return cue.speaker
        case ConfirmationField.SCREEN_TEXT:
            return cue.screen_text


def _confirmation_candidate(value: str | None) -> str:
    """Represent an absent optional field in the existing answer protocol."""
    return "없음" if value is None else value


def _apply_item(
    cue: Cue,
    item: ConfirmationItem,
    answer: str,
    pending_after: tuple[ConfirmationItem, ...],
) -> Cue:
    """Return one cue with the answered field applied."""
    speech = cue.speech
    speaker = cue.speaker
    screen_text = cue.screen_text
    match item.field:
        case ConfirmationField.SPEECH:
            speech = answer
        case ConfirmationField.SPEAKER:
            speaker = None if answer == "없음" else answer
        case ConfirmationField.SCREEN_TEXT:
            screen_text = None if answer == "없음" else answer
        case None:
            raise ConfirmationError(
                ConfirmationErrorCode.INVALID_CONFIRMATION_ID,
                "field confirmation is missing its field",
            )
    has_pending_field = any(_matches_item(cue, pending) for pending in pending_after)
    return Cue(
        sequence=cue.sequence,
        cue_id=cue.cue_id,
        start_tc_ms=cue.start_tc_ms,
        end_tc_ms=cue.end_tc_ms,
        speech=speech,
        speaker=speaker,
        uncertainty=(
            Uncertainty.NEEDS_CONFIRMATION
            if has_pending_field
            else Uncertainty.CERTAIN
        ),
        screen_text=screen_text,
        vision_evidence_ids=cue.vision_evidence_ids,
    )


def _matches_item(cue: Cue, item: ConfirmationItem) -> bool:
    """Match confirmation targets by stable timing after cue renumbering."""
    if item.cue_id is not None:
        return cue.cue_id == item.cue_id
    return (
        cue.start_tc_ms == item.start_tc_ms
        and cue.end_tc_ms == item.end_tc_ms
    )


def _renumber_cue(cue: Cue, sequence: int) -> Cue:
    """Return a cue with its current content and contiguous sequence."""
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


def _working_digest(transcript: Transcript) -> str:
    """Digest semantic source bytes while ignoring lifecycle fields."""
    semantic = transcript.model_copy(
        update={
            "workflow_status": WorkflowStatus.PROCESSING,
            "validation_status": ValidationStatus.PENDING,
        }
    )
    return hashlib.sha256(semantic.model_dump_json().encode()).hexdigest()


def _confirmation_scope_digest(store: RunStore, run_id: RunId) -> str:
    """Digest raw visual and provisional authority bound at consent."""
    paths = store.paths(run_id)
    scoped = (
        ("window_visual_review", paths.window_visual_review),
        ("window_drafts", paths.window_drafts),
        ("whole_transcript", paths.whole_transcript_artifact),
    )
    digest = hashlib.sha256()
    for label, path in scoped:
        digest.update(label.encode())
        digest.update(b"\0")
        if path.exists():
            digest.update(b"present\0")
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing\0")
    return digest.hexdigest()
