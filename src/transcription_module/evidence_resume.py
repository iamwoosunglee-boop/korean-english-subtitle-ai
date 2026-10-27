# Copyright 2026 Transcription Module contributors

"""Validate complete evidence from a source-change protected-run failure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Never, override

from pydantic import BaseModel, Field, ValidationError

from transcription_module.asr import AsrRunArtifact
from transcription_module.drafting import (
    DiarizationRunArtifact,
    DiarizationStatus,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import FrozenModel, RunId, RunState, WorkflowStatus
from transcription_module.protected import ProtectedRunArtifact

if TYPE_CHECKING:
    from transcription_module.storage import RunPaths, RunStore

_DOWNSTREAM_ARTIFACTS: Final = (
    "window-drafts.json",
    "whole-transcript.json",
    "speaker-inference.json",
    "transcript.json",
    "transcript.csv",
    "confirmation-session.json",
    "confirmations.jsonl",
    "validation.json",
)


@dataclass(frozen=True, slots=True)
class EvidenceResumeError(Exception):
    """Existing evidence is incomplete, mismatched, or unsafe to reuse."""

    detail: str

    @override
    def __str__(self) -> str:
        """Return the stable validation diagnostic."""
        return self.detail


class EvidenceResumeSummary(FrozenModel):
    """Counts proving which existing evidence was accepted for reuse."""

    run_id: RunId
    duration_ms: int = Field(gt=0)
    frame_count: int = Field(ge=0)
    subtitle_track_count: int = Field(ge=0)
    asr_segment_count: int = Field(ge=0)
    diarization_segment_count: int = Field(ge=0)


def validate_resumable_evidence(
    run_id: RunId,
    store: RunStore,
) -> EvidenceResumeSummary:
    """Validate one failed protected run without regenerating its evidence."""
    paths = store.paths(run_id)
    state = store.load_state(run_id)
    protected = _read_model(paths.run_dir / "protected-run.json", ProtectedRunArtifact)
    media = _read_model(paths.media_manifest, PreparedMediaManifest)
    asr = _read_model(paths.asr_transcript, AsrRunArtifact)
    diarization = _read_model(
        paths.diarization_artifact,
        DiarizationRunArtifact,
    )
    _validate_run_state(state)
    _validate_protected_failure(protected)
    _validate_no_downstream(paths)
    _validate_bindings(
        run_id,
        state=state,
        media=media,
        asr=asr,
        diarization=diarization,
    )
    _validate_referenced_files(paths, media)
    _validate_timelines(media.duration_ms, asr, diarization)

    return EvidenceResumeSummary(
        run_id=run_id,
        duration_ms=media.duration_ms,
        frame_count=media.frame_count,
        subtitle_track_count=len(media.subtitle_tracks),
        asr_segment_count=len(asr.transcript.segments),
        diarization_segment_count=len(diarization.segments),
    )


def _validate_run_state(state: RunState) -> None:
    if state.context is None:
        _fail("episode context is required")
    if state.workflow_status is not WorkflowStatus.PROCESSING:
        _fail("run must be processing")


def _validate_protected_failure(protected: ProtectedRunArtifact) -> None:
    if not (
        protected.status == "failed"
        and protected.source_changed
        and protected.error == "source changed during protected execution"
    ):
        _fail(
            "run is not a source-change protected execution failure"
        )


def _validate_no_downstream(paths: RunPaths) -> None:
    for name in _DOWNSTREAM_ARTIFACTS:
        if (paths.run_dir / name).exists():
            _fail(f"downstream artifact already exists: {name}")


def _validate_bindings(
    run_id: RunId,
    *,
    state: RunState,
    media: PreparedMediaManifest,
    asr: AsrRunArtifact,
    diarization: DiarizationRunArtifact,
) -> None:
    if {state.run_id, media.run_id, asr.run_id, diarization.run_id} != {run_id}:
        _fail("evidence run ID does not match")
    if media.source_url != state.source_url:
        _fail("media source URL does not match")
    if media.frame_count != len(media.frames):
        _fail("media frame count does not match")
    if diarization.status is not DiarizationStatus.AVAILABLE:
        _fail("diarization evidence is not available")


def _validate_referenced_files(
    paths: RunPaths,
    media: PreparedMediaManifest,
) -> None:
    referenced = (
        Path(media.video_path),
        Path(media.audio_path),
        *(Path(track.path) for track in media.subtitle_tracks),
        *(_resolve_frame_path(paths, frame.path) for frame in media.frames),
    )
    missing = [path for path in referenced if not path.is_file()]
    if missing:
        _fail(f"referenced media is missing: {missing[0]}")


def _validate_timelines(
    duration_ms: int,
    asr: AsrRunArtifact,
    diarization: DiarizationRunArtifact,
) -> None:
    if any(
        segment.end_ms > duration_ms
        for segment in asr.transcript.segments
    ):
        _fail("ASR evidence exceeds media duration")
    if any(
        segment.end_tc_ms > duration_ms
        for segment in diarization.segments
    ):
        _fail("diarization evidence exceeds media duration")


def _read_model[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
) -> ModelT:
    if not path.is_file():
        _fail(f"required evidence is missing: {path.name}")
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        detail = f"invalid evidence artifact: {path.name}"
        raise EvidenceResumeError(detail) from error


def _resolve_frame_path(paths: RunPaths, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() or path.is_file() else paths.run_dir / path


def _fail(detail: str) -> Never:
    raise EvidenceResumeError(detail)
