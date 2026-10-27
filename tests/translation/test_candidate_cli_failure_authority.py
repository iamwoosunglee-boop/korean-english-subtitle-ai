# Copyright 2026 Transcription Module contributors

"""Legacy candidate CLI failed-attempt authority."""

import subprocess
from pathlib import Path

from tests.translation.candidate_authority_test_support import (
    assembled_candidate_fixture,
    candidate_source_fixture,
)
from translation_module.canonical.attempts import TranslationAttemptStore
from translation_module.canonical.legacy_authority import project_legacy_candidate


def test_cli_records_failed_attempt_without_candidate_claims(tmp_path: Path) -> None:
    """The explicit failure lifecycle appends no candidate or rationale fields."""
    authority_root = tmp_path / "failed-authority"
    projected = project_legacy_candidate(
        candidate_source_fixture(), assembled_candidate_fixture(), authority_root
    ).candidate
    candidate_path = tmp_path / "canonical-candidate.json"
    _ = candidate_path.write_text(projected.model_dump_json(), encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "translation-cli",
            "record-failed-attempt",
            str(candidate_path),
            "--authority-root",
            str(authority_root),
            "--attempt",
            "failed-attempt",
            "retry",
            "-",
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    payload = (
        TranslationAttemptStore(authority_root / "attempts")
        .load()[0]
        .model_dump(mode="json", exclude_none=True)
    )
    assert {"candidate_digest", "rationales"}.isdisjoint(payload)
