# Copyright 2026 Transcription Module contributors

"""Held-descriptor namespace bindings for checkpoint roots."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from transcription_module.media_checkpoint_models import CheckpointStorageError

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_OPEN_DIR: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_UNSAFE_ROOT: Final = "unsafe checkpoint root"
_NAMESPACE_CHANGED: Final = "checkpoint namespace binding changed"


@dataclass(frozen=True, slots=True)
class InodeBinding:
    """Stable kernel identity for one held inode."""

    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class NamespaceEdge:
    """One visible parent/name edge and its held child descriptor."""

    parent_fd: int
    name: str
    child_fd: int


@dataclass(frozen=True, slots=True)
class RootNamespace:
    """A component-opened root plus every held edge from its anchor."""

    root_fd: int
    edges: tuple[NamespaceEdge, ...]

    def assert_bound(self) -> None:
        """Require every visible edge to still name its held inode."""
        for edge in self.edges:
            assert_name_binding(edge.parent_fd, edge.name, edge.child_fd)


@contextmanager
def opened_root_namespace(root: Path) -> Generator[RootNamespace]:
    """Open a root component-by-component without following any symlink."""
    components = _components(root)
    anchor_name = "/" if root.is_absolute() else "."
    with ExitStack() as stack:
        try:
            anchor_fd = os.open(anchor_name, _OPEN_DIR)
            _ = stack.callback(os.close, anchor_fd)
            current_fd = anchor_fd
            edges: list[NamespaceEdge] = []
            for name in components:
                child_fd = os.open(name, _OPEN_DIR, dir_fd=current_fd)
                _ = stack.callback(os.close, child_fd)
                edges.append(NamespaceEdge(current_fd, name, child_fd))
                current_fd = child_fd
        except OSError as error:
            raise CheckpointStorageError(_UNSAFE_ROOT) from error
        namespace = RootNamespace(current_fd, tuple(edges))
        namespace.assert_bound()
        yield namespace


def inode_binding(descriptor: int) -> InodeBinding:
    """Return the device/inode pair bound to one held descriptor."""
    observed = os.fstat(descriptor)
    return InodeBinding(observed.st_dev, observed.st_ino)


def assert_name_binding(parent_fd: int, name: str, child_fd: int) -> None:
    """Require a visible no-follow name to identify one held descriptor."""
    try:
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        held = os.fstat(child_fd)
    except OSError as error:
        raise CheckpointStorageError(_NAMESPACE_CHANGED) from error
    if (
        not stat.S_ISDIR(visible.st_mode)
        or visible.st_dev != held.st_dev
        or visible.st_ino != held.st_ino
    ):
        raise CheckpointStorageError(_NAMESPACE_CHANGED)


def assert_file_binding(parent_fd: int, name: str, child_fd: int) -> None:
    """Require a visible regular-file name to identify a held descriptor."""
    try:
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        held = os.fstat(child_fd)
    except OSError as error:
        raise CheckpointStorageError(_NAMESPACE_CHANGED) from error
    if (
        not stat.S_ISREG(visible.st_mode)
        or visible.st_dev != held.st_dev
        or visible.st_ino != held.st_ino
    ):
        raise CheckpointStorageError(_NAMESPACE_CHANGED)


def _components(root: Path) -> tuple[str, ...]:
    parts = root.parts[1:] if root.is_absolute() else root.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        if not parts and str(root) in {".", "/"}:
            return ()
        raise CheckpointStorageError(_UNSAFE_ROOT)
    return tuple(parts)
