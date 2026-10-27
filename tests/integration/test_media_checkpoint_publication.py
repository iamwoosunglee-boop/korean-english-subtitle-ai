# Copyright 2026 Transcription Module contributors

"""Crash-window and committed-success media publication contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import transcription_module._media_checkpoint_publication as publication_module
from tests.integration.media_checkpoint_support import (
    COMPLETED_AT,
    CONTRACTS,
    SOURCE_URL,
    publish_video,
    stage_files,
)
from transcription_module._media_checkpoint_publication import FaultPoint
from transcription_module.media_checkpoint import (
    CheckpointPublicationError,
    MediaCheckpointStore,
    StageName,
    SubtitleUnavailable,
)

if TYPE_CHECKING:
    from pathlib import Path

PUBLISH_POINTS = (
    FaultPoint.PUBLISH_BEFORE_JOURNAL,
    FaultPoint.PUBLISH_AFTER_JOURNAL,
    FaultPoint.PUBLISH_BEFORE_OUTPUT,
    FaultPoint.PUBLISH_AFTER_OUTPUT,
    FaultPoint.PUBLISH_BEFORE_CHECKPOINT,
    FaultPoint.PUBLISH_AFTER_CHECKPOINT,
    FaultPoint.PUBLISH_BEFORE_CLEANUP,
    FaultPoint.PUBLISH_AFTER_CLEANUP,
)


@pytest.mark.parametrize("has_prior_checkpoint", [False, True])
@pytest.mark.parametrize("point", PUBLISH_POINTS)
def test_same_publication_converges_when_every_durable_edge_interrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_prior_checkpoint: bool,
    point: FaultPoint,
) -> None:
    """A retry commits or replays after every before/after durable edge."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    if has_prior_checkpoint:
        _ = store.mark_subtitles_unavailable(
            SubtitleUnavailable(
                source_url=SOURCE_URL,
                adapter_contract_version=CONTRACTS.subtitles,
                completed_at=COMPLETED_AT,
            )
        )
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"video"),),
    )
    interrupted = False

    def interrupt(selected: FaultPoint) -> None:
        nonlocal interrupted
        if selected is point and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(publication_module, "publication_fault", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _ = store.publish_video(SOURCE_URL, completion)
    monkeypatch.undo()

    # When
    receipt = store.publish_video(SOURCE_URL, completion)

    # Then
    assert receipt == store.load().video
    assert ((store.root / "media/video") / "source.mp4").read_bytes() == (b"video")
    assert not (store.root / ".media-checkpoint/publication.json").exists()


def test_post_journal_delete_retry_returns_committed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T7-AV-003: unlink-then-interrupt is deterministic committed success."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"video"),),
    )

    def interrupt_after_delete(point: FaultPoint) -> None:
        if point is FaultPoint.PUBLISH_AFTER_CLEANUP:
            raise KeyboardInterrupt

    monkeypatch.setattr(publication_module, "publication_fault", interrupt_after_delete)
    with pytest.raises(KeyboardInterrupt):
        _ = store.publish_video(SOURCE_URL, completion)
    monkeypatch.undo()
    committed = (store.root / ".media-checkpoint/checkpoint.json").read_bytes()

    # When
    first = store.publish_video(SOURCE_URL, completion)
    second = store.publish_video(SOURCE_URL, completion)

    # Then
    assert first == second
    assert (store.root / ".media-checkpoint/checkpoint.json").read_bytes() == committed


def test_conflicting_duplicate_fails_without_changing_committed_bytes(
    tmp_path: Path,
) -> None:
    """A different staged tree cannot masquerade as an exact replay."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    checkpoint = (store.root / ".media-checkpoint/checkpoint.json").read_bytes()
    output = ((store.root / "media/video") / "source.mp4").read_bytes()
    conflicting = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"different"),),
    )

    # When / Then
    with pytest.raises(CheckpointPublicationError, match="already published"):
        _ = store.publish_video(SOURCE_URL, conflicting)
    assert (store.root / ".media-checkpoint/checkpoint.json").read_bytes() == checkpoint
    assert ((store.root / "media/video") / "source.mp4").read_bytes() == output
