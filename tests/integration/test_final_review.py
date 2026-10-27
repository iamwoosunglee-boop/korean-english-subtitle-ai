# Copyright 2026 Transcription Module contributors

"""Integration contract for exact, immutable final-review publication."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread
from typing import TYPE_CHECKING, Literal

import pytest

from transcription_module.confirmation_models import (
    ConfirmationAnswerRecord,
    ConfirmationAppliedRecord,
    ConfirmationField,
    ConfirmationId,
    ConfirmationItem,
    ConfirmationRequestRecord,
)
from transcription_module.final_review import (
    FinalReviewError,
    apply_final_review,
    assert_current_final_review,
    prepare_final_review,
)
from transcription_module.final_review_models import (
    CueReplacement,
    CueRevision,
    FinalReviewAssignment,
    FinalReviewReceipt,
    FinalReviewResult,
    FrameReviewObservation,
    ReviewSegmentResult,
)
from transcription_module.media import PreparedFrameManifest, PreparedMediaManifest
from transcription_module.models import (
    Cue,
    CueId,
    EvidenceId,
    FrameId,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    VisionEvidence,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import (
    RunStore,
    write_model_atomic,
)

if TYPE_CHECKING:
    from pathlib import Path


_DEFAULT_DURATION_MS = 2_000
_DEFAULT_DURATION = Milliseconds(_DEFAULT_DURATION_MS)
_EXPECTED_CONFIRMATION_REQUESTS = 2


def _cue(  # noqa: PLR0913
    sequence: int,
    cue_id: str,
    start_tc_ms: int,
    end_tc_ms: int,
    *,
    speech: str | None = None,
    evidence_ids: tuple[EvidenceId, ...] = (),
) -> Cue:
    return Cue(
        sequence=sequence,
        cue_id=CueId(cue_id),
        start_tc_ms=Milliseconds(start_tc_ms),
        end_tc_ms=Milliseconds(end_tc_ms),
        speech=speech or f"speech {cue_id}",
        speaker="speaker",
        uncertainty=Uncertainty.CERTAIN,
        screen_text=f"screen {cue_id}",
        vision_evidence_ids=evidence_ids,
    )


def _create_run(
    tmp_path: Path,
    *,
    duration_ms: Milliseconds = _DEFAULT_DURATION,
    cue_count: int = 5,
) -> tuple[RunStore, RunId]:
    run_id = RunId("review-run")
    store = RunStore(tmp_path / "runs")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtube.test/source"),
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
        )
    )
    evidence_id = EvidenceId("evidence-1")
    spacing = max(1, duration_ms // (cue_count + 1))
    cues = tuple(
        _cue(
            sequence,
            f"cue-{sequence}",
            min(duration_ms - 2, sequence * spacing),
            min(duration_ms - 1, sequence * spacing + max(1, spacing // 2)),
            evidence_ids=(evidence_id,) if sequence == 1 else (),
        )
        for sequence in range(1, cue_count + 1)
    )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            validation_status=ValidationStatus.PENDING,
            duration_ms=duration_ms,
            cues=cues,
            vision_evidence=(
                VisionEvidence(
                    evidence_id=evidence_id,
                    frame_ids=(FrameId("frame-000"),),
                    scene="scene",
                ),
            ),
        )
    )

    frame_pts = (
        (0, 83, 1_333)
        if duration_ms == _DEFAULT_DURATION_MS
        else (0, 2_000, 300_000)
    )
    frames: list[PreparedFrameManifest] = []
    for index, pts_ms in enumerate(frame_pts):
        frame_id = FrameId(f"frame-{index:03d}")
        relative_path = f"frames/{frame_id}.png"
        frame_path = paths.run_dir / relative_path
        _ = frame_path.parent.mkdir(parents=True, exist_ok=True)
        _ = frame_path.write_bytes(f"frame bytes {index}".encode())
        frames.append(
            PreparedFrameManifest(
                frame_id=frame_id,
                frame_index=index,
                raw_pts=index * 1001,
                raw_pts_time=f"{pts_ms / 1000:.6f}",
                best_effort_timestamp=index * 1001,
                best_effort_timestamp_time=f"{pts_ms / 1000:.6f}",
                pts_ms=pts_ms,
                path=relative_path,
            )
        )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtube.test/source"),
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            video_path="video.mp4",
            audio_path="audio.wav",
            duration_ms=duration_ms,
            frame_count=len(frames),
            frames=tuple(frames),
        ),
    )
    return store, run_id


def _result(
    assignment: FinalReviewAssignment,
    *,
    replacements: tuple[CueReplacement, ...] = (),
) -> FinalReviewResult:
    return FinalReviewResult(
        run_id=assignment.run_id,
        assignment_digest=assignment.assignment_digest,
        segments=tuple(
            ReviewSegmentResult(
                segment_id=segment.segment_id,
                frame_ids=segment.frame_ids,
                cue_ids=segment.cue_ids,
                frame_observations=tuple(
                    FrameReviewObservation(
                        frame_id=frame_id,
                        visible_screen_text=(),
                        compared_cue_ids=segment.cue_ids,
                    )
                    for frame_id in segment.frame_ids
                ),
                replacements=replacements if index == 0 else (),
            )
            for index, segment in enumerate(assignment.segments)
        ),
    )


def _revision(
    cue_id: str,
    start_tc_ms: int,
    end_tc_ms: int,
    *,
    speech: str,
) -> CueRevision:
    return CueRevision(
        cue_id=CueId(cue_id),
        start_tc_ms=Milliseconds(start_tc_ms),
        end_tc_ms=Milliseconds(end_tc_ms),
        speech=speech,
        speaker="reviewer",
        uncertainty=Uncertainty.CERTAIN,
        screen_text=None,
    )


def _assert_no_current_receipt(store: RunStore, run_id: RunId) -> None:
    with pytest.raises((FinalReviewError, FileNotFoundError)):
        _ = assert_current_final_review(store, run_id)


def test_prepare_is_deterministic_and_clean_review_publishes_receipt(
    tmp_path: Path,
) -> None:
    """Assign exact five-minute evidence and publish JSON/CSV/receipt together."""
    store, run_id = _create_run(
        tmp_path,
        duration_ms=Milliseconds(301_000),
        cue_count=2,
    )

    first = prepare_final_review(store, run_id)
    second = prepare_final_review(store, run_id)

    assert first == second
    assert tuple(
        (segment.segment_id, segment.start_tc_ms, segment.end_tc_ms)
        for segment in first.segments
    ) == (
        ("segment-0001", 0, 300_000),
        ("segment-0002", 300_000, 301_000),
    )
    assert (
        tuple(frame_id for segment in first.segments for frame_id in segment.frame_ids)
        == first.frame_ids
    )
    assert (
        tuple(cue_id for segment in first.segments for cue_id in segment.cue_ids)
        == first.cue_ids
    )

    receipt = apply_final_review(store, run_id, _result(first))
    transcript = store.load_transcript(run_id)

    assert receipt == assert_current_final_review(store, run_id)
    assert receipt.result_cue_ids == tuple(cue.cue_id for cue in transcript.cues)


def test_apply_rejects_id_only_review_that_bypasses_model_validation(
    tmp_path: Path,
) -> None:
    """Application rechecks semantic observations on tampered model instances."""
    store, run_id = _create_run(tmp_path)
    assignment = prepare_final_review(store, run_id)
    result = _result(assignment)
    first = result.segments[0].model_copy(update={"frame_observations": ()})
    bypassed = result.model_copy(update={"segments": (first,)})

    with pytest.raises(FinalReviewError, match="frame observations"):
        _ = apply_final_review(store, run_id, bypassed)

    _assert_no_current_receipt(store, run_id)


def test_apply_rejects_legacy_speaker_notation_before_receipt(
    tmp_path: Path,
) -> None:
    """A reviewed transcript cannot retain arrow or middle-dot metadata."""
    store, run_id = _create_run(tmp_path)
    transcript = store.load_transcript(run_id)
    store.save_transcript(
        transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0].model_copy(
                        update={
                            "speaker": "루미 → 한지아",
                            "speech": "좋습니다. 너무 좋아요.",
                        }
                    ),
                    *transcript.cues[1:],
                )
            }
        )
    )
    assignment = prepare_final_review(store, run_id)

    with pytest.raises(FinalReviewError, match="invalid speaker notation"):
        _ = apply_final_review(store, run_id, _result(assignment))

    _assert_no_current_receipt(store, run_id)


def test_grouped_replace_delete_split_and_merge_are_applied_from_one_snapshot(
    tmp_path: Path,
) -> None:
    """Support every grouped correction shape with deterministic final ordering."""
    store, run_id = _create_run(tmp_path)
    assignment = prepare_final_review(store, run_id)
    replacements = (
        CueReplacement(
            target_cue_ids=(CueId("cue-1"),),
            revisions=(_revision("cue-1", 300, 450, speech="replacement"),),
        ),
        CueReplacement(target_cue_ids=(CueId("cue-2"),), revisions=()),
        CueReplacement(
            target_cue_ids=(CueId("cue-3"),),
            revisions=(
                _revision("cue-3", 1_000, 1_100, speech="split a"),
                _revision("cue-6", 1_100, 1_200, speech="split b"),
            ),
        ),
        CueReplacement(
            target_cue_ids=(CueId("cue-4"), CueId("cue-5")),
            revisions=(_revision("cue-4", 1_300, 1_800, speech="merged"),),
        ),
    )

    _ = apply_final_review(
        store,
        run_id,
        _result(assignment, replacements=replacements),
    )
    transcript = store.load_transcript(run_id)

    assert [cue.sequence for cue in transcript.cues] == [1, 2, 3, 4]
    assert [cue.cue_id for cue in transcript.cues] == [
        CueId("cue-1"),
        CueId("cue-3"),
        CueId("cue-6"),
        CueId("cue-4"),
    ]
    assert [cue.speech for cue in transcript.cues] == [
        "replacement",
        "split a",
        "split b",
        "merged",
    ]
    _ = assert_current_final_review(store, run_id)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "swapped"])
@pytest.mark.parametrize("identity", ["frame_ids", "cue_ids"])
def test_apply_rejects_inexact_ordered_coverage(
    tmp_path: Path,
    identity: Literal["frame_ids", "cue_ids"],
    kind: str,
) -> None:
    """Reject omitted, repeated, or reordered frame and cue acknowledgements."""
    store, run_id = _create_run(tmp_path)
    assignment = prepare_final_review(store, run_id)
    result = _result(assignment)
    segment = result.segments[0]
    values = list(
        segment.frame_ids if identity == "frame_ids" else segment.cue_ids
    )
    if kind == "missing":
        _ = values.pop()
    elif kind == "duplicate":
        values[-1] = values[0]
    else:
        values[0], values[1] = values[1], values[0]
    malformed_segment = segment.model_copy(update={identity: tuple(values)})
    malformed = result.model_copy(update={"segments": (malformed_segment,)})

    with pytest.raises(FinalReviewError, match="coverage"):
        _ = apply_final_review(store, run_id, malformed)

    _assert_no_current_receipt(store, run_id)


Mutation = Callable[[RunStore, RunId], None]


def _change_frame_bytes(store: RunStore, run_id: RunId) -> None:
    _ = (store.paths(run_id).run_dir / "frames/frame-000.png").write_bytes(
        b"changed"
    )


def _change_frame_field(store: RunStore, run_id: RunId, field: str) -> None:
    path = store.paths(run_id).media_manifest
    manifest = PreparedMediaManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    frame = manifest.frames[0].model_copy(
        update={field: 1 if field != "path" else "frames/renamed.png"}
    )
    if field == "path":
        _ = (store.paths(run_id).run_dir / "frames/renamed.png").write_bytes(
            b"frame bytes 0"
        )
    write_model_atomic(
        path, manifest.model_copy(update={"frames": (frame, *manifest.frames[1:])})
    )


def _change_count(store: RunStore, run_id: RunId) -> None:
    path = store.paths(run_id).media_manifest
    manifest = PreparedMediaManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    write_model_atomic(path, manifest.model_copy(update={"frame_count": 99}))


def _change_transcript(store: RunStore, run_id: RunId, field: str) -> None:
    path = store.paths(run_id).transcript
    transcript = store.load_transcript(run_id)
    if field == "duration_ms":
        changed = transcript.model_copy(update={"duration_ms": 2_001})
    elif field == "vision_evidence":
        evidence = transcript.vision_evidence[0].model_copy(update={"scene": "changed"})
        changed = transcript.model_copy(update={"vision_evidence": (evidence,)})
    else:
        cue = transcript.cues[0].model_copy(update={"speech": "changed"})
        changed = transcript.model_copy(update={"cues": (cue, *transcript.cues[1:])})
    write_model_atomic(path, changed)


def _change_frame_pts(store: RunStore, run_id: RunId) -> None:
    _change_frame_field(store, run_id, "raw_pts")


def _change_frame_path(store: RunStore, run_id: RunId) -> None:
    _change_frame_field(store, run_id, "path")


def _change_cue_content(store: RunStore, run_id: RunId) -> None:
    _change_transcript(store, run_id, "speech")


def _change_duration(store: RunStore, run_id: RunId) -> None:
    _change_transcript(store, run_id, "duration_ms")


def _change_transcript_evidence(store: RunStore, run_id: RunId) -> None:
    _change_transcript(store, run_id, "vision_evidence")


_STALE_MUTATIONS: tuple[Mutation, ...] = (
    _change_frame_bytes,
    _change_frame_pts,
    _change_frame_path,
    _change_count,
    _change_cue_content,
    _change_duration,
    _change_transcript_evidence,
)


@pytest.mark.parametrize(
    "mutation",
    _STALE_MUTATIONS,
    ids=(
        "frame-bytes",
        "frame-pts",
        "frame-path",
        "declared-count",
        "cue-content",
        "duration",
        "transcript-evidence",
    ),
)
def test_apply_rejects_every_stale_assignment_input(
    tmp_path: Path,
    mutation: Mutation,
) -> None:
    """Bind application to current media bytes, manifest facts, and transcript."""
    store, run_id = _create_run(tmp_path)
    assignment = prepare_final_review(store, run_id)
    mutation(store, run_id)

    with pytest.raises(FinalReviewError, match=r"stale|frame_count|duration"):
        _ = apply_final_review(store, run_id, _result(assignment))

    _assert_no_current_receipt(store, run_id)


@pytest.mark.parametrize("kind", ["removed", "reordered", "tampered"])
def test_apply_rejects_tampered_persisted_assignment_contents(
    tmp_path: Path,
    kind: str,
) -> None:
    """Recompute the persisted assignment digest instead of trusting its field."""
    duration_ms = Milliseconds(
        301_000 if kind == "removed" else _DEFAULT_DURATION_MS
    )
    cue_count = 2 if kind == "removed" else 5
    store, run_id = _create_run(
        tmp_path,
        duration_ms=duration_ms,
        cue_count=cue_count,
    )
    assignment = prepare_final_review(store, run_id)
    if kind == "removed":
        tampered = assignment.model_copy(
            update={
                "frame_ids": assignment.segments[0].frame_ids,
                "segments": assignment.segments[:1],
            }
        )
    elif kind == "reordered":
        reordered_segment = assignment.segments[0].model_copy(
            update={"frame_ids": tuple(reversed(assignment.segments[0].frame_ids))}
        )
        tampered = assignment.model_copy(
            update={
                "frame_ids": tuple(reversed(assignment.frame_ids)),
                "segments": (reordered_segment,),
            }
        )
    else:
        tampered = assignment.model_copy(update={"media_digest": "f" * 64})
    store.save_final_review_assignment(run_id, tampered)

    with pytest.raises(FinalReviewError, match="assignment"):
        _ = apply_final_review(store, run_id, _result(assignment))

    _assert_no_current_receipt(store, run_id)


def test_prepare_rejects_frame_path_escape(tmp_path: Path) -> None:
    """Never digest frame bytes outside the run directory."""
    store, run_id = _create_run(tmp_path)
    manifest_path = store.paths(run_id).media_manifest
    manifest = PreparedMediaManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    escaped = manifest.frames[0].model_copy(update={"path": "../../outside.png"})
    _ = (tmp_path / "outside.png").write_bytes(b"outside")
    write_model_atomic(
        manifest_path,
        manifest.model_copy(update={"frames": (escaped, *manifest.frames[1:])}),
    )

    with pytest.raises(FinalReviewError, match=r"confined|relative"):
        _ = prepare_final_review(store, run_id)


def test_apply_rejects_wrong_run_and_invalid_replacement_graphs(
    tmp_path: Path,
) -> None:
    """Validate run binding, targets, produced identities, and candidate timing."""
    cases: list[tuple[str, Callable[[FinalReviewResult], FinalReviewResult]]] = [
        (
            "run",
            lambda result: result.model_copy(update={"run_id": RunId("other-run")}),
        ),
        (
            "target",
            lambda result: _with_replacements(
                result,
                (CueReplacement(target_cue_ids=(CueId("unknown"),), revisions=()),),
            ),
        ),
        (
            "target",
            lambda result: _with_replacements(
                result,
                (
                    CueReplacement(target_cue_ids=(CueId("cue-1"),), revisions=()),
                    CueReplacement(target_cue_ids=(CueId("cue-1"),), revisions=()),
                ),
            ),
        ),
        (
            "produced",
            lambda result: _with_replacements(
                result,
                (
                    CueReplacement(
                        target_cue_ids=(CueId("cue-1"),),
                        revisions=(_revision("cue-5", 100, 200, speech="collision"),),
                    ),
                ),
            ),
        ),
        (
            "timing",
            lambda result: _with_replacements(
                result,
                (
                    CueReplacement(
                        target_cue_ids=(CueId("cue-1"),),
                        revisions=(
                            _revision("cue-1", 100, 200, speech="bad").model_copy(
                                update={"end_tc_ms": 99}
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ]
    for expected, mutate in cases:
        store, run_id = _create_run(
            tmp_path / expected / str(len(list((tmp_path / expected).glob("*"))))
        )
        assignment = prepare_final_review(store, run_id)
        with pytest.raises(FinalReviewError, match=expected):
            _ = apply_final_review(store, run_id, mutate(_result(assignment)))
        _assert_no_current_receipt(store, run_id)


def test_apply_rejects_replacement_owned_by_another_segment(tmp_path: Path) -> None:
    """A result segment cannot modify a cue assigned to another segment."""
    store, run_id = _create_run(
        tmp_path,
        duration_ms=Milliseconds(600_000),
        cue_count=5,
    )
    assignment = prepare_final_review(store, run_id)
    result = _result(assignment)
    cross_segment = CueReplacement(
        target_cue_ids=(assignment.segments[1].cue_ids[0],),
        revisions=(),
    )
    first = result.segments[0].model_copy(
        update={"replacements": (cross_segment,)}
    )
    bypassed_boundary_validation = result.model_copy(
        update={"segments": (first, *result.segments[1:])}
    )

    with pytest.raises(FinalReviewError, match="segment"):
        _ = apply_final_review(store, run_id, bypassed_boundary_validation)

    _assert_no_current_receipt(store, run_id)


def _with_replacements(
    result: FinalReviewResult,
    replacements: tuple[CueReplacement, ...],
) -> FinalReviewResult:
    first = result.segments[0].model_copy(update={"replacements": replacements})
    return result.model_copy(update={"segments": (first, *result.segments[1:])})


def _record_confirmed_speech(store: RunStore, run_id: RunId) -> None:
    confirmation_id = ConfirmationId("confirmed-speech")
    item = ConfirmationItem(
        confirmation_id=confirmation_id,
        cue_id=CueId("cue-1"),
        cue_sequence=1,
        field=ConfirmationField.SPEECH,
        start_tc_ms=Milliseconds(333),
        end_tc_ms=Milliseconds(499),
        current_value="old",
        cue_speech="speech cue-1",
        candidates=("speech cue-1",),
        evidence=("earlier evidence",),
    )
    records = (
        ConfirmationRequestRecord(ordinal=1, item=item),
        ConfirmationAnswerRecord(
            ordinal=2,
            confirmation_id=confirmation_id,
            answer="speech cue-1",
        ),
        ConfirmationAppliedRecord(
            ordinal=3,
            confirmation_id=confirmation_id,
            applied_value="speech cue-1",
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
        ),
    )
    _ = store.paths(run_id).confirmations.write_text(
        "".join(f"{record.model_dump_json()}\n" for record in records),
        encoding="utf-8",
    )


def test_confirmed_field_conflict_is_enqueued_but_structural_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    """Use ordinary confirmation only for one-to-one supported field conflicts."""
    field_store, run_id = _create_run(tmp_path / "field")
    _record_confirmed_speech(field_store, run_id)
    assignment = prepare_final_review(field_store, run_id)
    replacement = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(_revision("cue-1", 333, 499, speech="contradiction"),),
    )

    with pytest.raises(FinalReviewError, match="confirmation"):
        _ = apply_final_review(
            field_store,
            run_id,
            _result(assignment, replacements=(replacement,)),
        )
    transcript = field_store.load_transcript(run_id)
    assert transcript.cues[0].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert (
        field_store.paths(run_id)
        .confirmations.read_text(encoding="utf-8")
        .count('"kind":"request"')
        == _EXPECTED_CONFIRMATION_REQUESTS
    )
    _assert_no_current_receipt(field_store, run_id)

    structural_store, run_id = _create_run(tmp_path / "structural")
    _record_confirmed_speech(structural_store, run_id)
    assignment = prepare_final_review(structural_store, run_id)
    structural = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(
            _revision("cue-1", 333, 400, speech="a"),
            _revision("new-cue", 400, 499, speech="b"),
        ),
    )

    with pytest.raises(FinalReviewError, match="structural"):
        _ = apply_final_review(
            structural_store,
            run_id,
            _result(assignment, replacements=(structural,)),
        )
    assert (
        structural_store.load_transcript(run_id).cues[0].uncertainty
        is Uncertainty.CERTAIN
    )
    _assert_no_current_receipt(structural_store, run_id)


def test_structural_conflicts_preflight_before_any_field_enqueue(
    tmp_path: Path,
) -> None:
    """A later structural conflict prevents every earlier side effect."""
    store, run_id = _create_run(tmp_path)
    _record_confirmed_speech(store, run_id)
    second_id = ConfirmationId("confirmed-second")
    second = ConfirmationItem(
        confirmation_id=second_id,
        cue_id=CueId("cue-2"),
        cue_sequence=2,
        field=ConfirmationField.SPEECH,
        start_tc_ms=Milliseconds(666),
        end_tc_ms=Milliseconds(832),
        current_value="speech cue-2",
        cue_speech="speech cue-2",
        candidates=("speech cue-2",),
        evidence=("earlier evidence",),
    )
    path = store.paths(run_id).confirmations
    with path.open("a", encoding="utf-8") as output:
        for record in (
            ConfirmationRequestRecord(ordinal=4, item=second),
            ConfirmationAnswerRecord(
                ordinal=5,
                confirmation_id=second_id,
                answer="speech cue-2",
            ),
            ConfirmationAppliedRecord(
                ordinal=6,
                confirmation_id=second_id,
                applied_value="speech cue-2",
                workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            ),
        ):
            _ = output.write(f"{record.model_dump_json()}\n")
    before = path.read_text(encoding="utf-8")
    assignment = prepare_final_review(store, run_id)
    field_conflict = CueReplacement(
        target_cue_ids=(CueId("cue-1"),),
        revisions=(_revision("cue-1", 333, 499, speech="contradiction"),),
    )
    structural_conflict = CueReplacement(
        target_cue_ids=(CueId("cue-2"),),
        revisions=(
            _revision("cue-2", 666, 740, speech="split a"),
            _revision("new-cue", 740, 832, speech="split b"),
        ),
    )

    with pytest.raises(FinalReviewError, match="structural"):
        _ = apply_final_review(
            store,
            run_id,
            _result(
                assignment,
                replacements=(field_conflict, structural_conflict),
            ),
        )

    assert path.read_text(encoding="utf-8") == before
    assert all(
        cue.uncertainty is Uncertainty.CERTAIN
        for cue in store.load_transcript(run_id).cues
    )


def test_current_receipt_requires_preserved_assignment_binding(tmp_path: Path) -> None:
    """Receipt acceptance is bound to the preserved assignment semantics."""
    store, run_id = _create_run(tmp_path)
    assignment = prepare_final_review(store, run_id)
    receipt = apply_final_review(store, run_id, _result(assignment))

    assert store.load_final_review_assignment(run_id) == assignment
    tampered = assignment.model_copy(update={"transcript_digest": "f" * 64})
    store.save_final_review_assignment(run_id, tampered)
    with pytest.raises(FinalReviewError, match="assignment"):
        _ = assert_current_final_review(store, run_id)

    store.save_final_review_assignment(run_id, assignment)
    store.save_final_review_artifact(
        run_id,
        receipt.model_copy(update={"assignment_digest": "f" * 64}),
    )
    with pytest.raises(FinalReviewError, match="assignment"):
        _ = assert_current_final_review(store, run_id)


def test_apply_holds_run_lock_through_receipt_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent mutation cannot acquire the run lock before publication."""
    store, run_id = _create_run(tmp_path)
    assignment = prepare_final_review(store, run_id)
    apply_inside = Event()
    release = Event()
    mutation_entered = Event()
    thread_errors: list[BaseException] = []
    original = RunStore.save_final_review_artifact

    def blocked_save(
        current_store: RunStore,
        current_run_id: RunId,
        artifact: FinalReviewReceipt,
    ) -> None:
        apply_inside.set()
        assert release.wait(timeout=1.0)
        original(current_store, current_run_id, artifact)

    monkeypatch.setattr(RunStore, "save_final_review_artifact", blocked_save)

    def apply() -> None:
        try:
            _ = apply_final_review(store, run_id, _result(assignment))
        except Exception as error:  # noqa: BLE001  # pragma: no cover - diagnostic capture
            thread_errors.append(error)

    def mutate() -> None:
        try:
            with store.lock_run(run_id):
                mutation_entered.set()
        except Exception as error:  # noqa: BLE001  # pragma: no cover - diagnostic capture
            thread_errors.append(error)

    apply_thread = Thread(target=apply)
    mutation_thread = Thread(target=mutate)
    apply_thread.start()
    try:
        assert apply_inside.wait(timeout=1.0)
        mutation_thread.start()
        assert not mutation_entered.wait(timeout=0.1)
        release.set()
        apply_thread.join(timeout=1.0)
        mutation_thread.join(timeout=1.0)
    finally:
        release.set()
        apply_thread.join(timeout=1.0)
        mutation_thread.join(timeout=1.0)

    assert not thread_errors
    assert mutation_entered.is_set()
    _ = assert_current_final_review(store, run_id)


def test_receipt_is_last_and_is_recomputed_from_current_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed receipt write or later byte mutation cannot retain acceptance."""
    store, run_id = _create_run(tmp_path / "write-failure")
    assignment = prepare_final_review(store, run_id)

    def fail_receipt(*_: object) -> None:
        msg = "injected receipt write failure"
        raise OSError(msg)

    monkeypatch.setattr(RunStore, "save_final_review_artifact", fail_receipt)
    with pytest.raises(OSError, match="injected"):
        _ = apply_final_review(store, run_id, _result(assignment))
    _assert_no_current_receipt(store, run_id)

    monkeypatch.undo()
    current_store, current_run_id = _create_run(tmp_path / "current")
    current_assignment = prepare_final_review(current_store, current_run_id)
    _ = apply_final_review(
        current_store,
        current_run_id,
        _result(current_assignment),
    )
    _change_frame_bytes(current_store, current_run_id)
    _assert_no_current_receipt(current_store, current_run_id)
