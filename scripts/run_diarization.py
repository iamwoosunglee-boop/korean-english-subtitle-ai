# Copyright 2026 Transcription Module contributors
"""Run whole-audio pyannote diarization once for one existing run.

The access token is read from the environment or a local .env file and is
never written to artifacts or logs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from pyannote.audio import Pipeline

from transcription_module.boundaries import parse_run_id
from transcription_module.drafting import (
    DiarizationError,
    SpeakerEvidenceSegment,
    run_optional_diarization,
)
from transcription_module.storage import RunStore

_RUNS_DIR_ARG_INDEX = 2


def _read_token() -> str:
    """Read the HuggingFace token without exposing it."""
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("HF_TOKEN="):
                return line.split("=", 1)[1].strip()
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        message = "HF token is not configured"
        raise DiarizationError(message)
    return token


def diarize(audio_path: Path) -> tuple[SpeakerEvidenceSegment, ...]:
    """Cluster whole-audio voices without assigning person identities."""
    token = _read_token()
    try:
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=token,
        )
        result = pipeline(str(audio_path))
        annotation = getattr(result, "speaker_diarization", result)
        return tuple(
            SpeakerEvidenceSegment(
                start_tc_ms=round(turn.start * 1_000),
                end_tc_ms=round(turn.end * 1_000),
                cluster=str(speaker),
            )
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        )
    except Exception as error:
        raise DiarizationError(str(error)) from error


def main() -> None:
    """Apply diarization to one run and report the persisted status."""
    run_id = parse_run_id(sys.argv[1])
    runs_dir = (
        Path(sys.argv[_RUNS_DIR_ARG_INDEX])
        if len(sys.argv) > _RUNS_DIR_ARG_INDEX
        else Path("runs")
    )
    artifact = run_optional_diarization(RunStore(runs_dir), run_id, diarize)
    typer.echo(
        f"status={artifact.status.value} segments={len(artifact.segments)}"
        f" error={artifact.error}"
    )


if __name__ == "__main__":
    main()
