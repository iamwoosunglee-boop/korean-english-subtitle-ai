# Copyright 2026 Transcription Module contributors

"""Strict receipt parsing and descriptor-confined checkpoint paths."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

import transcription_module._media_checkpoint_publication as publication_module
from tests.integration.media_checkpoint_support import (
    CONTRACTS,
    SOURCE_URL,
    stage_files,
)
from transcription_module._media_checkpoint_publication import FaultPoint
from transcription_module.media_checkpoint import (
    CheckpointStorageError,
    MediaCheckpoint,
    MediaCheckpointStore,
    StageName,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "video_json",
    [
        f"""{{
            "stage": "video",
            "status": "mystery",
            "input_fingerprint": {json.dumps("a" * 64)},
            "outputs": [{{
                "path": "media/video/source.mp4",
                "sha256": {json.dumps("b" * 64)}
            }}],
            "adapter_contract_version": "yt-dlp.v1",
            "completed_at": "2026-08-18T01:02:03Z",
            "sampling_profile": null
        }}""",
        f"""{{
            "stage": "video",
            "status": "succeeded",
            "input_fingerprint": "bad-digest",
            "outputs": [{{
                "path": "media/video/source.mp4",
                "sha256": {json.dumps("b" * 64)}
            }}],
            "adapter_contract_version": "yt-dlp.v1",
            "completed_at": "2026-08-18T01:02:03Z",
            "sampling_profile": null
        }}""",
        f"""{{
            "stage": "video",
            "status": "succeeded",
            "input_fingerprint": {json.dumps("a" * 64)},
            "outputs": [{{
                "path": "../escape",
                "sha256": {json.dumps("b" * 64)}
            }}],
            "adapter_contract_version": "yt-dlp.v1",
            "completed_at": "2026-08-18T01:02:03Z",
            "sampling_profile": null
        }}""",
    ],
)
def test_malformed_receipt_is_rejected(video_json: str) -> None:
    """Untrusted persisted receipt fields fail strict boundary parsing."""
    # Given
    payload = f'{{"schema_version":"media-checkpoint.v1","video":{video_json}}}'

    # When / Then
    with pytest.raises(ValidationError):
        _ = MediaCheckpoint.model_validate_json(payload)


def test_parent_symlink_cannot_redirect_pending_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T7-AV-004: fixed output traversal never follows a parent symlink."""
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    victim = outside / "source.mp4"
    _ = victim.write_bytes(b"outside")
    store = MediaCheckpointStore(root)
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
    media = root / "media"
    backup = root / "media-backup"
    _ = media.rename(backup)
    media.symlink_to(outside, target_is_directory=True)

    # When / Then
    with pytest.raises(CheckpointStorageError, match="unsafe"):
        _ = store.load()
    assert victim.read_bytes() == b"outside"
    assert (store.root / ".media-checkpoint/publication.json").exists()


def test_journal_parent_symlink_is_rejected_without_outside_read(
    tmp_path: Path,
) -> None:
    """The private journal parent must be a real directory below the root."""
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    marker = outside / "publication.json"
    _ = marker.write_text(json.dumps({"attacker": True}), encoding="utf-8")
    (root / ".media-checkpoint").symlink_to(outside, target_is_directory=True)

    # When / Then
    with pytest.raises(CheckpointStorageError, match="unsafe"):
        _ = MediaCheckpointStore(root).load()
    assert marker.read_text(encoding="utf-8") == '{"attacker": true}'


def test_staging_parent_symlink_cannot_supply_outside_bytes(tmp_path: Path) -> None:
    """A staged adapter output must remain under the fixed staging parent."""
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _ = (outside / "source.mp4").write_bytes(b"outside")
    (root / ".media-staging").symlink_to(outside, target_is_directory=True)
    store = MediaCheckpointStore(root)

    # When / Then
    with (
        pytest.raises(CheckpointStorageError, match="unsafe"),
        store.open_stage_file(StageName.VIDEO),
    ):
        pass
    assert (outside / "source.mp4").read_bytes() == b"outside"


def test_final_output_symlink_is_rejected_without_reading_target(
    tmp_path: Path,
) -> None:
    """A final symlink can never satisfy a persisted output receipt."""
    # Given
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    store = MediaCheckpointStore(root)
    completion = stage_files(
        store,
        StageName.VIDEO,
        (("source.mp4", b"video"),),
    )
    _ = store.publish_video(SOURCE_URL, completion)
    output = (store.root / "media/video") / "source.mp4"
    output.unlink()
    victim = outside / "source.mp4"
    _ = victim.write_bytes(b"outside")
    output.symlink_to(victim)

    # When / Then
    with pytest.raises(CheckpointStorageError, match="unsafe"):
        _ = store.validate(SOURCE_URL, CONTRACTS)
    assert victim.read_bytes() == b"outside"
