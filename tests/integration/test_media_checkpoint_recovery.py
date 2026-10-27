# Copyright 2026 Transcription Module contributors

"""Idempotent media publication journal recovery contracts."""

from __future__ import annotations

from dataclasses import dataclass
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
    CheckpointRecoveryError,
    MediaCheckpointStore,
    StageCompletion,
    StageName,
    SubtitleUnavailable,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class PendingVideo:
    """One output-published operation awaiting recovery."""

    store: MediaCheckpointStore
    completion: StageCompletion
    prior_checkpoint: bytes | None


def _pending_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_prior_checkpoint: bool,
) -> PendingVideo:
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

    def interrupt_after_output(point: FaultPoint) -> None:
        if point is FaultPoint.PUBLISH_AFTER_OUTPUT:
            raise KeyboardInterrupt

    monkeypatch.setattr(publication_module, "publication_fault", interrupt_after_output)
    with pytest.raises(KeyboardInterrupt):
        _ = store.publish_video(SOURCE_URL, completion)
    monkeypatch.undo()
    return PendingVideo(store, completion, prior)


def _assert_prior_state(pending: PendingVideo) -> None:
    if pending.prior_checkpoint is None:
        assert not (pending.store.root / ".media-checkpoint/checkpoint.json").exists()
    else:
        assert (
            pending.store.root / ".media-checkpoint/checkpoint.json"
        ).read_bytes() == pending.prior_checkpoint
    assert (
        (pending.store.root / ".media-staging/video") / "source.mp4"
    ).read_bytes() == b"video"
    assert not (pending.store.root / "media/video").exists()


@pytest.mark.parametrize("has_prior_checkpoint", [False, True])
@pytest.mark.parametrize("interruptions", [2, 3])
def test_rollback_retries_after_double_and_triple_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_prior_checkpoint: bool,
    interruptions: int,
) -> None:
    """T7-AV-002: repeated interruption retains one retryable journal."""
    # Given
    pending = _pending_video(
        tmp_path,
        monkeypatch,
        has_prior_checkpoint=has_prior_checkpoint,
    )
    remaining = interruptions

    def interrupt_recovery(point: FaultPoint) -> None:
        nonlocal remaining
        if point is FaultPoint.RECOVER_BEFORE_OUTPUT and remaining > 0:
            remaining -= 1
            raise KeyboardInterrupt

    monkeypatch.setattr(publication_module, "publication_fault", interrupt_recovery)

    # When
    for _ in range(interruptions):
        with pytest.raises(KeyboardInterrupt):
            _ = pending.store.load()
        assert (pending.store.root / ".media-checkpoint/publication.json").exists()
    monkeypatch.undo()
    _ = pending.store.load()

    # Then
    _assert_prior_state(pending)
    receipt = pending.store.publish_video(SOURCE_URL, pending.completion)
    assert receipt == pending.store.load().video


@pytest.mark.parametrize(
    "point",
    [
        FaultPoint.RECOVER_AFTER_OUTPUT,
        FaultPoint.RECOVER_BEFORE_CLEANUP,
        FaultPoint.RECOVER_AFTER_CLEANUP,
    ],
)
def test_recovery_post_operation_interruption_converges_on_next_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: FaultPoint,
) -> None:
    """Recovery is idempotent before and after its durable mutations."""
    # Given
    pending = _pending_video(
        tmp_path,
        monkeypatch,
        has_prior_checkpoint=True,
    )
    interrupted = False

    def interrupt(selected: FaultPoint) -> None:
        nonlocal interrupted
        if selected is point and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(publication_module, "publication_fault", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _ = pending.store.load()
    monkeypatch.undo()

    # When
    _ = pending.store.load()

    # Then
    _assert_prior_state(pending)


def test_tampered_journal_is_rejected_and_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed private journal cannot drive filesystem recovery."""
    # Given
    pending = _pending_video(
        tmp_path,
        monkeypatch,
        has_prior_checkpoint=True,
    )
    original = (pending.store.root / ".media-checkpoint/publication.json").read_bytes()
    assert b"staged_path" not in original
    assert b"destination_path" not in original
    tampered = original.replace(b'"video"', b'"audio"', 1)
    assert tampered != original
    _ = (pending.store.root / ".media-checkpoint/publication.json").write_bytes(
        tampered
    )

    # When / Then
    with pytest.raises(CheckpointRecoveryError, match="journal"):
        _ = pending.store.load()
    assert (
        pending.store.root / ".media-checkpoint/publication.json"
    ).read_bytes() == tampered


def test_missing_copied_output_restores_held_staged_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing copy is safely discarded because staged bytes remain held."""
    # Given
    pending = _pending_video(
        tmp_path,
        monkeypatch,
        has_prior_checkpoint=True,
    )
    published = pending.store.root / "media/video"
    (published / "source.mp4").unlink()
    published.rmdir()

    # When
    _ = pending.store.load()

    # Then
    _assert_prior_state(pending)
    assert not (pending.store.root / ".media-checkpoint/publication.json").exists()


def test_changed_copied_output_restores_held_staged_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uncommitted changed copy is discarded while staged bytes remain."""
    # Given
    pending = _pending_video(
        tmp_path,
        monkeypatch,
        has_prior_checkpoint=True,
    )
    published = pending.store.root / "media/video"
    _ = (published / "source.mp4").write_bytes(b"different")

    # When
    _ = pending.store.load()

    # Then
    _assert_prior_state(pending)
    assert not (
        pending.store.root / ".media-checkpoint/publication.json"
    ).exists()
