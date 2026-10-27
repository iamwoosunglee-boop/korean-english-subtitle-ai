# Copyright 2026 Transcription Module contributors

"""Canonical v2 candidate-attempt CLI regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import TypeAdapter

from tests.translation.canonical_attempt_support import (
    attempt_document,
    attempt_rationales,
)
from translation_module.canonical.attempts import (
    TranslationAttemptOutcome,
    TranslationAttemptStore,
    TranslationLineRationale,
)

PROJECT_ROOT = Path(__file__).parents[2]
CLI_ERROR_EXIT = 2
SOURCE_EXAMPLE = (
    PROJECT_ROOT / "docs/contracts/examples/source-transcript.v2.example.json"
)


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "translation-cli", *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_record_candidate_attempt_accepts_canonical_v2_source(
    tmp_path: Path,
) -> None:
    """A canonical v2 source and candidate append one successful attempt."""
    # Given
    source_path = tmp_path / "source.json"
    candidate_path = tmp_path / "candidate.json"
    rationales_path = tmp_path / "rationales.json"
    authority_root = tmp_path / "authority"
    _ = source_path.write_bytes(SOURCE_EXAMPLE.read_bytes())
    candidate = attempt_document()
    _ = candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    _ = rationales_path.write_text(
        TypeAdapter(tuple[TranslationLineRationale, ...])
        .dump_json(attempt_rationales(candidate))
        .decode(),
        encoding="utf-8",
    )

    # When
    result = _run(
        "record-candidate-attempt",
        str(source_path),
        str(candidate_path),
        "--authority-root",
        str(authority_root),
        "--attempt",
        "canonical-v2-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationales_path),
    )

    # Then
    assert result.returncode == 0, result.stderr
    attempts = TranslationAttemptStore(authority_root / "attempts").load()
    assert len(attempts) == 1
    assert attempts[0].attempt_id == "canonical-v2-attempt"
    assert attempts[0].outcome is TranslationAttemptOutcome.SUCCEEDED
    assert attempts[0].candidate_digest == candidate.content_digest


def test_record_candidate_attempt_leaves_no_candidate_when_rationales_fail(
    tmp_path: Path,
) -> None:
    """An invalid canonical attempt must not persist any authority bytes."""
    # Given
    source_path = tmp_path / "source.json"
    candidate_path = tmp_path / "candidate.json"
    rationales_path = tmp_path / "rationales.json"
    authority_root = tmp_path / "authority"
    _ = source_path.write_bytes(SOURCE_EXAMPLE.read_bytes())
    candidate = attempt_document()
    _ = candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    _ = rationales_path.write_text(
        TypeAdapter(tuple[TranslationLineRationale, ...])
        .dump_json(attempt_rationales(candidate)[:-1])
        .decode(),
        encoding="utf-8",
    )

    # When
    result = _run(
        "record-candidate-attempt",
        str(source_path),
        str(candidate_path),
        "--authority-root",
        str(authority_root),
        "--attempt",
        "canonical-v2-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationales_path),
    )

    # Then
    assert result.returncode == CLI_ERROR_EXIT
    assert not tuple(authority_root.rglob("*"))


def test_record_candidate_attempt_leaves_no_candidate_when_parent_is_unknown(
    tmp_path: Path,
) -> None:
    """A ledger-invalid canonical attempt must not persist candidate bytes."""
    # Given
    source_path = tmp_path / "source.json"
    candidate_path = tmp_path / "candidate.json"
    rationales_path = tmp_path / "rationales.json"
    authority_root = tmp_path / "authority"
    _ = source_path.write_bytes(SOURCE_EXAMPLE.read_bytes())
    candidate = attempt_document()
    _ = candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    _ = rationales_path.write_text(
        TypeAdapter(tuple[TranslationLineRationale, ...])
        .dump_json(attempt_rationales(candidate))
        .decode(),
        encoding="utf-8",
    )

    # When
    result = _run(
        "record-candidate-attempt",
        str(source_path),
        str(candidate_path),
        "--authority-root",
        str(authority_root),
        "--attempt",
        "canonical-v2-retry",
        "retry",
        "missing-parent",
        "--rationales",
        str(rationales_path),
    )

    # Then
    assert result.returncode == CLI_ERROR_EXIT
    assert not tuple(authority_root.rglob("*"))
