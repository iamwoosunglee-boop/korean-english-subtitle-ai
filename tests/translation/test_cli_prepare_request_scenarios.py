# Copyright 2026 Transcription Module contributors

"""Preserved translation CLI request and typed-gate scenarios."""

import json
from pathlib import Path

from tests.translation.cli_review_scenario_support import (
    ReviewScenario,
    run_review_scenario,
)
from tests.translation.test_cli import (
    FRAME_BYTES,
    batch_fixture,
    rationale_path_fixture,
    run_cli_fixture,
    source_fixture,
)
from translation_module.candidate import ChunkCandidate, assemble_candidate
from translation_module.common import SourceCueId, TargetCueId
from translation_module.contracts import TargetCue, TranslationDocument
from translation_module.preparation import ChunkPlan
from translation_module.review_preparation import ReviewScope
from translation_module.review_state import ReviewAttempts
from translation_module.workflow import ReviewAssignment, ReviewResult

CLI_ERROR_EXIT = 2


def test_prepare_request_preflights_included_evidence(tmp_path: Path) -> None:
    """The CLI verifies referenced file evidence before publishing output."""
    frame = tmp_path / "frames" / "frame-1.jpg"
    frame.parent.mkdir()
    _ = frame.write_bytes(FRAME_BYTES)
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    output_path = tmp_path / "request.json"
    source = source_fixture(bound=True, evidence_root=tmp_path)
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(
        batch_fixture(
            source,
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId("source-2"),),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = run_cli_fixture(
        "prepare-request",
        str(source_path),
        str(plan_path),
        "chunk-1",
        "--evidence-root",
        str(tmp_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 0, result.stderr
    assert output_path.is_file()


def test_prepare_request_failure_leaves_no_output(tmp_path: Path) -> None:
    """Digest failure happens before request output is created."""
    frame = tmp_path / "frames" / "frame-1.jpg"
    frame.parent.mkdir()
    _ = frame.write_bytes(b"changed")
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    output_path = tmp_path / "request.json"
    source = source_fixture(bound=True, evidence_root=tmp_path)
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(
        batch_fixture(
            source,
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId("source-2"),),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = run_cli_fixture(
        "prepare-request",
        str(source_path),
        str(plan_path),
        "chunk-1",
        "--evidence-root",
        str(tmp_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == CLI_ERROR_EXIT
    assert "digest mismatch" in result.stderr
    assert not output_path.exists()


def test_remaining_typed_cli_gates_happy_path(tmp_path: Path) -> None:
    """Assembly, review preparation, and clean routing cross typed files."""
    source = source_fixture(bound=True)
    plan = batch_fixture(
        source,
        ChunkPlan(
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-2"),),
        ),
    )
    source_path = tmp_path / "source.json"
    plan_path = tmp_path / "plan.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    validated = run_cli_fixture("validate-batch-plan", str(source_path), str(plan_path))
    assert validated.returncode == 0, validated.stderr
    for chunk in plan.chunks:
        request_path = tmp_path / f"request-{chunk.chunk_id}.json"
        prepared_request = run_cli_fixture(
            "prepare-request",
            str(source_path),
            str(plan_path),
            chunk.chunk_id,
            "--evidence-root",
            str(tmp_path),
            "--output",
            str(request_path),
        )
        assert prepared_request.returncode == 0, prepared_request.stderr
    candidate_paths: list[Path] = []
    for index, chunk in enumerate(plan.chunks, start=1):
        source_cue = source.cues[index - 1]
        path = tmp_path / f"chunk-{index}.json"
        _ = path.write_text(
            ChunkCandidate(
                source_digest=source.content_digest,
                chunk_id=chunk.chunk_id,
                owned_source_cue_ids=chunk.owned_source_cue_ids,
                cues=(
                    TargetCue(
                        sequence=index,
                        cue_id=TargetCueId(f"target-{index}"),
                        source_cue_ids=chunk.owned_source_cue_ids,
                        start_ms=source_cue.start_ms,
                        end_ms=source_cue.end_ms,
                        text="Welcome." if index == 1 else "Thank you.",
                        speakers=source_cue.speakers,
                        content_kind=source_cue.content_kind,
                    ),
                ),
            ).model_dump_json(),
            encoding="utf-8",
        )
        candidate_paths.append(path)
    candidate_path = tmp_path / "candidate.json"
    document_id = "translation-cli-gates"
    candidates = tuple(
        ChunkCandidate.model_validate_json(path.read_bytes())
        for path in candidate_paths
    )
    expected = assemble_candidate(
        source, plan.chunks, candidates, document_id=document_id
    )
    authority_root = tmp_path / "translation-authority"
    rationale_path = rationale_path_fixture(
        authority_root, source, expected, "typed-gates"
    )
    assembled = run_cli_fixture(
        "assemble-candidate",
        str(source_path),
        str(plan_path),
        *(str(path) for path in candidate_paths),
        "--candidate-output",
        document_id,
        str(candidate_path),
        "--attempt-authority",
        str(authority_root),
        "typed-gates-attempt",
        "initial",
        "-",
        "--rationales",
        str(rationale_path),
    )
    assert assembled.returncode == 0, assembled.stderr
    document = TranslationDocument.model_validate_json(candidate_path.read_text())
    request_path = tmp_path / "review-request.json"
    assignment_path = tmp_path / "assignment.json"
    prepared = run_review_scenario(
        ReviewScenario(
            source=source,
            candidate=document,
            scope_json=ReviewScope(
                source_cue_ids=tuple(cue.cue_id for cue in source.cues),
                target_cue_ids=tuple(cue.cue_id for cue in document.cues),
            )
            .model_dump_json()
            .encode(),
            session_ids=(
                "translator-runtime",
                "reviser-runtime",
                "assembler-runtime",
                "reviewer-runtime",
            ),
        ),
        (request_path, assignment_path),
    )
    assert prepared.returncode == 0, prepared.stderr
    assignment = ReviewAssignment.model_validate_json(assignment_path.read_text())
    review_path = tmp_path / "review.json"
    attempts_path = tmp_path / "attempts.json"
    route_path = tmp_path / "route.json"
    _ = review_path.write_text(
        ReviewResult(
            candidate_digest=document.content_digest,
            assignment_digest=assignment.assignment_digest,
            reviewed_source_cue_ids=assignment.ordered_source_cue_ids,
            reviewed_target_cue_ids=assignment.ordered_target_cue_ids,
            reviewed_domains=assignment.reviewed_domains,
        ).model_dump_json(),
        encoding="utf-8",
    )
    _ = attempts_path.write_text(
        ReviewAttempts(
            root_candidate_digest=document.content_digest,
            current_candidate_digest=document.content_digest,
        ).model_dump_json(),
        encoding="utf-8",
    )
    routed = run_cli_fixture(
        "route-review",
        str(candidate_path),
        str(review_path),
        str(assignment_path),
        str(attempts_path),
        "--output",
        str(route_path),
    )
    assert routed.returncode == 0, routed.stderr
    assert json.loads(route_path.read_text())["action"] == "finalize"
