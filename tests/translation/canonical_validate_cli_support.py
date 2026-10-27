# Copyright 2026 Transcription Module contributors

"""Shared helpers for canonical validation CLI tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from translation_module.canonical.digest import canonical_json_bytes

if TYPE_CHECKING:
    from translation_module.canonical.common import CanonicalModel

PROJECT_ROOT = Path(__file__).parents[2]
CLI_ERROR_EXIT = 2


def write_artifact(path: Path, artifact: CanonicalModel) -> None:
    """Write exact canonical bytes for one dependency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(canonical_json_bytes(artifact))


def run_validate(
    source_path: Path,
    document_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the real validate command from the project root."""
    return subprocess.run(
        [
            "uv",
            "run",
            "translation-cli",
            "validate",
            str(source_path),
            str(document_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


__all__ = [
    "CLI_ERROR_EXIT",
    "run_validate",
    "write_artifact",
]
