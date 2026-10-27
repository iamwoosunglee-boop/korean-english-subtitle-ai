# Copyright 2026 Transcription Module contributors

"""Canonical v2 preflight-selection CLI regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import JsonValue, TypeAdapter

from tests.translation.canonical_preflight_support import (
    checker_evidence,
    preflight_attempt,
    preflight_context,
)
from translation_module.canonical.attempts import (
    TranslationSelectionReceipt,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.preflight import CandidatePreflightContext
from translation_module.canonical.runtime import CanonicalTranslationWorkflow
from translation_module.cli_io import MAX_JSON_BYTES

PROJECT_ROOT = Path(__file__).parents[2]
CLI_ERROR_EXIT = 2
type SelectionPaths = tuple[Path, Path, Path]
type SelectionRoots = tuple[Path, Path]
type SelectionIds = tuple[str, str]
type DependencyKind = Literal["escape", "symlink"]


def _bound_checker_bytes(context: CandidatePreflightContext) -> bytes:
    checker = checker_evidence(context)
    payload: dict[str, JsonValue] = {
        "schema_version": "translation-preflight-checker.v1",
        "translation_document_id": context.candidate.document_id,
        "translation_revision_id": context.candidate.revision_id,
        "candidate_content_digest": context.candidate.content_digest,
        "source_document_id": context.source.document_id,
        "source_revision_id": context.source.revision_id,
        "source_content_digest": context.source.content_digest,
        "glossary_asset_id": context.glossary.asset_id,
        "glossary_revision_id": context.glossary.revision_id,
        "glossary_content_digest": context.glossary.content_digest,
        "evidence": TypeAdapter(JsonValue).validate_json(checker.model_dump_json()),
    }
    return TypeAdapter(dict[str, JsonValue]).dump_json(payload)


def _write_inputs(
    root: Path,
    context: CandidatePreflightContext,
    checker_context: CandidatePreflightContext | None = None,
) -> SelectionPaths:
    project = root / "project"
    _ = project.mkdir(parents=True)
    source_path = project / "source.json"
    candidate_path = project / "candidate.json"
    checker_path = project / "checker.json"
    _ = source_path.write_text(context.source.model_dump_json(), encoding="utf-8")
    _ = candidate_path.write_text(
        context.candidate.model_dump_json(),
        encoding="utf-8",
    )
    _ = checker_path.write_bytes(_bound_checker_bytes(checker_context or context))
    glossary_locator = context.candidate.glossary_ref.path
    assert glossary_locator is not None
    glossary_path = candidate_path.parent / glossary_locator
    _ = glossary_path.parent.mkdir(parents=True, exist_ok=True)
    _ = glossary_path.write_text(
        context.glossary.model_dump_json(),
        encoding="utf-8",
    )
    return source_path, candidate_path, checker_path


def _run_selection(
    paths: SelectionPaths,
    roots: SelectionRoots,
    ids: SelectionIds,
) -> subprocess.CompletedProcess[str]:
    source_path, candidate_path, checker_path = paths
    authority_root, dependency_root = roots
    attempt_id, selection_id = ids
    return subprocess.run(
        [
            "uv",
            "run",
            "translation-cli",
            "preflight-select",
            str(source_path),
            str(candidate_path),
            str(checker_path),
            "--authority-root",
            str(authority_root),
            "--dependency-root",
            str(dependency_root),
            "--attempt-id",
            attempt_id,
            "--selection",
            selection_id,
            "2026-09-01T00:00:00Z",
            "reviewer-v2",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_preflight_select_accepts_persisted_canonical_v2_candidate(
    tmp_path: Path,
) -> None:
    """A clean v2 checker selects the exact persisted canonical attempt."""
    # Given
    context = preflight_context()
    authority_root = tmp_path / "authority"
    paths = _write_inputs(tmp_path, context)
    workflow = CanonicalTranslationWorkflow(authority_root)
    _ = workflow.record_attempt(preflight_attempt(context), context.candidate)

    # When
    result = _run_selection(
        paths,
        (authority_root, tmp_path),
        ("attempt-preflight", "selection-v2"),
    )

    # Then
    assert result.returncode == 0, result.stderr
    payload = TypeAdapter(dict[str, str]).validate_json(result.stdout)
    selection_path = workflow.attempt_store.selection_path(
        context.candidate.document_id,
        context.candidate.revision_id,
    )
    receipt = TranslationSelectionReceipt.model_validate_json(
        selection_path.read_bytes()
    )
    assert payload["selection_id"] == "selection-v2"
    assert receipt.attempt_id == "attempt-preflight"
    assert receipt.translation_document_digest == context.candidate.content_digest


def test_preflight_select_rejects_checker_bound_to_prior_candidate(
    tmp_path: Path,
) -> None:
    """Changed candidate bytes cannot reuse prior semantic checker evidence."""
    # Given
    prior = preflight_context()
    first = prior.candidate.cues[0].model_copy(update={"text": "Changed target."})
    candidate = bind_canonical_digests(
        prior.candidate.model_copy(update={"cues": (first, *prior.candidate.cues[1:])})
    )
    current = CandidatePreflightContext(
        candidate=candidate,
        source=prior.source,
        glossary=prior.glossary,
    )
    authority_root = tmp_path / "authority"
    paths = _write_inputs(tmp_path, current, prior)
    workflow = CanonicalTranslationWorkflow(authority_root)
    _ = workflow.record_attempt(preflight_attempt(current), current.candidate)

    # When
    result = _run_selection(
        paths,
        (authority_root, tmp_path),
        ("attempt-preflight", "stale-checker-selection"),
    )

    # Then
    assert result.returncode == CLI_ERROR_EXIT
    selection_path = workflow.attempt_store.selection_path(
        current.candidate.document_id,
        current.candidate.revision_id,
    )
    assert not selection_path.exists()
    assert not workflow.preflight_path(current.candidate).exists()


@pytest.mark.parametrize("dependency_kind", ["escape", "symlink"])
def test_preflight_select_rejects_unconfined_glossary_dependency(
    tmp_path: Path,
    dependency_kind: DependencyKind,
) -> None:
    """Traversal and intermediate symlinks cannot supply canonical glossary."""
    # Given
    base = preflight_context()
    graph_root = tmp_path / "graph"
    project = graph_root / "project"
    _ = project.mkdir(parents=True)
    outside = tmp_path / "outside"
    _ = outside.mkdir()
    match dependency_kind:
        case "escape":
            locator = "../../outside/glossary.json"
            glossary_path = outside / "glossary.json"
        case "symlink":
            locator = "../assets/glossary.json"
            outside_assets = outside / "assets"
            _ = outside_assets.mkdir()
            (graph_root / "assets").symlink_to(
                outside_assets,
                target_is_directory=True,
            )
            glossary_path = outside_assets / "glossary.json"
    candidate = bind_canonical_digests(
        base.candidate.model_copy(
            update={
                "glossary_ref": base.candidate.glossary_ref.model_copy(
                    update={"path": locator}
                )
            }
        )
    )
    current = CandidatePreflightContext(
        candidate=candidate,
        source=base.source,
        glossary=base.glossary,
    )
    paths = (
        project / "source.json",
        project / "candidate.json",
        project / "checker.json",
    )
    _ = paths[0].write_text(current.source.model_dump_json(), encoding="utf-8")
    _ = paths[1].write_text(current.candidate.model_dump_json(), encoding="utf-8")
    _ = paths[2].write_bytes(_bound_checker_bytes(current))
    _ = glossary_path.write_text(current.glossary.model_dump_json(), encoding="utf-8")
    authority_root = graph_root / "authority"
    workflow = CanonicalTranslationWorkflow(authority_root)
    _ = workflow.record_attempt(preflight_attempt(current), current.candidate)

    # When
    result = _run_selection(
        paths,
        (authority_root, graph_root),
        ("attempt-preflight", f"{dependency_kind}-selection"),
    )

    # Then
    assert result.returncode == CLI_ERROR_EXIT
    selection_path = workflow.attempt_store.selection_path(
        current.candidate.document_id,
        current.candidate.revision_id,
    )
    assert not selection_path.exists()
    assert not workflow.preflight_path(current.candidate).exists()


def test_preflight_select_rejects_oversized_confined_glossary(
    tmp_path: Path,
) -> None:
    """Confined dependency reads retain the public CLI JSON byte limit."""
    # Given
    context = preflight_context()
    authority_root = tmp_path / "authority"
    paths = _write_inputs(tmp_path, context)
    glossary_locator = context.candidate.glossary_ref.path
    assert glossary_locator is not None
    glossary_path = paths[1].parent / glossary_locator
    payload = glossary_path.read_bytes()
    _ = glossary_path.write_bytes(payload + b" " * (MAX_JSON_BYTES + 1 - len(payload)))
    workflow = CanonicalTranslationWorkflow(authority_root)
    _ = workflow.record_attempt(preflight_attempt(context), context.candidate)

    # When
    result = _run_selection(
        paths,
        (authority_root, tmp_path),
        ("attempt-preflight", "oversized-glossary-selection"),
    )

    # Then
    assert result.returncode == CLI_ERROR_EXIT
    selection_path = workflow.attempt_store.selection_path(
        context.candidate.document_id,
        context.candidate.revision_id,
    )
    assert not selection_path.exists()
    assert not workflow.preflight_path(context.candidate).exists()
