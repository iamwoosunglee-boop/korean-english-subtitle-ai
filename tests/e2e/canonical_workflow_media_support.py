# Copyright 2026 Transcription Module contributors

"""Failed-stage media resume and manifest workflow support."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from tests.integration.test_media_resume import CONTRACTS, SOURCE, FakeMediaAdapters
from transcription_module.media import (
    FrameExtractionError,
    PreparedFrameManifest,
    PreparedMediaManifest,
)
from transcription_module.media_checkpoint import MediaCheckpointStore, StageName
from transcription_module.media_manifest_authority import publish_media_manifest
from transcription_module.media_resume import prepare_media_with_checkpoint
from transcription_module.media_runtime import CheckpointedMediaAdapters
from transcription_module.models import FrameId, RunId, RunState, WorkflowStatus
from transcription_module.storage import RunStore
from transcription_module.subtitle_admission import build_policy_bound_inventory

if TYPE_CHECKING:
    from pathlib import Path

COMPLETED_AT: Final = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MediaWorkflowResult:
    """Final manifest, checkpoint, and exact fixed-adapter ledger."""

    manifest: PreparedMediaManifest
    checkpoint_path: Path
    observations: dict[str, bool | dict[str, int]]


def run_media_workflow(root: Path) -> MediaWorkflowResult:
    """Fail frames once, resume frames only, then repair audio only."""
    run_id = RunId("90000000-0000-4000-8000-000000000019")
    store = RunStore(root / "runs")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=SOURCE,
            workflow_status=WorkflowStatus.PROCESSING,
        )
    )
    with store.lock_run(run_id):
        pass
    input_root = paths.run_dir / "input"
    fake = FakeMediaAdapters()
    fake.fail_frames = True
    runtime = CheckpointedMediaAdapters(fake.bundle(), CONTRACTS, COMPLETED_AT)
    with pytest.raises(FrameExtractionError, match="failed"):
        _ = prepare_media_with_checkpoint(SOURCE, input_root, runtime)
    failed_calls = fake.calls.copy()
    assert not paths.media_manifest.exists()

    fake.fail_frames = False
    prepared = prepare_media_with_checkpoint(SOURCE, input_root, runtime)
    resumed_calls = fake.calls.copy()
    assert resumed_calls == failed_calls + Counter({"frames": 1})
    frames = tuple(
        PreparedFrameManifest(
            frame_id=FrameId(frame_path.stem),
            frame_index=index,
            raw_pts=probe.pts,
            raw_pts_time=probe.pts_time,
            best_effort_timestamp=probe.best_effort_timestamp,
            best_effort_timestamp_time=probe.best_effort_timestamp_time,
            pts_ms=probe.pts_ms,
            path=str(frame_path.relative_to(paths.run_dir)),
        )
        for index, (frame_path, probe) in enumerate(
            zip(prepared.frame_paths, prepared.probe.frames, strict=True)
        )
    )
    _ = publish_media_manifest(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=SOURCE,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(prepared.video_path.relative_to(paths.run_dir)),
            audio_path=str(prepared.audio_path.relative_to(paths.run_dir)),
            subtitle_tracks=prepared.subtitle_tracks,
            subtitle_inventory=build_policy_bound_inventory(
                paths.run_dir,
                prepared.subtitle_tracks,
            ),
            duration_ms=prepared.probe.duration_ms,
            frame_count=len(frames),
            frames=frames,
        ),
    )
    manifest_bytes = paths.media_manifest.read_bytes()
    parsed = PreparedMediaManifest.model_validate_json(manifest_bytes)
    checkpoint_store = MediaCheckpointStore(input_root)
    checkpoint = checkpoint_store.load()
    assert checkpoint_store.validate(SOURCE, CONTRACTS).valid_stages == tuple(StageName)

    audio = input_root / "media/audio/audio.wav"
    _ = audio.write_bytes(b"tampered-audio")
    before_audio_repair = fake.calls.copy()
    repaired = prepare_media_with_checkpoint(SOURCE, input_root, runtime)
    assert fake.calls == before_audio_repair + Counter({"audio": 1})
    assert repaired.audio_path == prepared.audio_path
    assert paths.media_manifest.read_bytes() == manifest_bytes
    assert not tuple(input_root.glob(".media-adapter-*"))
    checkpoint_path = input_root / ".media-checkpoint/checkpoint.json"
    assert MediaCheckpointStore(input_root).load() == checkpoint
    assert MediaCheckpointStore(input_root).validate(
        SOURCE, CONTRACTS
    ).valid_stages == tuple(StageName)
    return MediaWorkflowResult(
        parsed,
        checkpoint_path,
        {
            "failed_calls": dict(failed_calls),
            "resumed_calls": dict(resumed_calls),
            "audio_repair_calls": dict(fake.calls),
            "frames_only_resume": True,
            "audio_only_repair": True,
            "manifest_stable": True,
        },
    )
