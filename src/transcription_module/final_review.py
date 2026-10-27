# Copyright 2026 Transcription Module contributors

"""Exact, digest-bound final review of stored frames and transcript cues."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from transcription_module._final_review import (
    FinalReviewError,
    assignment_digest_for,
    build_assignment,
    cue_ids_for,
    load_current_inputs,
    result_digest_for,
)
from transcription_module._final_review_coverage import assert_coverage
from transcription_module._final_review_replacements import (
    apply_replacements,
    validate_replacements,
)
from transcription_module.confirmation import enqueue_review_confirmation_conflicts
from transcription_module.confirmation_log import load_confirmation_history
from transcription_module.confirmation_models import ConfirmationField
from transcription_module.final_review_models import (
    CueReplacement,
    FinalReviewAssignment,
    FinalReviewReceipt,
    FinalReviewResult,
)
from transcription_module.speaker_notation import (
    SpeakerNotationError,
    validate_speaker_notation,
)

if TYPE_CHECKING:
    from transcription_module.models import RunId, Transcript
    from transcription_module.storage import RunStore


def _prepare_final_review_unlocked(
    store: RunStore,
    run_id: RunId,
) -> FinalReviewAssignment:
    """Persist the deterministic complete review assignment for one run."""
    assignment = build_assignment(store, run_id)
    store.paths(run_id).final_review_artifact.unlink(missing_ok=True)
    store.save_final_review_assignment(run_id, assignment)
    return assignment


def _apply_final_review_unlocked(
    store: RunStore,
    run_id: RunId,
    result: FinalReviewResult,
) -> FinalReviewReceipt:
    """Validate complete review coverage, apply corrections, and commit last."""
    paths = store.paths(run_id)
    paths.final_review_artifact.unlink(missing_ok=True)
    assignment = store.load_final_review_assignment(run_id)
    current = build_assignment(store, run_id)
    if assignment.run_id != run_id or result.run_id != run_id:
        msg = "final review run binding is invalid"
        raise FinalReviewError(msg)
    assignment_digest = assignment_digest_for(assignment)
    if assignment.assignment_digest != assignment_digest:
        msg = "final review assignment digest does not match its contents"
        raise FinalReviewError(msg)
    if assignment_digest != current.assignment_digest:
        msg = "final review assignment is stale"
        raise FinalReviewError(msg)
    if result.assignment_digest != assignment_digest:
        msg = "final review assignment digest is stale"
        raise FinalReviewError(msg)
    transcript = store.load_transcript(run_id)
    assert_coverage(assignment, result, transcript)
    replacements = tuple(
        replacement
        for segment in result.segments
        for replacement in segment.replacements
    )
    validate_replacements(transcript, replacements)
    _route_confirmation_conflicts(
        store,
        run_id,
        assignment,
        replacements,
    )
    updated = apply_replacements(transcript, replacements)
    _assert_canonical_speakers(updated)
    store.save_transcript(updated, preserve_final_review_assignment=True)
    store.save_final_review_result(run_id, result)
    _, published, media_digest, transcript_digest = load_current_inputs(
        store,
        run_id,
    )
    receipt = FinalReviewReceipt(
        run_id=run_id,
        assignment_digest=assignment_digest,
        result_digest=result_digest_for(result),
        media_digest=media_digest,
        transcript_digest=transcript_digest,
        result_cue_ids=cue_ids_for(published),
    )
    store.save_final_review_artifact(run_id, receipt)
    return receipt


def _assert_current_final_review_unlocked(
    store: RunStore,
    run_id: RunId,
) -> FinalReviewReceipt:
    """Return the receipt only when every binding matches current bytes."""
    try:
        receipt = store.load_final_review_artifact(run_id)
        assignment = store.load_final_review_assignment(run_id)
        result = store.load_final_review_result(run_id)
    except (FileNotFoundError, ValidationError) as error:
        msg = "a valid final review receipt is required with its assignment"
        raise FinalReviewError(msg) from error
    assignment_digest = assignment_digest_for(assignment)
    if (
        assignment.run_id != run_id
        or assignment.assignment_digest != assignment_digest
        or receipt.assignment_digest != assignment_digest
        or result.run_id != run_id
        or result.assignment_digest != assignment_digest
        or receipt.result_digest != result_digest_for(result)
    ):
        msg = "final review receipt has an invalid assignment binding"
        raise FinalReviewError(msg)
    _, transcript, media_digest, transcript_digest = load_current_inputs(
        store,
        run_id,
    )
    _assert_canonical_speakers(transcript)
    if receipt.run_id != run_id:
        msg = "final review receipt has the wrong run"
        raise FinalReviewError(msg)
    if receipt.media_digest != media_digest:
        msg = "final review receipt has stale media"
        raise FinalReviewError(msg)
    if receipt.transcript_digest != transcript_digest:
        msg = "final review receipt has a stale transcript"
        raise FinalReviewError(msg)
    if receipt.result_cue_ids != cue_ids_for(transcript):
        msg = "final review receipt has stale cue identities"
        raise FinalReviewError(msg)
    return receipt


def _assert_canonical_speakers(transcript: Transcript) -> None:
    for cue in transcript.cues:
        try:
            validate_speaker_notation(
                cue.speaker,
                cue.speech,
                require_speaker=True,
            )
        except SpeakerNotationError as error:
            message = f"cue {cue.sequence} has invalid speaker notation: {error}"
            raise FinalReviewError(message) from error


def prepare_final_review(
    store: RunStore,
    run_id: RunId,
) -> FinalReviewAssignment:
    """Persist the deterministic complete review assignment for one run."""
    with store.lock_run(run_id):
        return _prepare_final_review_unlocked(store, run_id)


def apply_final_review(
    store: RunStore,
    run_id: RunId,
    result: FinalReviewResult,
) -> FinalReviewReceipt:
    """Validate complete review coverage, apply corrections, and commit last."""
    with store.lock_run(run_id):
        return _apply_final_review_unlocked(store, run_id, result)


def assert_current_final_review(
    store: RunStore,
    run_id: RunId,
) -> FinalReviewReceipt:
    """Return the receipt only when every binding matches current bytes."""
    with store.lock_run(run_id):
        return _assert_current_final_review_unlocked(store, run_id)


def _route_confirmation_conflicts(
    store: RunStore,
    run_id: RunId,
    assignment: FinalReviewAssignment,
    replacements: tuple[CueReplacement, ...],
) -> None:
    history = load_confirmation_history(store.paths(run_id).confirmations)
    requests = {item.confirmation_id: item for item in history.requests}
    confirmed_cue_ids = {
        item.cue_id
        for confirmation_id, item in requests.items()
        if confirmation_id in history.applied_ids and item.cue_id is not None
    }
    for replacement in replacements:
        if not confirmed_cue_ids.intersection(replacement.target_cue_ids):
            continue
        if (
            len(replacement.target_cue_ids) != 1
            or len(replacement.revisions) != 1
            or replacement.revisions[0].cue_id != replacement.target_cue_ids[0]
        ):
            msg = "confirmed cue has a structural review conflict"
            raise FinalReviewError(msg)
    for replacement in replacements:
        confirmed_targets = confirmed_cue_ids.intersection(replacement.target_cue_ids)
        if not confirmed_targets:
            continue
        revision = replacement.revisions[0]
        queued = enqueue_review_confirmation_conflicts(
            store,
            run_id,
            cue_id=replacement.target_cue_ids[0],
            proposed_values={
                ConfirmationField.SPEECH: revision.speech,
                ConfirmationField.SPEAKER: revision.speaker,
                ConfirmationField.SCREEN_TEXT: revision.screen_text,
            },
            evidence=(f"final review assignment {assignment.assignment_digest}",),
        )
        if queued:
            msg = "final review requires user confirmation"
            raise FinalReviewError(msg)


__all__ = [
    "FinalReviewError",
    "apply_final_review",
    "assert_current_final_review",
    "prepare_final_review",
]
