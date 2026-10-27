# Copyright 2026 Transcription Module contributors

"""Stored-evidence preparation and validation for final-review execution."""

from __future__ import annotations

import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from transcription_module.asr import AsrRunArtifact
from transcription_module.final_review_execution_models import (
    FinalReviewExecutionError,
    FinalReviewSegmentInput,
    KoreanVttEvidence,
)
from transcription_module.final_review_models import (
    FinalReviewAssignment,
    ReviewSegmentResult,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.subtitle_admission import (
    SubtitleAdmissionError,
    admitted_subtitle_tracks,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from transcription_module.models import Cue, RunId
    from transcription_module.storage import RunStore

_PURPOSE = (
    "저장 프레임의 실제 화면 문구를 현재 cue와 비교해 누락과 오기를 바로잡는다."
)
_FAILURE_CONTEXT = (
    "이전 최종 검토는 화면 문구 누락을 찾지 못하고 ID 반환을 완전한 검토로 오인했다."
)
_FORBIDDEN_PROXY_METRICS = ("id-list", "contact-sheet", "self-report")
_REQUIRED_EVIDENCE_ACTIONS = (
    "각 저장 프레임을 읽을 수 있는 해상도로 직접 확인하고 모든 완전한 화면 문구를 "
    "같은 segment의 현재 cue screen_text와 비교한다."
)
_STOP_CONDITION = (
    "모든 assigned frame의 ordered semantic observation과 필요한 complete correction이 "
    "검증을 통과한다."
)


def build_segment_inputs(
    store: RunStore,
    run_id: RunId,
    assignment: FinalReviewAssignment,
) -> tuple[PreparedMediaManifest, tuple[FinalReviewSegmentInput, ...]]:
    """Load confined stored evidence and partition it by assigned segment."""
    paths = store.paths(run_id)
    root = paths.run_dir.resolve()
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    asr = AsrRunArtifact.model_validate_json(
        paths.asr_transcript.read_text(encoding="utf-8")
    )
    transcript = store.load_transcript(run_id)
    if {media.run_id, asr.run_id, transcript.run_id, assignment.run_id} != {run_id}:
        message = "final review execution evidence has the wrong run"
        raise FinalReviewExecutionError(message)

    frames = {
        frame.frame_id: frame.model_copy(
            update={"path": _confined_relative(root, Path(frame.path))}
        )
        for frame in media.frames
    }
    cues: dict[str, Cue] = {}
    for cue in transcript.cues:
        if cue.cue_id is None:
            message = "final review execution requires stable cue IDs"
            raise FinalReviewExecutionError(message)
        cues[cue.cue_id] = cue
    try:
        admitted_tracks = admitted_subtitle_tracks(media, root)
        vtt_evidence = tuple(
            KoreanVttEvidence(
                path=_confined_relative(root, Path(track.path)),
                language=track.language,
                source=track.source,
                contents=_confined_path(root, Path(track.path)).read_text(
                    encoding="utf-8"
                ),
            )
            for track in admitted_tracks
        )
    except SubtitleAdmissionError as error:
        message = "final review subtitle authority is stale or foreign"
        raise FinalReviewExecutionError(message) from error
    try:
        inputs = tuple(
            FinalReviewSegmentInput(
                run_id=run_id,
                run_dir=root,
                assignment_digest=assignment.assignment_digest,
                segment=segment,
                purpose=_PURPOSE,
                failure_context=_FAILURE_CONTEXT,
                forbidden_proxy_metrics=_FORBIDDEN_PROXY_METRICS,
                required_evidence_actions=_REQUIRED_EVIDENCE_ACTIONS,
                observable_stop_condition=_STOP_CONDITION,
                frames=tuple(frames[frame_id] for frame_id in segment.frame_ids),
                cues=tuple(cues[cue_id] for cue_id in segment.cue_ids),
                asr_segments=tuple(
                    item
                    for item in asr.transcript.segments
                    if item.start_ms < segment.end_tc_ms
                    and item.end_ms > segment.start_tc_ms
                ),
                korean_vtt_evidence=vtt_evidence,
            )
            for segment in assignment.segments
        )
    except KeyError as error:
        message = "final review assignment references missing stored evidence"
        raise FinalReviewExecutionError(message) from error
    return media, inputs


def validate_segment_result(
    review_input: FinalReviewSegmentInput,
    result: ReviewSegmentResult,
) -> ReviewSegmentResult:
    """Reparse and require exact assignment and known screen-text coverage."""
    try:
        validated = ReviewSegmentResult.model_validate(result.model_dump())
    except ValidationError as error:
        message = (
            "final review semantic coverage rejection: "
            "segment result is invalid"
        )
        raise FinalReviewExecutionError(message) from error
    expected = review_input.segment
    if (
        validated.segment_id,
        validated.frame_ids,
        validated.cue_ids,
    ) != (expected.segment_id, expected.frame_ids, expected.cue_ids):
        message = "final review segment result does not exactly match assignment order"
        raise FinalReviewExecutionError(message)
    for cue in review_input.cues:
        if cue.screen_text is None or not cue.screen_text.strip():
            continue
        if not any(
            cue.cue_id in observation.compared_cue_ids
            and cue.screen_text in observation.visible_screen_text
            for observation in validated.frame_observations
        ):
            message = (
                "final review observations must account for current cue screen_text"
            )
            raise FinalReviewExecutionError(message)
    return validated


@contextmanager
def source_media_unreadable(
    root: Path,
    media: PreparedMediaManifest,
) -> Generator[None]:
    """Make source media unreadable and restore both exact modes on exit."""
    root = root.resolve()
    source_paths = tuple(
        dict.fromkeys(
            (
                _confined_path(root, Path(media.video_path)),
                _confined_path(root, Path(media.audio_path)),
            )
        )
    )
    modes = tuple(
        (path, stat.S_IMODE(path.stat().st_mode)) for path in source_paths
    )
    try:
        for path, _ in modes:
            path.chmod(0)
        yield
    finally:
        for path, mode in modes:
            path.chmod(mode)


def _confined_relative(root: Path, candidate: Path) -> str:
    return _confined_path(root, candidate).relative_to(root).as_posix()


def _confined_path(root: Path, candidate: Path) -> Path:
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        cwd_resolved = candidate.resolve()
        rooted_resolved = (root / candidate).resolve()
        if cwd_resolved.is_relative_to(root):
            resolved = cwd_resolved
        elif rooted_resolved.is_relative_to(root):
            resolved = rooted_resolved
        else:
            message = (
                "final review execution path must remain confined to the run: "
                f"{candidate}"
            )
            raise FinalReviewExecutionError(message)
    if not resolved.is_relative_to(root):
        message = (
            "final review execution path must remain confined to the run: "
            f"{candidate} (root: {root}, resolved: {resolved})"
        )
        raise FinalReviewExecutionError(message)
    return resolved


__all__ = [
    "build_segment_inputs",
    "source_media_unreadable",
    "validate_segment_result",
]
