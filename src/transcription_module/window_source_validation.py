# Copyright 2026 Transcription Module contributors

"""Filesystem and run identity checks for policy-window source snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from transcription_module._drafting_models import DiarizationRunArtifact
    from transcription_module.asr import AsrRunArtifact
    from transcription_module.media import PreparedMediaManifest
    from transcription_module.models import RunId
    from transcription_module.vad_evidence import VadEvidenceArtifact


@dataclass(frozen=True, slots=True)
class PersistedWindowSources:
    """Complete stored inputs whose identities authorize policy drafting."""

    run_id: RunId
    run_dir: Path
    media: PreparedMediaManifest
    asr: AsrRunArtifact
    vad: VadEvidenceArtifact
    diarization: DiarizationRunArtifact | None


def validate_persisted_window_sources(sources: PersistedWindowSources) -> None:
    """Reject foreign models, changed audio, and missing manifest frames."""
    run_ids = (
        sources.media.run_id,
        sources.asr.run_id,
        sources.vad.run_id,
        *(() if sources.diarization is None else (sources.diarization.run_id,)),
    )
    if any(item != sources.run_id for item in run_ids):
        fail_window_evidence("policy-bound window source has a foreign run identity")
    audio = _resolve_file(sources.run_dir, Path(sources.media.audio_path), "audio")
    audio_sha = _sha256(audio)
    if sources.asr.audio_sha256 != audio_sha or sources.vad.audio_sha256 != audio_sha:
        fail_window_evidence("policy-bound window audio bytes are stale")
    for frame in sources.media.frames:
        _ = _resolve_file(sources.run_dir, Path(frame.path), "frame")


def _resolve_file(run_dir: Path, candidate: Path, label: str) -> Path:
    root = run_dir.resolve()
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    )
    if not resolved.is_relative_to(root) or not resolved.is_file():
        fail_window_evidence(
            f"policy-bound window {label} is missing or leaves its run"
        )
    return resolved


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


__all__ = ["PersistedWindowSources", "validate_persisted_window_sources"]
