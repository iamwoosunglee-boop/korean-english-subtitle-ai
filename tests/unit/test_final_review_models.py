# Copyright 2026 Transcription Module contributors

"""Final-review boundary model tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from transcription_module.final_review_models import (
    CueReplacement,
    CueRevision,
    FinalReviewAssignment,
    FinalReviewReceipt,
    FinalReviewResult,
    FrameReviewObservation,
    ReviewSegmentAssignment,
    ReviewSegmentResult,
)
from transcription_module.models import (
    CueId,
    EvidenceId,
    FrameId,
    Milliseconds,
    RunId,
    Uncertainty,
)

if TYPE_CHECKING:
    from collections.abc import Callable


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _segments() -> tuple[ReviewSegmentAssignment, ...]:
    return (
        ReviewSegmentAssignment(
            segment_id="segment-1",
            start_tc_ms=Milliseconds(0),
            end_tc_ms=Milliseconds(300_000),
            frame_ids=(FrameId("frame-1"), FrameId("frame-2")),
            cue_ids=(CueId("cue-1"),),
        ),
        ReviewSegmentAssignment(
            segment_id="segment-2",
            start_tc_ms=Milliseconds(300_000),
            end_tc_ms=Milliseconds(600_000),
            frame_ids=(FrameId("frame-3"),),
            cue_ids=(CueId("cue-2"), CueId("cue-3")),
        ),
    )


def _assignment() -> FinalReviewAssignment:
    return FinalReviewAssignment(
        run_id=RunId("run-1"),
        duration_ms=Milliseconds(600_000),
        frame_ids=(FrameId("frame-1"), FrameId("frame-2"), FrameId("frame-3")),
        cue_ids=(CueId("cue-1"), CueId("cue-2"), CueId("cue-3")),
        media_digest=DIGEST_A,
        transcript_digest=DIGEST_B,
        assignment_digest=DIGEST_C,
        segments=_segments(),
    )


def _revision(cue_id: str = "cue-1") -> CueRevision:
    return CueRevision(
        cue_id=CueId(cue_id),
        start_tc_ms=Milliseconds(10),
        end_tc_ms=Milliseconds(90),
        speech="reviewed speech",
        speaker="speaker",
        uncertainty=Uncertainty.CERTAIN,
        screen_text="screen",
        vision_evidence_ids=(EvidenceId("evidence-1"),),
    )


def _observations(
    frame_ids: tuple[FrameId, ...],
    cue_ids: tuple[CueId, ...],
) -> tuple[FrameReviewObservation, ...]:
    return tuple(
        FrameReviewObservation(
            frame_id=frame_id,
            visible_screen_text=(),
            compared_cue_ids=cue_ids,
        )
        for frame_id in frame_ids
    )


def _result(
    replacements: tuple[CueReplacement, ...] = (),
) -> FinalReviewResult:
    return FinalReviewResult(
        run_id=RunId("run-1"),
        assignment_digest=DIGEST_C,
        segments=(
            ReviewSegmentResult(
                segment_id="segment-1",
                frame_ids=(FrameId("frame-1"), FrameId("frame-2")),
                cue_ids=(CueId("cue-1"),),
                frame_observations=_observations(
                    (FrameId("frame-1"), FrameId("frame-2")),
                    (CueId("cue-1"),),
                ),
                replacements=replacements,
            ),
            ReviewSegmentResult(
                segment_id="segment-2",
                frame_ids=(FrameId("frame-3"),),
                cue_ids=(CueId("cue-2"), CueId("cue-3")),
                frame_observations=_observations(
                    (FrameId("frame-3"),),
                    (CueId("cue-2"), CueId("cue-3")),
                ),
            ),
        ),
    )


def test_assignment_and_result_round_trip_deterministically() -> None:
    """A two-segment assignment and its complete result round-trip exactly."""
    assignment = _assignment()
    replacement = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(_revision(),),
    )
    result = _result((replacement,))
    receipt = FinalReviewReceipt(
        run_id=RunId("run-1"),
        assignment_digest=DIGEST_C,
        result_digest=DIGEST_C,
        media_digest=DIGEST_A,
        transcript_digest=DIGEST_B,
        result_cue_ids=(CueId("cue-1"), CueId("cue-2"), CueId("cue-3")),
    )

    assignment_json = assignment.model_dump_json()
    result_json = result.model_dump_json()
    receipt_json = receipt.model_dump_json()

    assert assignment_json == assignment.model_dump_json()
    assert result_json == result.model_dump_json()
    assert FinalReviewAssignment.model_validate_json(assignment_json) == assignment
    assert FinalReviewResult.model_validate_json(result_json) == result
    assert FinalReviewReceipt.model_validate_json(receipt_json) == receipt


def test_grouped_replacement_supports_delete_replace_split_and_merge() -> None:
    """One grouped primitive represents every required replacement shape."""
    replacements = (
        CueReplacement(target_cue_ids=(CueId("cue-1"),), revisions=()),
        CueReplacement(
            target_cue_ids=(CueId("cue-2"),),
            revisions=(_revision("cue-2"),),
        ),
        CueReplacement(
            target_cue_ids=(CueId("cue-3"),),
            revisions=(_revision("cue-3a"), _revision("cue-3b")),
        ),
        CueReplacement(
            target_cue_ids=(CueId("cue-4"), CueId("cue-5")),
            revisions=(_revision("cue-4"),),
        ),
    )

    result = ReviewSegmentResult(
        segment_id="segment-all",
        cue_ids=tuple(CueId(f"cue-{index}") for index in range(1, 6)),
        replacements=replacements,
    )

    assert result.replacements == replacements


@pytest.mark.parametrize(
    "update",
    [
        {"segments": (_segments()[0], _segments()[0])},
        {
            "frame_ids": (
                FrameId("frame-1"),
                FrameId("frame-1"),
                FrameId("frame-3"),
            )
        },
        {"cue_ids": (CueId("cue-1"), CueId("cue-1"), CueId("cue-3"))},
    ],
    ids=("segment", "frame", "cue"),
)
def test_assignment_rejects_duplicate_identifiers(update: dict[str, object]) -> None:
    """Assignment identities are globally unique and therefore unambiguous."""
    values = _assignment().model_dump()
    values.update(update)

    with pytest.raises(ValidationError):
        _ = FinalReviewAssignment.model_validate(values)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ReviewSegmentAssignment(
            segment_id="segment-1",
            start_tc_ms=Milliseconds(10),
            end_tc_ms=Milliseconds(10),
            frame_ids=(),
            cue_ids=(),
        ),
        lambda: CueRevision(
            cue_id=CueId("cue-1"),
            start_tc_ms=Milliseconds(90),
            end_tc_ms=Milliseconds(10),
            speech="speech",
            uncertainty=Uncertainty.CERTAIN,
        ),
    ],
    ids=("segment", "revision"),
)
def test_models_reject_empty_or_reversed_intervals(
    factory: Callable[[], object],
) -> None:
    """Every persisted interval must have positive duration."""
    with pytest.raises(ValidationError):
        _ = factory()


def test_result_rejects_unresolved_revision() -> None:
    """A final review cannot produce a cue that still needs confirmation."""
    values = _revision().model_dump()
    values["uncertainty"] = Uncertainty.NEEDS_CONFIRMATION

    with pytest.raises(ValidationError):
        _ = CueRevision.model_validate(values)


def test_revision_accepts_canonical_sequential_speakers() -> None:
    """Final review maps pipe-separated speakers to speech lines in order."""
    values = _revision().model_dump()
    values.update(
        {
            "speaker": "루미|한지아",
            "speech": "좋습니다.\n너무 좋아, 너무 좋아.",
        }
    )

    revision = CueRevision.model_validate(values)

    assert revision.speaker == "루미|한지아"


@pytest.mark.parametrize("speaker", ["루미 → 한지아", "루미·한지아"])
def test_revision_rejects_noncanonical_speaker_delimiters(speaker: str) -> None:
    """Final-review corrections cannot reintroduce legacy speaker notation."""
    values = _revision().model_dump()
    values["speaker"] = speaker

    with pytest.raises(ValidationError):
        _ = CueRevision.model_validate(values)


def test_revision_rejects_missing_final_speaker() -> None:
    """A certain reviewed cue must identify its speaker."""
    values = _revision().model_dump()
    values["speaker"] = None

    with pytest.raises(ValidationError):
        _ = CueRevision.model_validate(values)


@pytest.mark.parametrize(
    "values",
    [
        {"target_cue_ids": (), "revisions": ()},
        {
            "target_cue_ids": (CueId("cue-1"), CueId("cue-1")),
            "revisions": (),
        },
        {
            "target_cue_ids": (CueId("cue-1"),),
            "revisions": (_revision("cue-new"), _revision("cue-new")),
        },
    ],
    ids=("empty-targets", "duplicate-targets", "duplicate-produced-ids"),
)
def test_replacement_rejects_invalid_shapes(values: dict[str, object]) -> None:
    """A replacement has non-empty unique targets and unique produced IDs."""
    with pytest.raises(ValidationError):
        _ = CueReplacement.model_validate(values)


def test_replacement_validates_mappings_but_preserves_revision_instances() -> None:
    """Parse mapping inputs without revalidating existing revision instances."""
    revision_values = _revision().model_dump()
    parsed = CueReplacement.model_validate(
        {
            "target_cue_ids": (CueId("cue-1"),),
            "revisions": (revision_values,),
        }
    )
    assert isinstance(parsed.revisions[0], CueRevision)

    revision_values["end_tc_ms"] = Milliseconds(5)
    with pytest.raises(ValidationError):
        _ = CueReplacement.model_validate(
            {
                "target_cue_ids": (CueId("cue-1"),),
                "revisions": (revision_values,),
            }
        )

    tampered = _revision().model_copy(update={"end_tc_ms": Milliseconds(5)})
    preserved = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(tampered,),
    )
    assert preserved.revisions[0] is tampered


def test_segment_result_rejects_cross_segment_replacement_targets() -> None:
    """A segment can modify only the cues assigned to that segment."""
    replacement = CueReplacement(
        target_cue_ids=(CueId("cue-2"),),
        revisions=(_revision("cue-2"),),
    )

    with pytest.raises(ValidationError, match="belong to review segment"):
        _ = ReviewSegmentResult(
            segment_id="segment-1",
            cue_ids=(CueId("cue-1"),),
            replacements=(replacement,),
        )


def test_segment_result_rejects_id_only_review_without_frame_observations() -> None:
    """Frame IDs alone cannot prove that visible text was semantically reviewed."""
    with pytest.raises(ValidationError, match="frame observations"):
        _ = ReviewSegmentResult(
            segment_id="segment-1",
            frame_ids=(FrameId("frame-1"), FrameId("frame-2")),
            cue_ids=(CueId("cue-1"),),
        )


@pytest.mark.parametrize(
    ("observations", "message"),
    [
        (
            (
                FrameReviewObservation(
                    frame_id=FrameId("frame-1"),
                    visible_screen_text=(),
                    compared_cue_ids=(CueId("cue-1"),),
                ),
            ),
            "exactly match",
        ),
        (
            _observations(
                (FrameId("frame-2"), FrameId("frame-1")),
                (CueId("cue-1"),),
            ),
            "exactly match",
        ),
        (
            _observations(
                (FrameId("frame-1"), FrameId("frame-1")),
                (CueId("cue-1"),),
            ),
            "exactly match",
        ),
        (
            _observations(
                (FrameId("frame-1"), FrameId("frame-2")),
                (CueId("cue-foreign"),),
            ),
            "belong to review segment",
        ),
    ],
    ids=("missing", "reordered", "duplicate", "foreign-cue"),
)
def test_segment_result_rejects_inexact_frame_observations(
    observations: tuple[FrameReviewObservation, ...],
    message: str,
) -> None:
    """Semantic observations must exactly cover frames and segment-owned cues."""
    with pytest.raises(ValidationError, match=message):
        _ = ReviewSegmentResult(
            segment_id="segment-1",
            frame_ids=(FrameId("frame-1"), FrameId("frame-2")),
            cue_ids=(CueId("cue-1"),),
            frame_observations=observations,
        )


def test_result_rejects_target_reuse_across_segments() -> None:
    """A result cannot replace one original cue more than once."""
    replacement = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(_revision(),),
    )
    values = _result((replacement,)).model_dump()
    values["segments"][1]["replacements"] = (replacement,)

    with pytest.raises(ValidationError):
        _ = FinalReviewResult.model_validate(values)


def test_result_rejects_produced_id_reuse_across_segments() -> None:
    """Every cue ID produced by the complete result is unique."""
    first = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(_revision("cue-new"),),
    )
    second = CueReplacement(
        target_cue_ids=(CueId("cue-2"),),
        revisions=(_revision("cue-new"),),
    )
    values = _result((first,)).model_dump()
    values["segments"][1]["replacements"] = (second,)

    with pytest.raises(ValidationError):
        _ = FinalReviewResult.model_validate(values)


@pytest.mark.parametrize("digest", ["", "A" * 64, "a" * 63, "g" * 64])
def test_models_reject_malformed_sha256(digest: str) -> None:
    """Every freshness binding uses canonical lowercase SHA-256 text."""
    values = _assignment().model_dump()
    values["assignment_digest"] = digest

    with pytest.raises(ValidationError):
        _ = FinalReviewAssignment.model_validate(values)


def test_models_reject_empty_stable_ids() -> None:
    """Boundary identity fields cannot contain empty sentinel values."""
    assignment_values = _assignment().model_dump()
    assignment_values["run_id"] = ""

    with pytest.raises(ValidationError):
        _ = FinalReviewAssignment.model_validate(assignment_values)
    with pytest.raises(ValidationError):
        _ = ReviewSegmentResult(segment_id="")
    with pytest.raises(ValidationError):
        _ = ReviewSegmentResult(segment_id="segment-1", frame_ids=(FrameId(""),))
    with pytest.raises(ValidationError):
        _ = _revision("")
