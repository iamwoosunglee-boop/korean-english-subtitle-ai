# Copyright 2026 Transcription Module contributors

"""Validate and apply grouped final-review cue replacements."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from transcription_module._final_review import FinalReviewError
from transcription_module.models import (
    Cue,
    CueId,
    Transcript,
    ValidationStatus,
)

if TYPE_CHECKING:
    from transcription_module.final_review_models import CueReplacement, CueRevision


def validate_replacements(
    transcript: Transcript,
    replacements: tuple[CueReplacement, ...],
) -> None:
    """Reject replacement graphs that are ambiguous or touch missing cues."""
    existing = {cast("CueId", cue.cue_id) for cue in transcript.cues}
    targets = tuple(
        cue_id
        for replacement in replacements
        for cue_id in replacement.target_cue_ids
    )
    produced = tuple(
        revision.cue_id
        for replacement in replacements
        for revision in replacement.revisions
    )
    if len(targets) != len(set(targets)):
        msg = "final review replacement targets must be globally unique"
        raise FinalReviewError(msg)
    if any(cue_id not in existing for cue_id in targets):
        msg = "final review replacement target does not exist"
        raise FinalReviewError(msg)
    untouched = existing.difference(targets)
    if any(cue_id in untouched for cue_id in produced):
        msg = "final review replacement produced ID collides with an untouched cue"
        raise FinalReviewError(msg)
    if len(produced) != len(set(produced)):
        msg = "final review replacement produced IDs must be globally unique"
        raise FinalReviewError(msg)
    for replacement in replacements:
        for revision in replacement.revisions:
            if (
                revision.end_tc_ms <= revision.start_tc_ms
                or revision.end_tc_ms > transcript.duration_ms
            ):
                msg = "replacement timing is invalid"
                raise FinalReviewError(msg)


def apply_replacements(
    transcript: Transcript,
    replacements: tuple[CueReplacement, ...],
) -> Transcript:
    """Apply grouped replacements once and return deterministic cue ordering."""
    replacement_by_target: dict[CueId, int] = {}
    for index, replacement in enumerate(replacements):
        for cue_id in replacement.target_cue_ids:
            replacement_by_target[cue_id] = index
    emitted: set[int] = set()
    submitted: list[Cue] = []
    for cue in transcript.cues:
        cue_id = cast("CueId", cue.cue_id)
        replacement_index = replacement_by_target.get(cue_id)
        if replacement_index is None:
            submitted.append(cue)
            continue
        if replacement_index in emitted:
            continue
        emitted.add(replacement_index)
        submitted.extend(
            _revision_as_cue(revision)
            for revision in replacements[replacement_index].revisions
        )
    ordered = sorted(
        enumerate(submitted),
        key=lambda item: (item[1].start_tc_ms, item[1].end_tc_ms, item[0]),
    )
    cues = tuple(
        cue.model_copy(update={"sequence": sequence})
        for sequence, (_, cue) in enumerate(ordered, start=1)
    )
    try:
        return Transcript(
            run_id=transcript.run_id,
            workflow_status=transcript.workflow_status,
            validation_status=ValidationStatus.PENDING,
            duration_ms=transcript.duration_ms,
            cues=cues,
            vision_evidence=transcript.vision_evidence,
        )
    except ValidationError as error:
        msg = "final review replacements produce an invalid transcript"
        raise FinalReviewError(msg) from error


def _revision_as_cue(revision: CueRevision) -> Cue:
    return Cue(
        sequence=1,
        cue_id=revision.cue_id,
        start_tc_ms=revision.start_tc_ms,
        end_tc_ms=revision.end_tc_ms,
        speech=revision.speech,
        speaker=revision.speaker,
        uncertainty=revision.uncertainty,
        screen_text=revision.screen_text,
        vision_evidence_ids=revision.vision_evidence_ids,
    )


__all__ = ["apply_replacements", "validate_replacements"]
