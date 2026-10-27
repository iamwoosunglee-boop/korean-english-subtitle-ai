# Copyright 2026 Transcription Module contributors

"""Private canonical input and replacement helpers for final review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from transcription_module.final_review_models import (
    FinalReviewAssignment,
    FinalReviewResult,
    ReviewSegmentAssignment,
)
from transcription_module.media import PreparedFrameManifest, PreparedMediaManifest
from transcription_module.models import (
    Cue,
    CueId,
    Milliseconds,
    RunId,
    Transcript,
)

if TYPE_CHECKING:
    from transcription_module.storage import RunStore


type JsonValue = str | int | list[JsonValue] | dict[str, JsonValue] | None

_SEGMENT_DURATION_MS = 300_000


@dataclass(slots=True)
class FinalReviewError(Exception):
    """A final review is incomplete, stale, or unsafe to apply."""

    message: str

    @override
    def __str__(self) -> str:
        return self.message


def build_assignment(store: RunStore, run_id: RunId) -> FinalReviewAssignment:
    """Build the deterministic assignment bound to current stored inputs."""
    manifest, transcript, media_digest, transcript_digest = load_current_inputs(
        store,
        run_id,
    )
    frame_ids = tuple(frame.frame_id for frame in manifest.frames)
    cue_ids = cue_ids_for(transcript)
    segment_count = (
        transcript.duration_ms + _SEGMENT_DURATION_MS - 1
    ) // _SEGMENT_DURATION_MS
    segments = tuple(
        ReviewSegmentAssignment(
            segment_id=f"segment-{index + 1:04d}",
            start_tc_ms=Milliseconds(index * _SEGMENT_DURATION_MS),
            end_tc_ms=Milliseconds(
                min((index + 1) * _SEGMENT_DURATION_MS, transcript.duration_ms)
            ),
            frame_ids=tuple(
                frame.frame_id
                for frame in manifest.frames
                if frame.pts_ms // _SEGMENT_DURATION_MS == index
            ),
            cue_ids=tuple(
                cue_id
                for cue, cue_id in zip(transcript.cues, cue_ids, strict=True)
                if cue.start_tc_ms // _SEGMENT_DURATION_MS == index
            ),
        )
        for index in range(segment_count)
    )
    unbound = FinalReviewAssignment(
        run_id=run_id,
        duration_ms=transcript.duration_ms,
        frame_ids=frame_ids,
        cue_ids=cue_ids,
        media_digest=media_digest,
        transcript_digest=transcript_digest,
        assignment_digest="0" * 64,
        segments=segments,
    )
    return unbound.model_copy(
        update={"assignment_digest": assignment_digest_for(unbound)}
    )


def assignment_digest_for(assignment: FinalReviewAssignment) -> str:
    """Digest every ordered semantic field of a final-review assignment."""
    return _digest(
        {
            "run_id": str(assignment.run_id),
            "duration_ms": assignment.duration_ms,
            "frame_ids": list(assignment.frame_ids),
            "cue_ids": list(assignment.cue_ids),
            "media_digest": assignment.media_digest,
            "transcript_digest": assignment.transcript_digest,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "start_tc_ms": segment.start_tc_ms,
                    "end_tc_ms": segment.end_tc_ms,
                    "frame_ids": list(segment.frame_ids),
                    "cue_ids": list(segment.cue_ids),
                }
                for segment in assignment.segments
            ],
        }
    )


def result_digest_for(result: FinalReviewResult) -> str:
    """Digest the complete ordered review result."""
    return _digest(result.model_dump(mode="json"))


def load_current_inputs(
    store: RunStore,
    run_id: RunId,
) -> tuple[PreparedMediaManifest, Transcript, str, str]:
    """Load, validate, and digest the current media and transcript inputs."""
    paths = store.paths(run_id)
    manifest = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    transcript = store.load_transcript(run_id)
    if manifest.run_id != run_id or transcript.run_id != run_id:
        msg = "final review input has the wrong run"
        raise FinalReviewError(msg)
    if manifest.frame_count != len(manifest.frames):
        msg = "media frame_count does not match stored frames"
        raise FinalReviewError(msg)
    if manifest.duration_ms != transcript.duration_ms:
        msg = "media and transcript duration must match"
        raise FinalReviewError(msg)
    frame_digests = [_frame_digest(paths.run_dir, frame) for frame in manifest.frames]
    media_digest = _digest(
        cast(
            "dict[str, JsonValue]",
            {
                "run_id": str(run_id),
                "source_url": str(manifest.source_url),
                "duration_ms": manifest.duration_ms,
                "frame_count": manifest.frame_count,
                "frame_digests": frame_digests,
            },
        )
    )
    cue_digests = [_cue_digest(cue) for cue in transcript.cues]
    transcript_digest = _digest(
        cast(
            "dict[str, JsonValue]",
            {
                "run_id": str(run_id),
                "duration_ms": transcript.duration_ms,
                "cue_digests": cue_digests,
                "vision_evidence": [
                    {
                        "evidence_id": str(item.evidence_id),
                        "frame_ids": list(item.frame_ids),
                        "scene": item.scene,
                        "rough_lower_third": item.rough_lower_third,
                    }
                    for item in transcript.vision_evidence
                ],
            },
        )
    )
    return manifest, transcript, media_digest, transcript_digest


def _frame_digest(run_dir: Path, frame: PreparedFrameManifest) -> str:
    relative = Path(frame.path)
    if relative.is_absolute():
        msg = "frame path must be run-relative"
        raise FinalReviewError(msg)
    root = run_dir.resolve()
    frame_path = (run_dir / relative).resolve()
    if not frame_path.is_relative_to(root):
        msg = "frame path must remain confined to the run"
        raise FinalReviewError(msg)
    return _digest(
        {
            "frame_id": str(frame.frame_id),
            "frame_index": frame.frame_index,
            "raw_pts": frame.raw_pts,
            "raw_pts_time": frame.raw_pts_time,
            "best_effort_timestamp": frame.best_effort_timestamp,
            "best_effort_timestamp_time": frame.best_effort_timestamp_time,
            "pts_ms": frame.pts_ms,
            "path": frame.path,
            "bytes_sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
        }
    )


def _cue_digest(cue: Cue) -> str:
    if cue.cue_id is None:
        msg = "every reviewed cue requires a stable cue ID"
        raise FinalReviewError(msg)
    return _digest(
        {
            "sequence": cue.sequence,
            "cue_id": str(cue.cue_id),
            "start_tc_ms": cue.start_tc_ms,
            "end_tc_ms": cue.end_tc_ms,
            "speech": cue.speech,
            "speaker": cue.speaker,
            "uncertainty": cue.uncertainty.value,
            "screen_text": cue.screen_text,
            "vision_evidence_ids": list(cue.vision_evidence_ids),
        }
    )


def cue_ids_for(transcript: Transcript) -> tuple[CueId, ...]:
    """Return complete stable cue identities for a reviewed transcript."""
    cue_ids = tuple(cue.cue_id for cue in transcript.cues)
    if any(cue_id is None for cue_id in cue_ids):
        msg = "every reviewed cue requires a stable cue ID"
        raise FinalReviewError(msg)
    return tuple(cue_id for cue_id in cue_ids if cue_id is not None)


def _digest(value: dict[str, JsonValue]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()
