# Copyright 2026 Transcription Module contributors

"""Production CLI attempt-authority regressions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pytest

from tests.translation import test_cli as cli_test_support
from tests.translation.type_builders import milliseconds
from translation_module.candidate import ChunkCandidate
from translation_module.canonical.attempts import TranslationAttemptStore
from translation_module.canonical.legacy_authority import project_legacy_candidate
from translation_module.common import (
    ContentKind,
    SourceCueId,
    TargetCueId,
)
from translation_module.contracts import TargetCue
from translation_module.preparation import ChunkPlan, TranslationBatchPlan
from translation_module.validation import bind_translation_digest

if TYPE_CHECKING:
    from collections.abc import Callable

    from translation_module.contracts import SourceSnapshot, TranslationDocument

    type SelectFixture = Callable[
        [Path, tuple[Path, Path], SourceSnapshot, TranslationDocument, str],
        subprocess.CompletedProcess[str],
    ]

PROJECT_ROOT = Path(__file__).parents[2]
CLI_ERROR_EXIT = 2
type SelectionFailure = Literal["missing", "stale"]
source_fixture = cast(
    "Callable[..., SourceSnapshot]", vars(cli_test_support)["_source"]
)
document_fixture = cast(
    "Callable[[SourceSnapshot], TranslationDocument]",
    vars(cli_test_support)["_document"],
)
rationale_fixture = cast(
    "Callable[[Path, SourceSnapshot, TranslationDocument, str], Path]",
    vars(cli_test_support)["_rationale_path"],
)
select_fixture = cast(
    "SelectFixture",
    vars(cli_test_support)["_select_candidate"],
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


@pytest.mark.parametrize("failure", ["missing", "stale"])
def test_preflight_selection_cli_fails_closed(
    tmp_path: Path,
    failure: SelectionFailure,
) -> None:
    """Missing attempts and stale candidates never select."""
    source = source_fixture(bound=True)
    document = document_fixture(source)
    match failure:
        case "missing":
            pass
        case "stale":
            document = bind_translation_digest(
                document.model_copy(
                    update={
                        "cues": (
                            document.cues[0].model_copy(update={"text": "Changed."}),
                            *document.cues[1:],
                        )
                    }
                )
            )
    root = tmp_path / failure
    source_path = tmp_path / f"{failure}-source.json"
    document_path = tmp_path / f"{failure}-candidate.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    attempt_id = "selection-attempt"
    if failure != "missing":
        recorded_document = document_fixture(source) if failure == "stale" else document
        recorded_path = tmp_path / f"{failure}-recorded.json"
        _ = recorded_path.write_text(
            recorded_document.model_dump_json(), encoding="utf-8"
        )
        rationale = rationale_fixture(root, source, recorded_document, failure)
        recorded = _run(
            "record-candidate-attempt",
            str(source_path),
            str(recorded_path),
            "--authority-root",
            str(root),
            "--attempt",
            attempt_id,
            "initial",
            "-",
            "--rationales",
            str(rationale),
        )
        assert recorded.returncode == 0, recorded.stderr

    selected = select_fixture(
        root, (source_path, document_path), source, document, attempt_id
    )
    assert selected.returncode == CLI_ERROR_EXIT
    assert json.loads(selected.stderr)["error"]["code"] == (
        "preflight_selection_failed"
    )
    assert not tuple((root / "attempts").glob("translation-selection.*"))


def test_preflight_selection_cli_reports_duplicate_without_blocking(
    tmp_path: Path,
) -> None:
    """The CLI retains adjacent-duplicate findings and still selects."""
    source = source_fixture(bound=True)
    document = document_fixture(source)
    first = document.cues[0].model_copy(
        update={
            "source_cue_ids": (source.cues[0].cue_id,),
            "end_ms": source.cues[0].end_ms,
        }
    )
    second = first.model_copy(
        update={
            "sequence": 2,
            "cue_id": TargetCueId("target-2"),
            "source_cue_ids": (source.cues[1].cue_id,),
            "start_ms": source.cues[1].start_ms,
            "end_ms": source.cues[1].end_ms,
        }
    )
    document = bind_translation_digest(
        document.model_copy(update={"cues": (first, second)})
    )
    root = tmp_path / "duplicate"
    source_path = tmp_path / "duplicate-source.json"
    document_path = tmp_path / "duplicate-candidate.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    rationale = rationale_fixture(root, source, document, "duplicate")
    recorded = _run(
        "record-candidate-attempt",
        str(source_path),
        str(document_path),
        "--authority-root",
        str(root),
        "--attempt",
        "selection-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale),
    )
    assert recorded.returncode == 0, recorded.stderr

    selected = select_fixture(
        root,
        (source_path, document_path),
        source,
        document,
        "selection-attempt",
    )

    assert selected.returncode == 0, selected.stderr
    assert tuple((root / "attempts").glob("translation-selection.*"))


def test_preflight_selection_cli_reports_over_cps_without_blocking(
    tmp_path: Path,
) -> None:
    """The CLI selects a candidate while retaining its CPS overrun report."""
    source = source_fixture(bound=True)
    document = document_fixture(source)
    document = bind_translation_digest(
        document.model_copy(
            update={
                "cues": (
                    document.cues[0].model_copy(update={"text": "x" * 41}),
                    *document.cues[1:],
                )
            }
        )
    )
    root = tmp_path / "over-cps"
    source_path = tmp_path / "over-cps-source.json"
    document_path = tmp_path / "over-cps-candidate.json"
    recorded_path = tmp_path / "over-cps-recorded.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = document_path.write_text(document.model_dump_json(), encoding="utf-8")
    _ = recorded_path.write_text(document.model_dump_json(), encoding="utf-8")
    rationale = rationale_fixture(root, source, document, "over-cps")
    recorded = _run(
        "record-candidate-attempt",
        str(source_path),
        str(recorded_path),
        "--authority-root",
        str(root),
        "--attempt",
        "selection-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale),
    )
    assert recorded.returncode == 0, recorded.stderr

    selected = select_fixture(
        root,
        (source_path, document_path),
        source,
        document,
        "selection-attempt",
    )

    assert selected.returncode == 0, selected.stderr
    assert tuple((root / "attempts").glob("translation-selection.*"))


def test_missing_chunk_appends_one_idempotent_failed_attempt(tmp_path: Path) -> None:
    """Pre-document assembly failure records one retry-safe failed intent."""
    source = source_fixture(bound=True)
    plan = TranslationBatchPlan(
        plan_id="missing-chunk-plan",
        source_digest=source.content_digest,
        chunks=(
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId("source-1"),),
            ),
            ChunkPlan(
                chunk_id="chunk-2",
                owned_source_cue_ids=(SourceCueId("source-2"),),
            ),
        ),
    )
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    rationale_path = tmp_path / "unused-rationales.json"
    output = tmp_path / "candidate.json"
    root = tmp_path / "authority"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    _ = rationale_path.write_text("{}", encoding="utf-8")
    candidate_path = tmp_path / "chunk-1.json"
    candidate = ChunkCandidate(
        source_digest=source.content_digest,
        chunk_id="chunk-1",
        owned_source_cue_ids=(SourceCueId("source-1"),),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="Welcome.",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
    )
    _ = candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    arguments = (
        "assemble-candidate",
        str(source_path),
        str(plan_path),
        str(candidate_path),
        "--candidate-output",
        "missing-document",
        str(output),
        "--attempt-authority",
        str(root),
        "missing-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale_path),
    )

    for _index in range(2):
        result = _run(*arguments)
        assert result.returncode == CLI_ERROR_EXIT
    assert not output.exists()
    attempts = TranslationAttemptStore(root / "attempts").load()
    assert len(attempts) == 1
    payload = attempts[0].model_dump(mode="json", exclude_none=True)
    assert payload["outcome"] == "failed"
    assert "candidate_digest" not in payload
    assert "rationales" not in payload


def test_explicit_failed_attempt_has_no_candidate_claims(tmp_path: Path) -> None:
    """Explicit production failure records binding only."""
    source = source_fixture(bound=True)
    document = document_fixture(source)
    root = tmp_path / "failed-authority"
    candidate = project_legacy_candidate(source, document, root).candidate
    candidate_path = tmp_path / "candidate.json"
    _ = candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")

    result = _run(
        "record-failed-attempt",
        str(candidate_path),
        "--authority-root",
        str(root),
        "--attempt",
        "failed-attempt",
        "retry",
        "-",
    )
    assert result.returncode == 0, result.stderr
    payload = (
        TranslationAttemptStore(root / "attempts")
        .load()[0]
        .model_dump(mode="json", exclude_none=True)
    )
    assert payload["outcome"] == "failed"
    assert "candidate_digest" not in payload
    assert "rationales" not in payload
