# Copyright 2026 Transcription Module contributors

"""Reusable policy-bound FFmpeg VAD evidence with exact source identity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, Self

from pydantic import Field, ValidationError, model_validator

from transcription_module.media import PreparedMediaManifest
from transcription_module.models import FrozenModel, RunId
from transcription_module.recovery import VadSpeechInterval  # noqa: TC001
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.vad import FfmpegVadSubprocess
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    VisualEvidencePolicy,
    default_evidence_policy,
    policy_digest,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class VadEvidenceError(ValueError):
    """Stored VAD evidence is malformed, stale, or bound to another source."""


@dataclass(frozen=True, slots=True)
class _ExpectedVadIdentity:
    run_id: RunId
    policy_digest: str
    audio_sha256: str
    ffmpeg_filter: str
    duration_ms: int


class VadDetector(Protocol):
    """Minimal injected boundary for one FFmpeg speech-presence pass."""

    def detect(
        self,
        audio_path: Path,
        *,
        duration_ms: int,
    ) -> tuple[VadSpeechInterval, ...]:
        """Return speech-presence intervals without lexical or speaker claims."""
        ...


class VadEvidenceArtifact(FrozenModel):
    """One exact audio/policy-bound speech-presence result."""

    schema_version: Literal["vad-evidence.v1"] = "vad-evidence.v1"
    run_id: RunId
    era: Literal[EvidenceEra.POLICY_BOUND] = EvidenceEra.POLICY_BOUND
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ffmpeg_filter: Literal["silencedetect=noise=-35dB:d=0.20"]
    duration_ms: int = Field(gt=0)
    speech_intervals: tuple[VadSpeechInterval, ...]
    lexical_authority: Literal[False] = False
    speaker_authority: Literal[False] = False

    @model_validator(mode="after")
    def intervals_are_ordered_nonoverlapping_and_bounded(self) -> Self:
        """Reject reordered, overlapping, or out-of-duration presence intervals."""
        previous_end = 0
        for interval in self.speech_intervals:
            start = int(interval.start_tc_ms)
            end = int(interval.end_tc_ms)
            if start < previous_end:
                message = "VAD speech intervals must be ordered and nonoverlapping"
                raise ValueError(message)
            if end > self.duration_ms:
                message = "VAD speech interval exceeds source duration"
                raise ValueError(message)
            previous_end = end
        return self


def ensure_vad_evidence(
    store: RunStore,
    run_id: RunId,
    *,
    detector: VadDetector | None = None,
    policy: VisualEvidencePolicy | None = None,
) -> VadEvidenceArtifact:
    """Create VAD once or reuse exact current authority without rewriting it."""
    active_policy = default_evidence_policy() if policy is None else policy
    with store.lock_run(run_id):
        return _ensure_vad_evidence_unlocked(
            store,
            run_id,
            FfmpegVadSubprocess() if detector is None else detector,
            active_policy,
        )


def _ensure_vad_evidence_unlocked(
    store: RunStore,
    run_id: RunId,
    detector: VadDetector,
    policy: VisualEvidencePolicy,
) -> VadEvidenceArtifact:
    paths = store.paths(run_id)
    manifest = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    if manifest.run_id != run_id:
        message = "VAD media manifest has the wrong run"
        raise VadEvidenceError(message)
    audio_path = _resolve_confined(paths.run_dir, Path(manifest.audio_path))
    audio_sha256 = _sha256_file(audio_path)
    expected = _ExpectedVadIdentity(
        run_id=run_id,
        policy_digest=policy_digest(policy),
        audio_sha256=audio_sha256,
        ffmpeg_filter=policy.vad.ffmpeg_filter,
        duration_ms=manifest.duration_ms,
    )
    artifact_path = paths.vad_artifact
    if artifact_path.exists():
        existing = _load_artifact(artifact_path)
        _assert_reusable(existing, expected)
        return existing
    intervals = detector.detect(audio_path, duration_ms=manifest.duration_ms)
    artifact = VadEvidenceArtifact(
        run_id=run_id,
        policy_digest=expected.policy_digest,
        audio_sha256=audio_sha256,
        ffmpeg_filter=policy.vad.ffmpeg_filter,
        duration_ms=manifest.duration_ms,
        speech_intervals=intervals,
    )
    write_model_atomic(artifact_path, artifact)
    return artifact


def _load_artifact(path: Path) -> VadEvidenceArtifact:
    try:
        return VadEvidenceArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        message = "stored VAD evidence is invalid"
        raise VadEvidenceError(message) from error


def _assert_reusable(
    artifact: VadEvidenceArtifact,
    expected: _ExpectedVadIdentity,
) -> None:
    checks: Sequence[tuple[bool, str]] = (
        (artifact.run_id == expected.run_id, "VAD run identity is stale"),
        (
            artifact.policy_digest == expected.policy_digest,
            "VAD policy identity is stale",
        ),
        (
            artifact.audio_sha256 == expected.audio_sha256,
            "VAD audio identity is stale",
        ),
        (
            artifact.ffmpeg_filter == expected.ffmpeg_filter,
            "VAD filter is stale",
        ),
        (
            artifact.duration_ms == expected.duration_ms,
            "VAD duration identity is stale",
        ),
    )
    for valid, message in checks:
        if not valid:
            raise VadEvidenceError(message)


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        message = "VAD audio source is missing"
        raise VadEvidenceError(message)
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _resolve_confined(run_dir: Path, candidate: Path) -> Path:
    root = run_dir.resolve()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        cwd_candidate = candidate.resolve()
        rooted_candidate = (root / candidate).resolve()
        resolved = (
            cwd_candidate
            if cwd_candidate.is_relative_to(root)
            else rooted_candidate
        )
    if not resolved.is_relative_to(root):
        message = f"VAD audio path leaves the run: {candidate}"
        raise VadEvidenceError(message)
    return resolved


__all__ = [
    "VadDetector",
    "VadEvidenceArtifact",
    "VadEvidenceError",
    "ensure_vad_evidence",
]
