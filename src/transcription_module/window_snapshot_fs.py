# Copyright 2026 Transcription Module contributors

"""Immutable held-descriptor snapshots for policy window inputs."""

from __future__ import annotations

import hashlib
import os
import stat
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from contextlib import ExitStack
    from pathlib import Path

from transcription_module.window_evidence_errors import (
    WindowEvidenceError,
    fail_window_evidence,
)
from transcription_module.window_snapshot_models import (
    FileSnapshot,
    FileSnapshotSet,
    SnapshotMetadata,
    snapshot_metadata,
)

_OPEN_FILE: Final = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_READ_SIZE: Final = 1024 * 1024


@final
class SnapshotBuilder:
    """Accumulate descriptors while manifest-driven input paths are discovered."""

    __slots__: tuple[str, ...] = (
        "_entries",
        "_identities",
        "_paths",
        "_root",
        "_stack",
    )

    def __init__(self, run_dir: Path, stack: ExitStack) -> None:
        """Bind one non-aliased run root and its descriptor cleanup stack."""
        self._root: Path = run_dir.absolute()
        self._stack: ExitStack = stack
        self._entries: list[FileSnapshot] = []
        self._paths: dict[Path, str] = {}
        self._identities: dict[tuple[int, int], str] = {}
        try:
            resolved = self._root.resolve(strict=True)
        except OSError as error:
            message = "window snapshot run path is unsafe"
            raise WindowEvidenceError(message) from error
        if resolved != self._root:
            fail_window_evidence("window snapshot run path is a symlink or alias")

    @property
    def root(self) -> Path:
        """Return the lexical, non-aliased run root."""
        return self._root

    def required(
        self,
        label: str,
        path: Path,
        *,
        retain_payload: bool = True,
    ) -> FileSnapshot:
        """Capture one mandatory regular file and retain its descriptor."""
        snapshot = self._capture(
            label,
            path,
            retain_payload=retain_payload,
        )
        if snapshot is None:
            fail_window_evidence(f"window snapshot input is missing: {label}")
        return snapshot

    def optional(
        self,
        label: str,
        path: Path,
        *,
        retain_payload: bool = True,
    ) -> FileSnapshot | None:
        """Capture an optional regular file without a separate existence check."""
        return self._capture(
            label,
            path,
            retain_payload=retain_payload,
        )

    def freeze(self) -> FileSnapshotSet:
        """Expose the complete immutable descriptor set for barrier checks."""
        return FileSnapshotSet(tuple(self._entries))

    def _capture(
        self,
        label: str,
        path: Path,
        *,
        retain_payload: bool,
    ) -> FileSnapshot | None:
        confined = self._confined(path)
        prior_label = self._paths.get(confined)
        if prior_label is not None:
            message = (
                "duplicate snapshot identity for logically distinct inputs: "
                f"{prior_label}, {label}"
            )
            fail_window_evidence(message)
        snapshot = _read_snapshot(
            label,
            confined,
            retain_payload=retain_payload,
        )
        if snapshot is None:
            return None
        identity = (snapshot.metadata.device, snapshot.metadata.inode)
        prior_identity = self._identities.get(identity)
        if prior_identity is not None:
            os.close(snapshot.descriptor)
            message = (
                "duplicate snapshot identity for logically distinct inputs: "
                f"{prior_identity}, {label}"
            )
            fail_window_evidence(message)
        self._paths[confined] = label
        self._identities[identity] = label
        self._entries.append(snapshot)
        _ = self._stack.callback(os.close, snapshot.descriptor)
        return snapshot

    def _confined(self, path: Path) -> Path:
        if ".." in path.parts:
            fail_window_evidence("window snapshot path leaves its run")
        if path.is_absolute():
            confined = path.absolute()
        else:
            repository_relative = path.absolute()
            confined = (
                repository_relative
                if repository_relative.is_relative_to(self._root)
                else (self._root / path).absolute()
            )
        if not confined.is_relative_to(self._root):
            fail_window_evidence("window snapshot path leaves its run")
        return confined


def _read_snapshot(
    label: str,
    path: Path,
    *,
    retain_payload: bool,
) -> FileSnapshot | None:
    descriptor = _open_snapshot(path, label)
    if descriptor is None:
        return None
    keep_open = False
    try:
        resolved = _resolved_snapshot_path(path, label)
        before = _snapshot_metadata(descriptor, label)
        if resolved != path:
            fail_window_evidence(f"window snapshot path is a symlink or alias: {label}")
        if before.link_count != 1:
            fail_window_evidence(f"window snapshot input is hard-linked: {label}")
        digest, payload, after = _read_descriptor(
            descriptor,
            label,
            retain_payload=retain_payload,
        )
        if before != after:
            fail_window_evidence(f"window snapshot changed while reading: {label}")
        keep_open = True
        return FileSnapshot(
            label=label,
            path=path,
            descriptor=descriptor,
            metadata=after,
            sha256=digest,
            payload=payload,
        )
    finally:
        if not keep_open:
            os.close(descriptor)


def _open_snapshot(path: Path, label: str) -> int | None:
    try:
        return os.open(path, _OPEN_FILE)
    except FileNotFoundError:
        return None
    except OSError as error:
        message = f"window snapshot path is unsafe: {label}"
        raise WindowEvidenceError(message) from error


def _resolved_snapshot_path(path: Path, label: str) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as error:
        message = f"window snapshot path is unsafe: {label}"
        raise WindowEvidenceError(message) from error


def _snapshot_metadata(descriptor: int, label: str) -> SnapshotMetadata:
    try:
        observed = os.fstat(descriptor)
    except OSError as error:
        message = f"window snapshot metadata is unavailable: {label}"
        raise WindowEvidenceError(message) from error
    if not stat.S_ISREG(observed.st_mode):
        fail_window_evidence(f"window snapshot input is not regular: {label}")
    return snapshot_metadata(observed)


def _read_descriptor(
    descriptor: int,
    label: str,
    *,
    retain_payload: bool,
) -> tuple[str, bytes | None, SnapshotMetadata]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        while chunk := os.read(descriptor, _READ_SIZE):
            digest.update(chunk)
            if retain_payload:
                chunks.append(chunk)
        after = _snapshot_metadata(descriptor, label)
        _ = os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as error:
        message = f"window snapshot read failed: {label}"
        raise WindowEvidenceError(message) from error
    payload = b"".join(chunks) if retain_payload else None
    return digest.hexdigest(), payload, after


__all__ = ["FileSnapshot", "FileSnapshotSet", "SnapshotBuilder"]
