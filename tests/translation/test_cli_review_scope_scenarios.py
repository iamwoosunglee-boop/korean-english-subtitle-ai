# Copyright 2026 Transcription Module contributors

"""Preserved translation review-scope scenarios."""

import json
from pathlib import Path

import pytest

from tests.translation.cli_review_scenario_support import (
    ReviewScenario,
    run_review_scenario,
)
from tests.translation.test_cli import document_fixture, source_fixture
from translation_module.review_preparation import ReviewRequest, ReviewScope
from translation_module.review_state import ReviewAttempts
from translation_module.workflow import ReviewAssignment, ReviewDomain

CLI_ERROR_EXIT = 2

_SESSIONS = ("translator", "reviser", "assembler", "focused-reviewer")


def test_prepare_review_rejects_stale_attempts_atomically(tmp_path: Path) -> None:
    """Stale authoritative attempts produce neither prepared output."""
    source = source_fixture(bound=True)
    candidate = document_fixture(source)
    attempts = ReviewAttempts(
        root_candidate_digest="a" * 64,
        current_candidate_digest="a" * 64,
    )
    scope_path = tmp_path / "scope.json"
    attempts_path = tmp_path / "attempts.json"
    output = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    _ = scope_path.write_text(
        ReviewScope(
            source_cue_ids=tuple(cue.cue_id for cue in source.cues),
            target_cue_ids=tuple(cue.cue_id for cue in candidate.cues),
        ).model_dump_json(),
        encoding="utf-8",
    )
    _ = attempts_path.write_text(attempts.model_dump_json(), encoding="utf-8")
    result = run_review_scenario(
        ReviewScenario(
            source=source,
            candidate=candidate,
            scope_json=scope_path.read_bytes(),
            session_ids=_SESSIONS,
        ),
        (output, assignment),
        attempts,
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert json.loads(result.stderr)["error"]["code"] == ("review_preparation_failed")
    assert not output.exists()
    assert not assignment.exists()


def test_prepare_review_writes_digest_bound_focused_scope(tmp_path: Path) -> None:
    """Preparation preserves the exact parent-issued focused review scope."""
    source = source_fixture(bound=True)
    document = document_fixture(source)
    scope_path = tmp_path / "focused-scope.json"
    output = tmp_path / "request.json"
    assignment_path = tmp_path / "assignment.json"
    focused_domains = ("fidelity", "culture", "lyrics")
    _ = scope_path.write_text(
        json.dumps(
            {
                "candidate_digest": document.content_digest,
                "source_cue_ids": ["source-2"],
                "target_cue_ids": ["target-1"],
                "reviewed_domains": list(focused_domains),
            }
        ),
        encoding="utf-8",
    )
    result = run_review_scenario(
        ReviewScenario(
            source=source,
            candidate=document,
            scope_json=scope_path.read_bytes(),
            session_ids=_SESSIONS,
        ),
        (output, assignment_path),
    )

    assert result.returncode == 0, result.stderr
    request = ReviewRequest.model_validate_json(output.read_text(encoding="utf-8"))
    assignment = ReviewAssignment.model_validate_json(
        assignment_path.read_text(encoding="utf-8")
    )
    assert request.candidate_digest == document.content_digest
    assert request.source_cue_ids == ("source-2",)
    assert request.target_cue_ids == ("target-1",)
    assert request.reviewed_domains == tuple(
        ReviewDomain(domain) for domain in focused_domains
    )
    assert assignment.current_candidate_digest == document.content_digest
    assert assignment.ordered_source_cue_ids == ("source-1", "source-2")
    assert assignment.ordered_target_cue_ids == ("target-1",)
    assert assignment.reviewed_domains == tuple(ReviewDomain)
    assert assignment.focused_source_cue_ids == ("source-2",)
    assert assignment.focused_target_cue_ids == ("target-1",)
    assert assignment.focused_domains == tuple(
        ReviewDomain(domain) for domain in focused_domains
    )


@pytest.mark.parametrize("mutation", ["duplicate-domain", "foreign-target"])
def test_prepare_review_rejects_invalid_focused_scope_atomically(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Malformed and out-of-candidate focused scopes emit neither output."""
    source = source_fixture(bound=True)
    document = document_fixture(source)
    scope_path = tmp_path / "focused-scope.json"
    output = tmp_path / "request.json"
    assignment = tmp_path / "assignment.json"
    payload: dict[str, str | list[str]] = {
        "candidate_digest": document.content_digest,
        "source_cue_ids": ["source-2"],
        "target_cue_ids": ["target-1"],
        "reviewed_domains": ["fidelity", "culture", "lyrics"],
    }
    if mutation == "duplicate-domain":
        payload["reviewed_domains"] = ["fidelity", "fidelity"]
    else:
        payload["target_cue_ids"] = ["foreign-target"]
    _ = scope_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_review_scenario(
        ReviewScenario(
            source=source,
            candidate=document,
            scope_json=scope_path.read_bytes(),
            session_ids=_SESSIONS,
        ),
        (output, assignment),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert json.loads(result.stderr)["error"]["code"] == ("review_preparation_failed")
    assert not output.exists()
    assert not assignment.exists()
