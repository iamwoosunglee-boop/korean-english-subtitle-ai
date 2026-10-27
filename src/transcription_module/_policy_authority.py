# Copyright 2026 Transcription Module contributors

"""Bounded policy-dependent authority path discovery."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

POLICY_DEPENDENT_FILE_NAMES: Final = (
    "visual-evidence-policy.json",
    "subtitle-inventory.json",
    "vad.json",
    "visual-observations.json",
    "correction-receipts.json",
    "cue-provenance.json",
    "window-visual-review.json",
    "window-drafts.json",
    "whole-transcript.json",
    "speaker-inference.json",
    "transcript.json",
    "transcript.csv",
    "confirmation-session.json",
    "confirmations.jsonl",
    "final-review-assignment.json",
    "final-review-result.json",
    "final-review.json",
    "final-review-application-journal.json",
    "validation.json",
    "context-observations.json",
    "context.input.json",
    "source.json",
    "vision.json",
    "recovery.json",
    "unpublished-translation-trial.json",
)
POLICY_DEPENDENT_DIRECTORY_NAMES: Final = (
    "final-review-proposals",
    "final-review-decisions",
    "unpublished-translation-trial",
)


def policy_dependent_relative_paths(run_dir: Path) -> tuple[str, ...]:
    """Return fixed files plus regular entries below bounded authority roots."""
    result: list[str] = list(POLICY_DEPENDENT_FILE_NAMES)
    for name in POLICY_DEPENDENT_DIRECTORY_NAMES:
        root = run_dir / name
        if root.is_symlink():
            message = f"policy-dependent authority root is a symlink: {root}"
            raise OSError(message)
        if not root.exists():
            continue
        if not root.is_dir():
            message = f"policy-dependent authority root is not a directory: {root}"
            raise OSError(message)
        for candidate in sorted(root.rglob("*")):
            metadata = candidate.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                message = f"policy-dependent authority is not regular: {candidate}"
                raise OSError(message)
            result.append(candidate.relative_to(run_dir).as_posix())
    return tuple(result)


def remove_empty_policy_authority_directories(run_dir: Path) -> None:
    """Remove bounded authority roots after their files are withdrawn."""
    for name in POLICY_DEPENDENT_DIRECTORY_NAMES:
        root = run_dir / name
        if not root.exists():
            continue
        directories = sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            directory.rmdir()
        root.rmdir()


__all__ = [
    "POLICY_DEPENDENT_DIRECTORY_NAMES",
    "POLICY_DEPENDENT_FILE_NAMES",
    "policy_dependent_relative_paths",
    "remove_empty_policy_authority_directories",
]
