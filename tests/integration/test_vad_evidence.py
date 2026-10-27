# Copyright 2026 Transcription Module contributors

"""Policy-bound VAD evidence is reusable, non-lexical, and source-bound."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import pytest
from pydantic import ValidationError

from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    Cue,
    EpisodeContext,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.recovery import VadSpeechInterval
from transcription_module.recovery_stage import detect_and_queue_vad_recovery
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.vad_evidence import (
    VadEvidenceArtifact,
    VadEvidenceError,
    ensure_vad_evidence,
)
from transcription_module.visual_evidence_policy import (
    VisualEvidencePolicy,
    default_evidence_policy,
    policy_digest,
)

if TYPE_CHECKING:
    from pathlib import Path

DURATION_MS = 1_000


@dataclass
class CountingVadDetector:
    """Return one interval while recording each detector invocation."""

    calls: int = 0

    def detect(
        self,
        audio_path: Path,
        *,
        duration_ms: int,
    ) -> tuple[VadSpeechInterval, ...]:
        """Return one full-duration speech-presence interval."""
        assert audio_path.is_file()
        assert duration_ms == DURATION_MS
        self.calls += 1
        return (
            VadSpeechInterval(
                start_tc_ms=Milliseconds(0),
                end_tc_ms=Milliseconds(DURATION_MS),
            ),
        )


class FailingVadDetector(CountingVadDetector):
    """Fail if stored evidence is not reused."""

    @override
    def detect(
        self,
        audio_path: Path,
        *,
        duration_ms: int,
    ) -> tuple[VadSpeechInterval, ...]:
        del audio_path, duration_ms
        raise AssertionError


def _prepared_run(tmp_path: Path) -> tuple[RunStore, RunId, Path]:
    store = RunStore(tmp_path / "runs")
    run_id = RunId("policy-bound-vad-run")
    source_url = YouTubeVideoUrl("https://youtube.test/policy-vad")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    audio_path = paths.run_dir / "input/media/audio/audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    _ = audio_path.write_bytes(b"stable audio bytes")
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(paths.run_dir / "input/media/video/source.mp4"),
            audio_path=str(audio_path),
            duration_ms=DURATION_MS,
            frame_count=0,
        ),
    )
    store.save_transcript(
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.PROCESSING,
            validation_status=ValidationStatus.PENDING,
            duration_ms=Milliseconds(DURATION_MS),
            cues=(
                Cue(
                    sequence=1,
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(DURATION_MS),
                    speech="기존 발화",
                    speaker="화자",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
            vision_evidence=(),
        )
    )
    return store, run_id, audio_path


def test_vad_evidence_runs_once_and_reuses_exact_authority(tmp_path: Path) -> None:
    """The second request reuses exact bytes and never invokes FFmpeg again."""
    store, run_id, audio_path = _prepared_run(tmp_path)
    detector = CountingVadDetector()

    first = ensure_vad_evidence(store, run_id, detector=detector)
    path = store.paths(run_id).vad_artifact
    before = path.read_bytes()
    inode = path.stat().st_ino
    second = ensure_vad_evidence(store, run_id, detector=detector)

    assert detector.calls == 1
    assert second == first
    assert path.read_bytes() == before
    assert path.stat().st_ino == inode
    assert first.audio_sha256 == hashlib.sha256(audio_path.read_bytes()).hexdigest()
    assert first.policy_digest == policy_digest(default_evidence_policy())
    assert first.ffmpeg_filter == "silencedetect=noise=-35dB:d=0.20"
    assert first.lexical_authority is False
    assert first.speaker_authority is False


def test_vad_reuse_rejects_changed_audio_without_overwrite(tmp_path: Path) -> None:
    """A stale audio digest blocks reuse and preserves committed VAD bytes."""
    store, run_id, audio_path = _prepared_run(tmp_path)
    _ = ensure_vad_evidence(store, run_id, detector=CountingVadDetector())
    path = store.paths(run_id).vad_artifact
    before = path.read_bytes()
    _ = audio_path.write_bytes(b"changed audio bytes")
    detector = CountingVadDetector()

    with pytest.raises(VadEvidenceError, match="audio"):
        _ = ensure_vad_evidence(store, run_id, detector=detector)

    assert detector.calls == 0
    assert path.read_bytes() == before


def test_vad_reuse_rejects_changed_policy_without_overwrite(tmp_path: Path) -> None:
    """A stale policy digest blocks reuse before detector or file mutation."""
    store, run_id, _ = _prepared_run(tmp_path)
    _ = ensure_vad_evidence(store, run_id, detector=CountingVadDetector())
    path = store.paths(run_id).vad_artifact
    before = path.read_bytes()
    current = default_evidence_policy()
    changed = current.model_copy(
        update={
            "contract_versions": current.contract_versions.model_copy(
                update={"cue_provenance": "cue-provenance.v2"}
            )
        }
    )
    detector = CountingVadDetector()

    with pytest.raises(VadEvidenceError, match="policy"):
        _ = ensure_vad_evidence(
            store,
            run_id,
            detector=detector,
            policy=changed,
        )

    assert detector.calls == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("authority", ["lexical_authority", "speaker_authority"])
def test_vad_policy_cannot_enable_lexical_or_speaker_authority(
    authority: str,
) -> None:
    """The canonical policy itself cannot grant VAD semantic authority."""
    payload = default_evidence_policy().model_dump(mode="json")
    payload["vad"][authority] = True

    with pytest.raises(ValidationError):
        _ = VisualEvidencePolicy.model_validate(payload)


@pytest.mark.parametrize(
    "payload_update",
    [
        {"lexical_authority": True},
        {"speaker_authority": True},
        {"speech": "VAD가 단어를 결정하면 안 됨"},
        {"speaker": "VAD가 화자를 결정하면 안 됨"},
    ],
)
def test_vad_artifact_rejects_lexical_or_speaker_authority(
    tmp_path: Path,
    payload_update: dict[str, object],
) -> None:
    """VAD records timing presence only; lexical and speaker claims fail."""
    store, run_id, _ = _prepared_run(tmp_path)
    artifact = ensure_vad_evidence(
        store,
        run_id,
        detector=CountingVadDetector(),
    )
    payload = artifact.model_dump(mode="json")
    payload.update(payload_update)

    with pytest.raises(ValidationError):
        _ = VadEvidenceArtifact.model_validate(payload)


@pytest.mark.parametrize(
    ("start_ms", "end_ms"),
    [(0, 0), (500, 400)],
)
def test_vad_intervals_reject_zero_or_reversed_bounds(
    start_ms: int,
    end_ms: int,
) -> None:
    """Invalid half-open VAD intervals fail before artifact persistence."""
    with pytest.raises(ValidationError):
        _ = VadSpeechInterval(
            start_tc_ms=Milliseconds(start_ms),
            end_tc_ms=Milliseconds(end_ms),
        )


def test_recovery_consumes_stored_vad_without_second_detection(tmp_path: Path) -> None:
    """Recovery uses the same stored interval authority rather than rerunning VAD."""
    store, run_id, _ = _prepared_run(tmp_path)
    stored = ensure_vad_evidence(
        store,
        run_id,
        detector=CountingVadDetector(),
    )
    failing = FailingVadDetector()

    recovery = detect_and_queue_vad_recovery(
        store,
        run_id,
        detector=failing,
    )

    assert failing.calls == 0
    assert recovery.vad_intervals == stored.speech_intervals
    assert recovery.missing_count == 0
