# Copyright 2026 Transcription Module contributors

"""Cross-record validation for cue-independent visual review."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module.visual_review import (
    CandidateDispositionKind,
    RawVisualRole,
    VisualFrameOwnership,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
)

if TYPE_CHECKING:
    from transcription_module._drafting_models import WindowDraft, WindowDraftInput
    from transcription_module.models import FrameId
    from transcription_module.visual_review import (
        RawCandidateDisposition,
        RawVisualCandidate,
    )
    from transcription_module.window_evidence_models import (
        ProvisionalVisualInsertion,
        WindowVerifiedTurn,
    )


def validate_visual_review_result(
    assignment: WindowVisualReviewAssignment,
    result: WindowVisualReviewResult,
) -> None:
    """Require exact frame coverage and bounded candidate references."""
    if (
        result.window_index != assignment.window_index
        or result.reviewer_input_digest != assignment.reviewer_input_digest
    ):
        message = "visual review result does not match its assignment"
        raise ValueError(message)
    assigned_ids = tuple(frame.frame_id for frame in assignment.frames)
    observed_ids = tuple(item.frame_id for item in result.frame_observations)
    if observed_ids != assigned_ids:
        message = "visual review result must observe every assigned frame in order"
        raise ValueError(message)
    positions = {frame_id: index for index, frame_id in enumerate(assigned_ids)}
    spans = {span.context_span_id: span for span in result.context_spans}
    if len(spans) != len(result.context_spans):
        message = "visual review context span IDs must be unique"
        raise ValueError(message)
    candidate_ids = tuple(candidate.candidate_id for candidate in result.candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        message = "raw visual candidate IDs must be unique"
        raise ValueError(message)
    for span in result.context_spans:
        _require_ordered_frames(span.supporting_frame_ids, positions)
    ownership = {frame.frame_id: frame.ownership for frame in assignment.frames}
    owned_frame_ids = (
        None if assignment.owned_frame_ids is None else set(assignment.owned_frame_ids)
    )
    for candidate in result.candidates:
        _require_ordered_frames(candidate.supporting_frame_ids, positions)
        if ownership[
            candidate.supporting_frame_ids[0]
        ] is not VisualFrameOwnership.CORE or (
            owned_frame_ids is not None
            and candidate.supporting_frame_ids[0] not in owned_frame_ids
        ):
            message = "raw visual candidate must begin in its owning core window"
            raise ValueError(message)
        if any(span_id not in spans for span_id in candidate.context_span_ids):
            message = "raw visual candidate references an unknown context span"
            raise ValueError(message)


def validate_visual_review_artifact(
    artifact: WindowVisualReviewArtifact,
) -> None:
    """Require one ordered assignment/result pair for every window."""
    assignment_indexes = tuple(
        assignment.window_index for assignment in artifact.assignments
    )
    result_indexes = tuple(result.window_index for result in artifact.results)
    expected = tuple(range(len(artifact.assignments)))
    if assignment_indexes != expected or result_indexes != expected:
        message = "visual review artifact windows must be complete and ordered"
        raise ValueError(message)
    if len(artifact.assignments) != len(artifact.results):
        message = "visual review artifact requires one result per assignment"
        raise ValueError(message)
    for assignment, result in zip(
        artifact.assignments,
        artifact.results,
        strict=True,
    ):
        if assignment.run_id != artifact.run_id:
            message = "visual review assignment belongs to another run"
            raise ValueError(message)
        validate_visual_review_result(assignment, result)


def validate_candidate_dispositions(
    result: WindowVisualReviewResult,
    draft: WindowDraft,
) -> None:
    """Require one bounded downstream disposition per raw candidate."""
    candidate_ids = tuple(candidate.candidate_id for candidate in result.candidates)
    disposition_ids = tuple(
        disposition.candidate_id for disposition in draft.candidate_dispositions
    )
    if disposition_ids != candidate_ids:
        message = "raw visual candidate dispositions must be complete and ordered"
        raise ValueError(message)
    candidates = {candidate.candidate_id: candidate for candidate in result.candidates}
    for disposition in draft.candidate_dispositions:
        _validate_candidate_disposition(
            candidates[disposition.candidate_id],
            disposition,
            len(draft.cues),
        )


def validate_provisional_visual_insertions(
    window_input: WindowDraftInput,
    draft: WindowDraft,
) -> None:
    """Require exact raw, observation, VAD, and turn authority bindings."""
    if window_input.visual_review is None:
        if draft.provisional_visual_insertions:
            message = "provisional visual insertion lacks raw visual review"
            raise ValueError(message)
        return
    expected_ids = tuple(
        item.candidate_id
        for item in draft.candidate_dispositions
        if item.kind is CandidateDispositionKind.PROVISIONAL_INSERTION
    )
    actual_ids = tuple(
        item.candidate_id for item in draft.provisional_visual_insertions
    )
    if actual_ids != expected_ids:
        message = "provisional visual insertions must match their dispositions"
        raise ValueError(message)
    for provisional in draft.provisional_visual_insertions:
        _validate_provisional_visual_insertion(window_input, draft, provisional)


def _validate_provisional_visual_insertion(
    window_input: WindowDraftInput,
    draft: WindowDraft,
    provisional: ProvisionalVisualInsertion,
) -> None:
    visual_review = window_input.visual_review
    if visual_review is None:
        message = "provisional visual insertion lacks raw visual review"
        raise ValueError(message)
    candidate = next(
        (
            item
            for item in visual_review.candidates
            if item.candidate_id == provisional.candidate_id
        ),
        None,
    )
    observation = next(
        (
            item
            for item in draft.observations
            if item.observation_id == provisional.observation_id
        ),
        None,
    )
    turn = next(
        (
            item
            for item in window_input.verified_turns
            if item.turn_id == provisional.turn_id
        ),
        None,
    )
    if candidate is None or observation is None:
        message = "provisional visual insertion authority is missing"
        raise ValueError(message)
    _validate_provisional_audio_bounds(window_input, provisional, turn)
    if (
        tuple(candidate.supporting_frame_ids) != tuple(observation.frame_ids)
        or provisional.speech != " ".join(candidate.complete_text.split())
        or observation.turn_ids
        != (() if provisional.turn_id is None else (provisional.turn_id,))
        or provisional.vad_interval_ids != observation.vad_interval_ids
    ):
        message = "provisional visual insertion differs from raw evidence"
        raise ValueError(message)
    disposition = next(
        item
        for item in draft.candidate_dispositions
        if item.candidate_id == provisional.candidate_id
    )
    if provisional.disposition_evidence_ids != disposition.evidence_ids:
        message = "provisional visual insertion disposition evidence differs"
        raise ValueError(message)


def _validate_provisional_audio_bounds(
    window_input: WindowDraftInput,
    provisional: ProvisionalVisualInsertion,
    turn: WindowVerifiedTurn | None,
) -> None:
    if provisional.turn_id is not None:
        if turn is None:
            message = "provisional visual insertion verified turn is missing"
            raise ValueError(message)
        if (
            provisional.start_tc_ms != turn.start_tc_ms
            or provisional.end_tc_ms != turn.end_tc_ms
        ):
            message = "provisional visual insertion must use exact verified turn bounds"
            raise ValueError(message)
        if provisional.speaker != turn.speaker:
            message = "provisional visual insertion speaker differs from verified turn"
            raise ValueError(message)
        return
    intervals = tuple(
        item
        for item in window_input.vad_intervals
        if item.interval_id in provisional.vad_interval_ids
    )
    if (
        len(intervals) != 1
        or provisional.speaker is not None
        or provisional.start_tc_ms != intervals[0].start_tc_ms
        or provisional.end_tc_ms != intervals[0].end_tc_ms
    ):
        message = "speakerless provisional insertion requires exact VAD bounds"
        raise ValueError(message)


def _validate_candidate_disposition(
    candidate: RawVisualCandidate,
    disposition: RawCandidateDisposition,
    cue_count: int,
) -> None:
    match disposition.kind:
        case CandidateDispositionKind.EXISTING_CUE:
            if candidate.proposed_role is not RawVisualRole.SPEECH_CAPTION:
                message = "only speech captions may change an existing cue"
                raise ValueError(message)
            if (
                disposition.target_cue_index is None
                or disposition.target_cue_index >= cue_count
            ):
                message = "existing-cue disposition requires a valid cue target"
                raise ValueError(message)
            if not disposition.evidence_ids:
                message = "existing-cue disposition requires evidence lineage"
                raise ValueError(message)
        case (
            CandidateDispositionKind.AUTHORIZED_INSERTION
            | CandidateDispositionKind.PROVISIONAL_INSERTION
        ):
            if candidate.proposed_role is not RawVisualRole.SPEECH_CAPTION:
                message = "only speech captions may become inserted cues"
                raise ValueError(message)
            if not disposition.evidence_ids:
                message = "insertion disposition requires evidence lineage"
                raise ValueError(message)
        case CandidateDispositionKind.RETAINED_EVIDENCE:
            if disposition.target_cue_index is not None:
                message = "retained visual evidence cannot target a cue"
                raise ValueError(message)


def _require_ordered_frames(
    frame_ids: tuple[FrameId, ...],
    positions: dict[FrameId, int],
) -> None:
    if len(frame_ids) != len(set(frame_ids)):
        message = "visual review frame references must be unique"
        raise ValueError(message)
    try:
        observed = tuple(positions[frame_id] for frame_id in frame_ids)
    except KeyError as error:
        message = "visual review references a foreign frame"
        raise ValueError(message) from error
    if observed != tuple(sorted(observed)):
        message = "visual review frame references must be chronological"
        raise ValueError(message)


__all__ = [
    "validate_candidate_dispositions",
    "validate_provisional_visual_insertions",
    "validate_visual_review_artifact",
    "validate_visual_review_result",
]
