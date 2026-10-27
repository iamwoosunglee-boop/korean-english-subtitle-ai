# Copyright 2026 Transcription Module contributors

"""Public CLI evidence inventory and reuse decisions."""

from __future__ import annotations

import hashlib
import subprocess
from typing import TYPE_CHECKING, Final

from transcription_module.evidence_status_models import EvidenceStatusReport
from transcription_module.media import (
    PreparedMediaManifest,
    PreparedSubtitleTrack,
    SubtitleSource,
)
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.subtitle_admission import build_policy_bound_inventory
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    canonical_policy_bytes,
    default_evidence_policy,
    policy_digest,
)

if TYPE_CHECKING:
    from pathlib import Path

_RUN_ID: Final = RunId("53fc2ed5-449f-46df-94ac-35f4060cdfe1")


def test_evidence_status_reports_current_policy_sources_and_absent_descendants(
    tmp_path: Path,
) -> None:
    """Given prepared policy evidence, the CLI reports reuse without mutation."""
    # Given
    store = RunStore(tmp_path / "runs")
    paths = store.create(
        RunState(
            run_id=_RUN_ID,
            source_url=YouTubeVideoUrl("https://youtu.be/evidence-status"),
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    input_dir = paths.run_dir / "input"
    input_dir.mkdir()
    video = input_dir / "source.mp4"
    audio = input_dir / "audio.wav"
    subtitle = input_dir / "automatic.ko.ko-orig.vtt"
    _ = video.write_bytes(b"video")
    audio_bytes = b"audio"
    _ = audio.write_bytes(audio_bytes)
    subtitle_bytes = b"WEBVTT\n"
    _ = subtitle.write_bytes(subtitle_bytes)
    track = PreparedSubtitleTrack(
        path=str(subtitle),
        language="ko",
        source=SubtitleSource.AUTOMATIC,
        track_id="automatic-ko-orig",
        variant="ko-orig",
        sha256=hashlib.sha256(subtitle_bytes).hexdigest(),
        eligible_for_speech=True,
    )
    inventory = build_policy_bound_inventory(paths.run_dir, (track,))
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=_RUN_ID,
            source_url=YouTubeVideoUrl("https://youtu.be/evidence-status"),
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(video),
            audio_path=str(audio),
            subtitle_tracks=(track,),
            subtitle_inventory=inventory,
            duration_ms=1_000,
            frame_count=0,
        ),
    )
    _ = paths.visual_policy_artifact.write_bytes(
        canonical_policy_bytes(default_evidence_policy()) + b"\n"
    )
    write_model_atomic(paths.subtitle_inventory_artifact, inventory)
    write_model_atomic(
        paths.vad_artifact,
        VadEvidenceArtifact(
            run_id=_RUN_ID,
            policy_digest=policy_digest(default_evidence_policy()),
            audio_sha256=hashlib.sha256(audio_bytes).hexdigest(),
            ffmpeg_filter="silencedetect=noise=-35dB:d=0.20",
            duration_ms=1_000,
            speech_intervals=(),
        ),
    )
    before = {
        path.relative_to(paths.run_dir).as_posix(): path.read_bytes()
        for path in paths.run_dir.rglob("*")
        if path.is_file() and path.name != ".run.lock"
    }

    # When
    result = subprocess.run(
        (
            "uv",
            "run",
            "transcription-cli",
            "evidence-status",
            str(_RUN_ID),
            "--runs-dir",
            str(store.runs_dir),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert result.returncode == 0, result.stderr
    status = EvidenceStatusReport.model_validate_json(result.stdout)
    assert status.policy.state == "current"
    assert status.subtitle_inventory.state == "current"
    assert status.subtitle_inventory.exact_ko_orig_count == 1
    assert status.subtitle_inventory.availability == "available"
    assert status.vad.state == "current"
    assert status.window_evidence.state == "absent"
    assert status.deterministic_assembly.state == "absent"
    assert status.invalidation_required is False
    assert status.reusable is True
    assert status.operation_events.model_dump() == {
        "availability": "unavailable",
        "reason_code": "operation_events_unavailable",
        "blocks_transcription": False,
    }
    after = {
        path.relative_to(paths.run_dir).as_posix(): path.read_bytes()
        for path in paths.run_dir.rglob("*")
        if path.is_file() and path.name != ".run.lock"
    }
    assert after == before
