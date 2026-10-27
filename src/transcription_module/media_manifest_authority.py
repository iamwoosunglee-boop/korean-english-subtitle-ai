# Copyright 2026 Transcription Module contributors

"""Run-locked publication authority for prepared-media manifests."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import TYPE_CHECKING

from transcription_module.storage import write_text_atomic

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.media import PreparedMediaManifest


class ManifestWithdrawalPoint(StrEnum):
    """Deterministic interruption seams around durable manifest withdrawal."""

    BEFORE_UNLINK = "before_unlink"
    AFTER_UNLINK = "after_unlink"


def manifest_withdrawal_fault(point: ManifestWithdrawalPoint) -> None:
    """Do nothing unless a deterministic test replaces this hook."""
    del point


def withdraw_media_manifest(path: Path, *, repair_required: bool) -> None:
    """Durably revoke prior authority before any checkpoint repair mutation."""
    if not repair_required or not path.exists():
        return
    manifest_withdrawal_fault(ManifestWithdrawalPoint.BEFORE_UNLINK)
    path.unlink()
    _sync_directory(path.parent)
    manifest_withdrawal_fault(ManifestWithdrawalPoint.AFTER_UNLINK)


def publish_media_manifest(
    path: Path,
    manifest: PreparedMediaManifest,
) -> PreparedMediaManifest:
    """Publish complete authority once, preserving exact committed replay bytes."""
    payload = f"{manifest.model_dump_json(indent=2)}\n".encode()
    if path.is_file() and path.read_bytes() == payload:
        return manifest
    write_text_atomic(path, payload.decode())
    return manifest


def _sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
