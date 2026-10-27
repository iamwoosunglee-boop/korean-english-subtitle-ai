# Copyright 2026 Transcription Module contributors

"""Deterministic fixtures for media checkpoint integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from transcription_module.media_checkpoint import (
    MediaAdapterContracts,
    MediaCheckpointStore,
    StageCompletion,
    StageName,
)
from transcription_module.models import YouTubeVideoUrl

SOURCE_URL: Final = YouTubeVideoUrl("https://youtube.test/watch?v=media")
OTHER_URL: Final = YouTubeVideoUrl("https://youtube.test/watch?v=other")
COMPLETED_AT: Final = datetime(2026, 8, 18, 1, 2, 3, tzinfo=UTC)
CONTRACTS: Final = MediaAdapterContracts(
    downloader="yt-dlp.v1",
    subtitles="yt-dlp-subtitles.v1",
    audio="ffmpeg-audio.v1",
    probe="ffprobe.v1",
    frames="ffmpeg-frames.v1",
)


def contract_for(stage: StageName) -> str:
    """Return the fixture adapter contract for one fixed stage."""
    return {
        StageName.VIDEO: CONTRACTS.downloader,
        StageName.SUBTITLES: CONTRACTS.subtitles,
        StageName.AUDIO: CONTRACTS.audio,
        StageName.PROBE: CONTRACTS.probe,
        StageName.FRAMES: CONTRACTS.frames,
    }[stage]


def stage_files(
    store: MediaCheckpointStore,
    stage: StageName,
    files: tuple[tuple[str, bytes], ...],
) -> StageCompletion:
    """Write deterministic adapter output through a held stage descriptor."""
    with store.open_stage_directory(stage) as directory:
        for name, payload in files:
            with directory.open_file(name) as output:
                _ = output.write(payload)
    return store.inspect_staged(stage, contract_for(stage), COMPLETED_AT)


def publish_video(store: MediaCheckpointStore, payload: bytes = b"video") -> None:
    """Publish the fixture video stage."""
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", payload),),
    )
    _ = store.publish_video(SOURCE_URL, completion)


def publish_required_descendants(store: MediaCheckpointStore) -> None:
    """Publish audio, probe, and fixed-profile frame stages."""
    _ = store.publish_audio(
        stage_files(store, StageName.AUDIO, (("audio.wav", b"audio"),))
    )
    _ = store.publish_probe(
        stage_files(store, StageName.PROBE, (("probe.json", b"{}"),))
    )
    _ = store.publish_frames(
        stage_files(
            store,
            StageName.FRAMES,
            (("f000001.jpg", b"one"), ("f000002.jpg", b"two")),
        )
    )
