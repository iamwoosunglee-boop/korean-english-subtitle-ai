# Copyright 2026 Transcription Module contributors

"""Real translation bridge route for explicit reviewer frame opens."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.translation.evidence_open_support import (
    FRAME_A,
    candidate_fixture,
    source_fixture,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.cli import app
from translation_module.evidence import FileEvidenceResolver
from translation_module.evidence_open_preparation import OpenedReviewRequest

RUNNER = CliRunner()
CLI_ERROR_EXIT = 2
SESSIONS = ("translator", "reviser", "assembler", "reviewer")


def _artifacts(root: Path) -> tuple[Path, Path]:
    frame = root / "frames" / "a.jpg"
    frame.parent.mkdir()
    _ = frame.write_bytes(FRAME_A)
    source = source_fixture()
    first = source.evidence.entries[0].model_copy(update={"locator": "frames/a.jpg"})
    source = bind_canonical_digests(
        source.model_copy(
            update={
                "evidence": source.evidence.model_copy(
                    update={
                        "base_uri": root.as_uri(),
                        "entries": (first, *source.evidence.entries[1:]),
                    }
                )
            }
        )
    )
    candidate = candidate_fixture(source)
    source_path, candidate_path = root / "source.json", root / "candidate.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    return source_path, candidate_path


def _prepare_args(root: Path, source: Path, candidate: Path) -> tuple[str, ...]:
    return (
        "prepare-review",
        str(source),
        str(candidate),
        "--evidence-open-root",
        str(root / "opens"),
        "--output",
        str(root / "request.json"),
        "--assignment-output",
        str(root / "assignment.json"),
        "--runtime-session-ids",
        *SESSIONS,
    )


def test_prepare_review_without_open_record_fails_without_frame_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real bridge fails closed before any manifest frame read."""
    source, candidate = _artifacts(tmp_path)
    calls: list[str] = []

    def forbidden_read(
        _resolver: FileEvidenceResolver,
        _base_uri: str,
        locator: str,
    ) -> bytes:
        calls.append(locator)
        return FRAME_A

    monkeypatch.setattr(FileEvidenceResolver, "read", forbidden_read)

    result = RUNNER.invoke(app, _prepare_args(tmp_path, source, candidate))

    assert result.exit_code == CLI_ERROR_EXIT
    assert "not explicitly opened" in result.stderr
    assert calls == []


def test_open_frame_then_prepare_review_uses_persisted_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real bridge opens once, then assembles without rereading the frame."""
    source, candidate = _artifacts(tmp_path)
    opened = RUNNER.invoke(
        app,
        (
            "open-frame",
            str(source),
            str(candidate),
            "frame-a",
            "--actor-role",
            "reviewer",
            "--consumer-id",
            "reviewer",
            "--evidence-root",
            str(tmp_path),
            "--evidence-open-root",
            str(tmp_path / "opens"),
        ),
    )
    assert opened.exit_code == 0, opened.stderr
    direct_reads: list[str] = []

    def forbidden_read(
        _resolver: FileEvidenceResolver,
        _base_uri: str,
        locator: str,
    ) -> bytes:
        direct_reads.append(locator)
        return FRAME_A

    monkeypatch.setattr(FileEvidenceResolver, "read", forbidden_read)

    prepared = RUNNER.invoke(app, _prepare_args(tmp_path, source, candidate))

    assert prepared.exit_code == 0, prepared.stderr
    assert direct_reads == []
    request = OpenedReviewRequest.model_validate_json(
        (tmp_path / "request.json").read_bytes()
    )
    assert tuple(record.evidence_id for record in request.evidence) == ("frame-a",)
    assert request.reviewer_id == "reviewer"
    assert "rationale" not in request.model_dump()
