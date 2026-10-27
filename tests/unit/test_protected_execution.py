# Copyright 2026 Transcription Module contributors

"""Protected runs permit artifacts while rejecting source hot-fixes."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest

from transcription_module.protected import (
    ProtectedExecution,
    ProtectedRunArtifact,
    SourceChangedError,
    run_protected,
)

if TYPE_CHECKING:
    from pathlib import Path


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    source = project / "src" / "product.py"
    source.parent.mkdir(parents=True)
    _ = source.write_text("VALUE = 1\n", encoding="utf-8")
    tests = project / "tests" / "test_product.py"
    tests.parent.mkdir()
    _ = tests.write_text("def test_value(): pass\n", encoding="utf-8")
    _ = (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    artifacts = project / "runs" / "fixture"
    return project, artifacts


def test_protected_run_writes_artifacts_and_restores_cleanup(
    tmp_path: Path,
) -> None:
    """Artifact writes succeed while source writes fail and cleanup runs."""
    project, artifacts = _project(tmp_path)
    cleaned: list[str] = []

    def action(execution: ProtectedExecution) -> None:
        _ = execution.register_cleanup(lambda: cleaned.append("monitor-stopped"))
        _ = artifacts.mkdir(parents=True, exist_ok=True)
        _ = (artifacts / "result.json").write_text("{}\n", encoding="utf-8")
        with pytest.raises(PermissionError):
            _ = (project / "src" / "product.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )

    result = run_protected(project, artifacts, action)

    assert result.status == "succeeded"
    assert result.start_fingerprint == result.end_fingerprint
    assert result.source_changed is False
    assert cleaned == ["monitor-stopped"]
    persisted = ProtectedRunArtifact.model_validate_json(
        (artifacts / "protected-run.json").read_text(encoding="utf-8")
    )
    assert persisted == result


def test_protected_run_detects_source_change_and_still_cleans_up(
    tmp_path: Path,
) -> None:
    """A source hot-fix fails the run after cleanup and records evidence."""
    project, artifacts = _project(tmp_path)
    source = project / "src" / "product.py"
    cleaned: list[str] = []

    def hot_fix(execution: ProtectedExecution) -> None:
        _ = execution.register_cleanup(lambda: cleaned.append("monitor-stopped"))
        _ = source.chmod(source.stat().st_mode | stat.S_IWUSR)
        _ = source.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(SourceChangedError):
        _ = run_protected(project, artifacts, hot_fix)

    assert cleaned == ["monitor-stopped"]
    artifact = ProtectedRunArtifact.model_validate_json(
        (artifacts / "protected-run.json").read_text(encoding="utf-8")
    )
    assert artifact.status == "failed"
    assert artifact.source_changed is True
