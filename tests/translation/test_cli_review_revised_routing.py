# Copyright 2026 Transcription Module contributors

"""Preserved revised-candidate CLI review routing scenario."""

import json
from pathlib import Path

from tests.translation.cli_review_scenario_support import (
    ReviewScenario,
    run_review_scenario,
)
from tests.translation.test_cli import (
    document_fixture,
    run_cli_fixture,
    source_fixture,
)
from translation_module.review_preparation import ReviewScope
from translation_module.review_state import (
    ReviewAttempts,
    RevisionEntry,
    RevisionRiskClass,
)
from translation_module.validation import bind_translation_digest
from translation_module.workflow import ReviewAssignment, ReviewDomain, ReviewResult

CLI_ERROR_EXIT = 2


def test_prepare_review_preserves_authoritative_root_for_revised_routing(
    tmp_path: Path,
) -> None:
    """A revised review assignment retains its authoritative attempt root."""
    source = source_fixture(bound=True)
    root_candidate = document_fixture(source)
    revised_candidate = bind_translation_digest(
        root_candidate.model_copy(
            update={
                "content_digest": "0" * 64,
                "cues": (
                    root_candidate.cues[0].model_copy(update={"text": "Welcome back!"}),
                ),
            }
        )
    )
    audited_source_ids = tuple(cue.cue_id for cue in source.cues)
    attempts = ReviewAttempts(
        root_candidate_digest=root_candidate.content_digest,
        current_candidate_digest=revised_candidate.content_digest,
        revisions=(
            RevisionEntry(
                before_candidate_digest=root_candidate.content_digest,
                after_candidate_digest=revised_candidate.content_digest,
                risk_class=RevisionRiskClass.HIGH_RISK,
                reviewed_domains=(
                    ReviewDomain.FIDELITY,
                    ReviewDomain.NATURALNESS,
                ),
                defect_classes=("meaning-omission", "readability_limits"),
                audited_source_cue_ids=audited_source_ids,
            ),
        ),
    )
    source_path = tmp_path / "source.json"
    candidate_path = tmp_path / "candidate.json"
    attempts_path = tmp_path / "attempts.json"
    scope_path = tmp_path / "scope.json"
    reset_request_path = tmp_path / "reset-request.json"
    reset_assignment_path = tmp_path / "reset-assignment.json"
    reset_review_path = tmp_path / "reset-review.json"
    reset_route_path = tmp_path / "reset-route.json"
    request_path = tmp_path / "review-request.json"
    assignment_path = tmp_path / "review-assignment.json"
    review_path = tmp_path / "review.json"
    route_path = tmp_path / "route.json"
    _ = source_path.write_text(source.model_dump_json(), encoding="utf-8")
    _ = candidate_path.write_text(
        revised_candidate.model_dump_json(),
        encoding="utf-8",
    )
    _ = attempts_path.write_text(attempts.model_dump_json(), encoding="utf-8")
    _ = scope_path.write_text(
        ReviewScope(
            source_cue_ids=audited_source_ids,
            target_cue_ids=tuple(cue.cue_id for cue in revised_candidate.cues),
        ).model_dump_json(),
        encoding="utf-8",
    )

    scope = ReviewScope.model_validate_json(scope_path.read_bytes())
    scenario = ReviewScenario(
        source=source,
        candidate=revised_candidate,
        scope_json=scope.model_dump_json().encode(),
        session_ids=("translator", "reviser", "assembler", "reviewer"),
    )
    reset_preparation = run_review_scenario(
        scenario, (reset_request_path, reset_assignment_path)
    )

    assert reset_preparation.returncode == 0, reset_preparation.stderr
    reset_assignment = ReviewAssignment.model_validate_json(
        reset_assignment_path.read_text(encoding="utf-8")
    )
    assert reset_assignment.current_candidate_digest == (
        attempts.current_candidate_digest
    )
    assert reset_assignment.root_candidate_digest != attempts.root_candidate_digest
    _ = reset_review_path.write_text(
        ReviewResult(
            candidate_digest=revised_candidate.content_digest,
            assignment_digest=reset_assignment.assignment_digest,
            reviewed_source_cue_ids=reset_assignment.ordered_source_cue_ids,
            reviewed_target_cue_ids=reset_assignment.ordered_target_cue_ids,
            reviewed_domains=reset_assignment.reviewed_domains,
        ).model_dump_json(),
        encoding="utf-8",
    )
    reset_route = run_cli_fixture(
        "route-review",
        str(candidate_path),
        str(reset_review_path),
        str(reset_assignment_path),
        str(attempts_path),
        "--output",
        str(reset_route_path),
    )
    assert reset_route.returncode == CLI_ERROR_EXIT
    assert json.loads(reset_route.stderr)["error"]["code"] == ("review_routing_failed")
    assert not reset_route_path.exists()

    prepared = run_review_scenario(
        scenario, (request_path, assignment_path), attempts
    )

    assert prepared.returncode == 0, prepared.stderr
    assignment = ReviewAssignment.model_validate_json(
        assignment_path.read_text(encoding="utf-8")
    )
    assert assignment.root_candidate_digest == attempts.root_candidate_digest
    assert assignment.current_candidate_digest == attempts.current_candidate_digest
    _ = review_path.write_text(
        ReviewResult(
            candidate_digest=revised_candidate.content_digest,
            assignment_digest=assignment.assignment_digest,
            reviewed_source_cue_ids=assignment.ordered_source_cue_ids,
            reviewed_target_cue_ids=assignment.ordered_target_cue_ids,
            reviewed_domains=assignment.reviewed_domains,
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
    assert json.loads(route_path.read_text(encoding="utf-8"))["action"] == "finalize"
    retained_attempts = ReviewAttempts.model_validate_json(
        attempts_path.read_text(encoding="utf-8")
    )
    assert retained_attempts.general == 1
    assert retained_attempts.high_risk == 1
    assert retained_attempts.revisions[0].defect_classes == (
        "meaning-omission",
        "readability_limits",
    )
    assert retained_attempts.revisions[0].audited_source_cue_ids == (audited_source_ids)
