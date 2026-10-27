# Copyright 2026 Transcription Module contributors

"""Read-only source guard for reproducible fixture and real-media runs."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable
from contextlib import ExitStack
from typing import TYPE_CHECKING, final

from transcription_module.models import FrozenModel
from transcription_module.storage import write_model_atomic

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType
    from typing import Self

ProtectedAction = Callable[["ProtectedExecution"], None]
SKIPPED_PARTS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache"})
PROTECTED_DIRECTORIES = ("src", "tests", ".agents")
PROTECTED_CONFIGS = (
    "pyproject.toml",
    "uv.lock",
    "CONTEXT.md",
)


class SourceChangedError(Exception):
    """A protected run changed source despite the write guard."""


class ProtectedRunArtifact(FrozenModel):
    """Source fingerprints and terminal status for one protected run."""

    start_fingerprint: str
    end_fingerprint: str
    source_changed: bool
    status: str
    error: str | None = None


@final
class ProtectedExecution:
    """Register background-resource cleanup for the protected action."""

    def __init__(self, cleanups: ExitStack) -> None:
        """Use an exit stack that runs even when the action fails."""
        self._cleanups = cleanups

    def register_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Run cleanup even when the action or fingerprint check fails."""
        _ = self._cleanups.callback(cleanup)


@final
class _ProtectedSourceGuard:
    def __init__(self, project_root: Path, artifact_dir: Path) -> None:
        self.project_root = project_root
        self.artifact_dir = artifact_dir
        self.start = ""
        self.modes: tuple[tuple[Path, int], ...] = ()
        self.artifact: ProtectedRunArtifact | None = None

    def __enter__(self) -> Self:
        self.start = source_fingerprint(self.project_root, self.artifact_dir)
        self.modes = _make_source_read_only(
            self.project_root,
            self.artifact_dir,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        _restore_modes(self.modes)
        end = source_fingerprint(self.project_root, self.artifact_dir)
        changed = self.start != end
        source_error = (
            SourceChangedError("source changed during protected execution")
            if changed
            else None
        )
        terminal_error = source_error if source_error is not None else error
        self.artifact = ProtectedRunArtifact(
            start_fingerprint=self.start,
            end_fingerprint=end,
            source_changed=changed,
            status="failed" if terminal_error is not None else "succeeded",
            error=str(terminal_error) if terminal_error is not None else None,
        )
        write_model_atomic(
            self.artifact_dir / "protected-run.json",
            self.artifact,
        )
        if source_error is not None:
            raise source_error from error
        return False


def run_protected(
    project_root: Path,
    artifact_dir: Path,
    action: ProtectedAction,
) -> ProtectedRunArtifact:
    """Allow artifact writes while source remains read-only and unchanged."""
    project_root = project_root.resolve()
    artifact_dir = artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    guard = _ProtectedSourceGuard(project_root, artifact_dir)
    with guard, ExitStack() as cleanups:
        action(ProtectedExecution(cleanups))
    if guard.artifact is None:
        message = "protected source guard did not produce an artifact"
        raise RuntimeError(message)
    return guard.artifact


def source_fingerprint(project_root: Path, artifact_dir: Path) -> str:
    """Hash relative paths and bytes outside allowed run artifacts."""
    digest = hashlib.sha256()
    for path in _source_files(project_root, artifact_dir):
        relative = path.relative_to(project_root)
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_files(
    project_root: Path,
    artifact_dir: Path,
) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(_protected_paths(project_root))
        if path.is_file()
        and not _is_inside(path, artifact_dir)
        and not any(part in SKIPPED_PARTS for part in path.parts)
    )


def _make_source_read_only(
    project_root: Path,
    artifact_dir: Path,
) -> tuple[tuple[Path, int], ...]:
    paths = [
        path
        for path in _protected_paths(project_root)
        if not _is_inside(path, artifact_dir)
        and not any(part in SKIPPED_PARTS for part in path.parts)
    ]
    paths.append(project_root)
    modes: list[tuple[Path, int]] = []
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        mode = stat.S_IMODE(path.stat().st_mode)
        modes.append((path, mode))
        path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    return tuple(modes)


def _restore_modes(modes: tuple[tuple[Path, int], ...]) -> None:
    for path, mode in reversed(modes):
        path.chmod(mode)


def _is_inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _protected_paths(project_root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for name in PROTECTED_DIRECTORIES:
        root = project_root / name
        if root.exists():
            paths.append(root)
            paths.extend(root.rglob("*"))
    paths.extend(
        path
        for name in PROTECTED_CONFIGS
        if (path := project_root / name).exists()
    )
    return tuple(paths)
