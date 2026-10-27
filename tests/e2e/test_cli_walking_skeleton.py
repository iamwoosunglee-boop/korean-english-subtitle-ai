# Copyright 2026 Transcription Module contributors

"""End-to-end contract for the deterministic walking skeleton."""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path

from transcription_module.models import (
    ProcessResult,
    StartResult,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.protected import ProtectedRunArtifact

PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "walking"


def _run_cli(
    *arguments: str,
    runs_dir: Path,
    fixture_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if fixture_dir is not None:
        environment["TRANSCRIPTION_FIXTURE_DIR"] = str(fixture_dir)
    return subprocess.run(
        ["uv", "run", "transcription-cli", *arguments, "--runs-dir", str(runs_dir)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_protected_run_exposes_authenticated_browser_option(
    tmp_path: Path,
) -> None:
    """Protected media preparation exposes the typed Chrome login source."""
    result = _run_cli(
        "protected-run",
        "--help",
        runs_dir=tmp_path / "runs",
    )

    assert result.returncode == 0, result.stderr
    assert "--youtube-browser" in result.stdout
    assert "chrome" in result.stdout


def test_cli_creates_pending_draft_only_after_context(tmp_path: Path) -> None:
    """Costly adapters run only after context and produce matching outputs."""
    runs_dir = tmp_path / "runs"

    started = _run_cli(
        "start",
        "https://www.youtube.com/watch?v=fixture",
        runs_dir=runs_dir,
        fixture_dir=FIXTURE_DIR,
    )

    assert started.returncode == 0, started.stderr
    start_result = StartResult.model_validate_json(started.stdout)
    assert start_result.workflow_status is WorkflowStatus.WAITING_CONTEXT
    run_id = start_result.run_id
    run_dir = runs_dir / str(run_id)
    assert not (run_dir / "adapter-events.jsonl").exists()
    assert not (run_dir / "transcript.json").exists()

    completed = _run_cli(
        "context",
        str(run_id),
        "--text",
        "없음",
        runs_dir=runs_dir,
        fixture_dir=FIXTURE_DIR,
    )

    assert completed.returncode == 0, completed.stderr
    context_result = ProcessResult.model_validate_json(completed.stdout)
    assert context_result.run_id == run_id
    assert (
        context_result.workflow_status
        is WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    )
    assert context_result.validation_status is ValidationStatus.PENDING

    transcript = Transcript.model_validate_json(
        (run_dir / "transcript.json").read_text(encoding="utf-8")
    )
    with (run_dir / "transcript.csv").open(encoding="utf-8", newline="") as csv_file:
        csv_rows = list(csv.reader(csv_file))

    speech_column = csv_rows[0].index("speech")
    assert (
        transcript.workflow_status
        is WorkflowStatus.WAITING_CONFIRMATION_CONSENT
    )
    assert transcript.validation_status is ValidationStatus.PENDING
    assert [cue.sequence for cue in transcript.cues] == [1, 2]
    assert [row[speech_column] for row in csv_rows[1:]] == [
        cue.speech for cue in transcript.cues
    ]
    assert transcript.cues[1].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert (run_dir / "adapter-events.jsonl").read_text(encoding="utf-8").splitlines()


def test_context_without_fixture_persists_context_for_real_stages(
    tmp_path: Path,
) -> None:
    """Real workflow context advances to processing without fixture adapters."""
    runs_dir = tmp_path / "runs"
    started = _run_cli(
        "start",
        "https://www.youtube.com/watch?v=fixture",
        runs_dir=runs_dir,
    )
    start_result = StartResult.model_validate_json(started.stdout)

    completed = _run_cli(
        "context",
        str(start_result.run_id),
        "--text",
        "주인공은 빨간 재킷",
        runs_dir=runs_dir,
    )

    assert completed.returncode == 0, completed.stderr
    result = ProcessResult.model_validate_json(completed.stdout)
    assert result.workflow_status is WorkflowStatus.PROCESSING
    assert result.validation_status is ValidationStatus.PENDING
    run_dir = runs_dir / str(start_result.run_id)
    assert not (run_dir / "transcript.json").exists()


def test_validate_rejects_divergent_csv(tmp_path: Path) -> None:
    """Validation detects when derived CSV no longer matches canonical JSON."""
    runs_dir = tmp_path / "runs"
    started = _run_cli(
        "start",
        "https://www.youtube.com/watch?v=fixture",
        runs_dir=runs_dir,
        fixture_dir=FIXTURE_DIR,
    )
    run_id = StartResult.model_validate_json(started.stdout).run_id
    completed = _run_cli(
        "context",
        str(run_id),
        "--text",
        "없음",
        runs_dir=runs_dir,
        fixture_dir=FIXTURE_DIR,
    )
    assert completed.returncode == 0, completed.stderr
    csv_path = runs_dir / str(run_id) / "transcript.csv"
    _ = csv_path.write_text("corrupt\n", encoding="utf-8")

    validated = _run_cli(
        "validate",
        str(run_id),
        runs_dir=runs_dir,
        fixture_dir=FIXTURE_DIR,
    )

    assert validated.returncode != 0
    assert '"code":"validation_failed"' in validated.stderr


def test_cli_fixture_run_records_unchanged_source_fingerprint(
    tmp_path: Path,
) -> None:
    """Protected fixture runs persist equal source fingerprints."""
    runs_dir = tmp_path / "runs"
    started = _run_cli(
        "start",
        "https://www.youtube.com/watch?v=fixture",
        runs_dir=runs_dir,
    )
    run_id = StartResult.model_validate_json(started.stdout).run_id

    completed = _run_cli(
        "protected-run",
        str(run_id),
        "--context",
        "없음",
        "--fixture-dir",
        str(FIXTURE_DIR),
        runs_dir=runs_dir,
    )

    assert completed.returncode == 0, completed.stderr
    artifact = ProtectedRunArtifact.model_validate_json(completed.stdout)
    assert artifact.status == "succeeded"
    assert artifact.source_changed is False
    assert artifact.start_fingerprint == artifact.end_fingerprint
    assert (
        runs_dir / str(run_id) / "protected-run.json"
    ).read_text(encoding="utf-8")
