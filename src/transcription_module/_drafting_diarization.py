# Copyright 2026 Transcription Module contributors

"""Fallible optional diarization for window drafting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pyannote.audio import Pipeline

from transcription_module._drafting_models import (
    DiarizationError,
    DiarizationRunArtifact,
    DiarizationStatus,
    Diarizer,
    SpeakerEvidenceSegment,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import RunStore, write_model_atomic

if TYPE_CHECKING:
    from transcription_module.models import RunId


@dataclass(frozen=True, slots=True)
class PyannoteDiarizer:
    """Load pyannote lazily and keep its cluster labels non-identifying."""

    token: str | None = None
    model: str = "pyannote/speaker-diarization-3.1"

    def __call__(self, audio_path: Path) -> tuple[SpeakerEvidenceSegment, ...]:
        """Return time-bounded opaque clusters for the whole audio file."""
        try:
            pipeline = Pipeline.from_pretrained(self.model, token=self.token)
        except Exception as error:
            raise DiarizationError(str(error)) from error
        if pipeline is None:
            message = f"unable to load pyannote pipeline: {self.model}"
            raise DiarizationError(message)
        try:
            output = pipeline(str(audio_path))
            return tuple(
                SpeakerEvidenceSegment(
                    start_tc_ms=round(turn.start * 1_000),
                    end_tc_ms=round(turn.end * 1_000),
                    cluster=str(cluster),
                )
                for turn, _, cluster in output.speaker_diarization.itertracks(
                    yield_label=True
                )
            )
        except Exception as error:
            raise DiarizationError(str(error)) from error


def run_optional_diarization(
    store: RunStore,
    run_id: RunId,
    diarizer: Diarizer,
) -> DiarizationRunArtifact:
    """Run whole-audio clustering once and persist failure without raising."""
    paths = store.paths(run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    try:
        segments = diarizer(Path(media.audio_path))
    except DiarizationError as error:
        artifact = DiarizationRunArtifact(
            run_id=run_id,
            status=DiarizationStatus.FAILED,
            error=str(error),
        )
    else:
        artifact = DiarizationRunArtifact(
            run_id=run_id,
            status=DiarizationStatus.AVAILABLE,
            segments=segments,
        )
    write_model_atomic(paths.diarization_artifact, artifact)
    return artifact


__all__ = ["PyannoteDiarizer", "run_optional_diarization"]
