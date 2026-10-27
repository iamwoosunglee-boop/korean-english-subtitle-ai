# Copyright 2026 Transcription Module contributors

"""Typed episode-context CLI boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from transcription_module.errors import ErrorEnvelope
from transcription_module.models import EpisodeContext, RunState, StartResult

PROJECT_ROOT = Path(__file__).parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
VALID_CONTEXT = FIXTURES / "episode-context-four-person.v2.json"
FOREIGN_CONTEXT = FIXTURES / "episode-context-foreign-name.v2.json"
EXIT_TYPED_ERROR = 2
EXPECTED_ROSTER_SIZE = 4
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def _run_cli(
    *arguments: str,
    runs_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "transcription-cli",
            *arguments,
            "--runs-dir",
            str(runs_dir),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _start(runs_dir: Path) -> StartResult:
    result = _run_cli(
        "start",
        "https://www.youtube.com/watch?v=context-fixture",
        runs_dir=runs_dir,
    )
    assert result.returncode == 0, result.stderr
    return StartResult.model_validate_json(result.stdout)


def _error(result: subprocess.CompletedProcess[str]) -> ErrorEnvelope:
    return ErrorEnvelope.model_validate_json(result.stderr)


def test_context_file_persists_typed_four_person_roster(tmp_path: Path) -> None:
    """Persist the exact validated file model through the real workflow."""
    runs_dir = tmp_path / "runs"
    started = _start(runs_dir)

    result = _run_cli(
        "context",
        str(started.run_id),
        "--context-file",
        str(VALID_CONTEXT),
        runs_dir=runs_dir,
    )

    assert result.returncode == 0, result.stderr
    state = RunState.model_validate_json(
        (runs_dir / str(started.run_id) / "state.json").read_bytes()
    )
    expected = EpisodeContext.model_validate_json(VALID_CONTEXT.read_bytes())
    assert state.context == expected
    stored = _JSON_OBJECT.validate_python(expected.model_dump(mode="json"))
    roster = TypeAdapter(list[JsonValue]).validate_python(stored["roster"])
    assert len(roster) == EXPECTED_ROSTER_SIZE


def test_context_text_preserves_legacy_behavior(tmp_path: Path) -> None:
    """Keep the existing free-text command as a legacy v1 context."""
    runs_dir = tmp_path / "runs"
    started = _start(runs_dir)

    result = _run_cli(
        "context",
        str(started.run_id),
        "--text",
        "주인공은 빨간 재킷",
        runs_dir=runs_dir,
    )

    assert result.returncode == 0, result.stderr
    state = RunState.model_validate_json(
        (runs_dir / str(started.run_id) / "state.json").read_bytes()
    )
    assert state.context == EpisodeContext(raw_text="주인공은 빨간 재킷")


def test_context_requires_exactly_one_typed_source(tmp_path: Path) -> None:
    """Reject missing or competing context sources before state writes."""
    for label, options in (
        ("missing", ()),
        ("both", ("--text", "없음", "--context-file", str(VALID_CONTEXT))),
    ):
        runs_dir = tmp_path / label / "runs"
        started = _start(runs_dir)
        state_path = runs_dir / str(started.run_id) / "state.json"
        before = state_path.read_bytes()

        result = _run_cli(
            "context",
            str(started.run_id),
            *options,
            runs_dir=runs_dir,
        )

        assert result.returncode == EXIT_TYPED_ERROR
        assert _error(result).error.code.value == "invalid_episode_context"
        assert state_path.read_bytes() == before


def test_invalid_context_file_preserves_state_bytes(tmp_path: Path) -> None:
    """Reject foreign names and stale digests without touching the run."""
    stale_payload = _JSON_OBJECT.validate_json(VALID_CONTEXT.read_bytes())
    stale_payload["context_digest"] = "f" * 64
    stale_path = tmp_path / "stale-context.json"
    _ = stale_path.write_text(
        json.dumps(stale_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    for label, context_path in (
        ("foreign", FOREIGN_CONTEXT),
        ("stale", stale_path),
    ):
        runs_dir = tmp_path / label / "runs"
        started = _start(runs_dir)
        state_path = runs_dir / str(started.run_id) / "state.json"
        before = state_path.read_bytes()

        result = _run_cli(
            "context",
            str(started.run_id),
            "--context-file",
            str(context_path),
            runs_dir=runs_dir,
        )

        assert result.returncode == EXIT_TYPED_ERROR
        assert _error(result).error.code.value == "invalid_episode_context"
        assert state_path.read_bytes() == before
