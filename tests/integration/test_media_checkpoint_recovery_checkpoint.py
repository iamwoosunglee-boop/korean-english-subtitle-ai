# Copyright 2026 Transcription Module contributors

"""Checkpoint-side rollback interruption contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import transcription_module._media_checkpoint_publication as publication_module
from tests.integration.media_checkpoint_support import (
    COMPLETED_AT,
    CONTRACTS,
    SOURCE_URL,
    stage_files,
)
from transcription_module._media_checkpoint_publication import FaultPoint
from transcription_module.media_checkpoint import (
    MediaCheckpointStore,
    StageName,
    SubtitleUnavailable,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("has_prior_checkpoint", [False, True])
@pytest.mark.parametrize(
    "point",
    [
        FaultPoint.RECOVER_BEFORE_CHECKPOINT,
        FaultPoint.RECOVER_AFTER_CHECKPOINT,
    ],
)
def test_checkpoint_restore_retries_across_both_durable_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_prior_checkpoint: bool,
    point: FaultPoint,
) -> None:
    """An interrupted exact-sidecar restore remains journal-authoritative."""
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
    prior = (
        (store.root / ".media-checkpoint/checkpoint.json").read_bytes()
        if (store.root / ".media-checkpoint/checkpoint.json").exists()
        else None
    )
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"video"),),
    )

    def interrupt_after_checkpoint(selected: FaultPoint) -> None:
        if selected is FaultPoint.PUBLISH_AFTER_CHECKPOINT:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        publication_module,
        "publication_fault",
        interrupt_after_checkpoint,
    )
    with pytest.raises(KeyboardInterrupt):
        _ = store.publish_video(SOURCE_URL, completion)
    monkeypatch.undo()
    published = store.root / "media/video"
    (published / "source.mp4").unlink()
    published.rmdir()
    interrupted = False

    def interrupt_restore(selected: FaultPoint) -> None:
        nonlocal interrupted
        if selected is point and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(publication_module, "publication_fault", interrupt_restore)
    with pytest.raises(KeyboardInterrupt):
        _ = store.load()
    monkeypatch.undo()

    # When
    _ = store.load()

    # Then
    if prior is None:
        assert not (store.root / ".media-checkpoint/checkpoint.json").exists()
    else:
        assert (store.root / ".media-checkpoint/checkpoint.json").read_bytes() == prior
    assert (store.root / ".media-checkpoint/publication.json").exists() is False
    receipt = store.publish_video(SOURCE_URL, completion)
    assert receipt == store.load().video
