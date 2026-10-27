# Copyright 2026 Transcription Module contributors

"""Immutable file bindings held through one window-drafting transaction."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transcription_module.window_evidence_errors import (
    WindowEvidenceError,
    fail_window_evidence,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    """Filesystem fields that must remain stable for one captured file."""

    device: int
    inode: int
    link_count: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One regular file held open after bytes were captured from its descriptor."""

    label: str
    path: Path
    descriptor: int
    metadata: SnapshotMetadata
    sha256: str
    payload: bytes | None

    def assert_bound(self) -> None:
        """Require the visible path and held descriptor to retain exact identity."""
        try:
            visible = self.path.stat(follow_symlinks=False)
            held = os.fstat(self.descriptor)
        except OSError as error:
            message = f"window snapshot path was replaced or changed: {self.label}"
            raise WindowEvidenceError(message) from error
        if (
            not stat.S_ISREG(visible.st_mode)
            or snapshot_metadata(visible) != self.metadata
            or snapshot_metadata(held) != self.metadata
        ):
            fail_window_evidence(
                f"window snapshot path was replaced or changed: {self.label}"
            )

    def require_payload(self) -> bytes:
        """Return retained semantic bytes for a text or model snapshot."""
        if self.payload is None:
            fail_window_evidence(
                f"window snapshot retained no semantic bytes: {self.label}"
            )
        return self.payload


@dataclass(frozen=True, slots=True)
class FileSnapshotSet:
    """All distinct input descriptors held for one drafting transaction."""

    files: tuple[FileSnapshot, ...]

    def assert_bound(self) -> None:
        """Run one exact path-identity barrier across every held input."""
        for snapshot in self.files:
            snapshot.assert_bound()


def snapshot_metadata(value: os.stat_result) -> SnapshotMetadata:
    """Project an OS stat result into the exact stable binding fields."""
    return SnapshotMetadata(
        device=value.st_dev,
        inode=value.st_ino,
        link_count=value.st_nlink,
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
    )


__all__ = [
    "FileSnapshot",
    "FileSnapshotSet",
    "SnapshotMetadata",
    "snapshot_metadata",
]
