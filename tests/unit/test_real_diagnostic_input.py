# Copyright 2026 Transcription Module contributors

"""RED contracts for authoritative paired-diagnostic input construction."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from typing import TYPE_CHECKING

from pydantic import JsonValue, TypeAdapter

from tests.unit.real_diagnostic_fixture_support import (
    FIXTURE,
    FOREIGN_RUN_ID,
    RUN_ID,
    build_fixture_tree,
    load_assessment,
    load_fixture_run,
    rebind_assessment_author,
    replace_artifact,
    write_assessment,
    write_copied_sample_assessment,
    write_fixture_run,
)

if TYPE_CHECKING:
    from pathlib import Path

_JSON_DICT = TypeAdapter(dict[str, JsonValue])
_JSON_LIST = TypeAdapter(list[JsonValue])
EXPECTED_CASES = 17
EXPECTED_INTERVALS = 19
EXIT_REFUSAL = 2


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _build(run_fixture: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        "scripts.qa.build_paired_diagnostic_input",
        "--fixture",
        str(FIXTURE),
        "--run-fixture",
        str(run_fixture),
        "--output",
        str(output),
    )


def _payload(path: Path) -> dict[str, JsonValue]:
    return _JSON_DICT.validate_json(path.read_bytes())


def _error_code(result: subprocess.CompletedProcess[str]) -> str | None:
    try:
        envelope = _JSON_DICT.validate_json(result.stderr)
        error = _JSON_DICT.validate_python(envelope["error"])
        return TypeAdapter(str).validate_python(error["code"])
    except (KeyError, ValueError):
        return None


def _evidence(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return _JSON_DICT.validate_python(payload["evidence"])


def _rebind_v2_input(payload: dict[str, JsonValue]) -> None:
    binding_payload = deepcopy(payload)
    del binding_payload["input_payload_sha256"]
    encoded = json.dumps(
        binding_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    payload["input_payload_sha256"] = hashlib.sha256(encoded).hexdigest()


def _evaluate(
    diagnostic: Path,
    output_dir: Path,
    run_fixture: Path,
) -> subprocess.CompletedProcess[str]:
    return _run(
        "scripts.qa.paired_diagnostic",
        "--fixture",
        str(FIXTURE),
        "--diagnostic",
        str(diagnostic),
        "--output-dir",
        str(output_dir),
        "--run-fixture",
        str(run_fixture),
        "--required-input-mode",
        "fixture",
    )


def test_builds_evidence_bearing_v2_when_fixture_snapshot_is_current(
    tmp_path: Path,
) -> None:
    """Build one fixture-only v2 input with embedded evidence."""
    run_fixture = build_fixture_tree(tmp_path)
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = _payload(output)
    assert payload["schema_version"] == "paired-diagnostic-input.v2"
    assert payload["input_mode"] == "fixture"
    claims = tuple(
        _JSON_DICT.validate_python(item)
        for item in _JSON_LIST.validate_python(payload["alignment_claims"])
    )
    assert all("speakers" in claim for claim in claims)
    assert all("speaker" not in claim for claim in claims)


def test_evaluator_accepts_exact_authoritative_fixture_replay(
    tmp_path: Path,
) -> None:
    """Accept v2 only when evaluator replay reproduces exact builder bytes."""
    run_fixture = build_fixture_tree(tmp_path)
    output = tmp_path / "diagnostic.json"
    assert _build(run_fixture, output).returncode == 0

    result = _evaluate(output, tmp_path / "receipts", run_fixture)

    assert result.returncode == 0, (result.stdout, result.stderr)


def test_rejects_copied_v1_outcomes_without_output(tmp_path: Path) -> None:
    """Reject caller-supplied v1 outcomes as assessment authority."""
    run_fixture = build_fixture_tree(tmp_path)
    write_copied_sample_assessment(run_fixture)
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "non_authoritative_outcome"
    assert not output.exists()


def test_rejects_foreign_run_binding(tmp_path: Path) -> None:
    """Reject an assessment copied from another candidate run."""
    run_fixture = build_fixture_tree(tmp_path)
    assessment = load_assessment(run_fixture)
    write_assessment(
        run_fixture,
        assessment.model_copy(update={"candidate_run_id": FOREIGN_RUN_ID}),
    )
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "foreign_run"
    assert not output.exists()


def test_rejects_stale_artifact_digest(tmp_path: Path) -> None:
    """Reject manifest hashes that no longer match artifact bytes."""
    run_fixture = build_fixture_tree(tmp_path)
    fixture_run = load_fixture_run(run_fixture)
    stale = fixture_run.artifacts[0].model_copy(update={"sha256": "f" * 64})
    write_fixture_run(
        run_fixture,
        fixture_run.model_copy(
            update={"artifacts": (stale, *fixture_run.artifacts[1:])}
        ),
    )
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "stale_artifact"
    assert not output.exists()


def test_rejects_duplicate_case_assessment(tmp_path: Path) -> None:
    """Reject duplicated rows in the frozen assessment population."""
    run_fixture = build_fixture_tree(tmp_path)
    assessment = load_assessment(run_fixture)
    cases = assessment.author.cases
    author = assessment.author.model_copy(update={"cases": (*cases, cases[0])})
    write_assessment(run_fixture, assessment.model_copy(update={"author": author}))
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "duplicate_assessment"
    assert not output.exists()


def test_rejects_missing_case_assessment(tmp_path: Path) -> None:
    """Reject incomplete frozen assessment coverage."""
    run_fixture = build_fixture_tree(tmp_path)
    assessment = load_assessment(run_fixture)
    author = assessment.author.model_copy(
        update={"cases": assessment.author.cases[:-1]}
    )
    write_assessment(run_fixture, assessment.model_copy(update={"author": author}))
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "incomplete_frozen_population"
    assert not output.exists()


def test_rejects_unknown_frozen_case(tmp_path: Path) -> None:
    """Reject assessment rows outside the frozen case population."""
    run_fixture = build_fixture_tree(tmp_path)
    assessment = load_assessment(run_fixture)
    cases = assessment.author.cases
    unknown = cases[-1].model_copy(update={"case_id": "unknown-frozen-case"})
    author = assessment.author.model_copy(update={"cases": (*cases[:-1], unknown)})
    write_assessment(run_fixture, assessment.model_copy(update={"author": author}))
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "unknown_frozen_case"
    assert not output.exists()


def test_replay_is_byte_deterministic_across_output_paths(tmp_path: Path) -> None:
    """Publish byte-identical v2 input from the same artifact snapshot."""
    run_fixture = build_fixture_tree(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    first_result = _build(run_fixture, first)
    second_result = _build(run_fixture, second)

    assert first_result.returncode == 0
    assert second_result.returncode == 0
    assert first.read_bytes() == second.read_bytes()


def test_existing_output_remains_unchanged_without_temporary_files(
    tmp_path: Path,
) -> None:
    """Preserve existing output and clean every unpublished temp file."""
    run_fixture = build_fixture_tree(tmp_path)
    output = tmp_path / "diagnostic.json"
    original = b"existing-output\n"
    _ = output.write_bytes(original)

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "output_exists"
    assert output.read_bytes() == original
    assert tuple(tmp_path.glob(f".{output.name}.*.tmp")) == ()


def test_real_mode_requires_reviewed_speaker_authority(tmp_path: Path) -> None:
    """Keep real-run construction closed until Tasks 6-11 bind authority."""
    output = tmp_path / "diagnostic.json"

    result = _run(
        "scripts.qa.build_paired_diagnostic_input",
        "--fixture",
        str(FIXTURE),
        "--run-id",
        "171bbfef-6222-43ab-8bd1-613d86cebc7b",
        "--runs-dir",
        "runs",
        "--output",
        str(output),
    )

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "required_speaker_authority"
    assert not output.exists()


def test_v2_embeds_required_bindings_and_complete_case_bridges(
    tmp_path: Path,
) -> None:
    """Embed all artifact bindings and exact 17-case/19-interval bridges."""
    run_fixture = build_fixture_tree(tmp_path)
    output = tmp_path / "diagnostic.json"

    result = _build(run_fixture, output)

    assert result.returncode == 0
    evidence = _evidence(_payload(output))
    required = {
        "fixture_sha256",
        "artifact_manifest",
        "artifact_manifest_sha256",
        "artifact_snapshot_digest",
        "assessment_sha256",
        "assessment_author_payload_sha256",
        "candidate_transcript_sha256",
        "working_transcript_digest",
        "policy_file_sha256",
        "policy_digest",
        "inventory_file_sha256",
        "inventory_digest",
        "source_authority_digest",
        "source_digest",
        "speaker_authority_sha256",
        "verified_speaker_turns_sha256",
        "final_review_assignment_digest",
        "final_review_result_digest",
        "final_review_receipt_digest",
        "final_review_proposal_digest",
        "final_review_decision_digest",
        "final_review_application_digest",
        "confirmation_anchor_digest",
        "case_evidence",
        "validation_expected_absent",
    }
    bridges = _JSON_LIST.validate_python(evidence["case_evidence"])
    intervals = sum(
        len(
            _JSON_LIST.validate_python(
                _JSON_DICT.validate_python(bridge)["intervals"]
            )
        )
        for bridge in bridges
    )
    assert required <= evidence.keys()
    assert len(bridges) == EXPECTED_CASES
    assert intervals == EXPECTED_INTERVALS
    assert evidence["validation_expected_absent"] is True


def test_unsupported_additions_must_not_increase(tmp_path: Path) -> None:
    """Fail safety when unsupported additions increase after correction."""
    source = FIXTURE.parent / "paired-diagnostic-pass.v1.json"
    payload = _payload(source)
    safety = _JSON_DICT.validate_python(payload["safety_counts"])
    passing = tmp_path / "passing.json"
    safety["unsupported_additions_before"] = 1
    safety["unsupported_additions_after"] = 0
    payload["safety_counts"] = safety
    _ = passing.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    safety["unsupported_additions_after"] = 2
    payload["safety_counts"] = safety
    failing = tmp_path / "failing.json"
    _ = failing.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    pass_result = _run(
        "scripts.qa.paired_diagnostic",
        "--fixture",
        str(FIXTURE),
        "--diagnostic",
        str(passing),
        "--output-dir",
        str(tmp_path / "pass-receipts"),
    )
    fail_result = _run(
        "scripts.qa.paired_diagnostic",
        "--fixture",
        str(FIXTURE),
        "--diagnostic",
        str(failing),
        "--output-dir",
        str(tmp_path / "fail-receipts"),
    )

    assert pass_result.returncode == 0
    assert fail_result.returncode == EXIT_REFUSAL


def test_real_run_requirement_rejects_fixture_v2(tmp_path: Path) -> None:
    """Prevent fixture-mode evidence from satisfying the real-run gate."""
    run_fixture = build_fixture_tree(tmp_path)
    output = tmp_path / "diagnostic.json"
    built = _build(run_fixture, output)
    assert built.returncode == 0

    result = _run(
        "scripts.qa.paired_diagnostic",
        "--fixture",
        str(FIXTURE),
        "--diagnostic",
        str(output),
        "--output-dir",
        str(tmp_path / "receipts"),
        "--required-input-mode",
        "real_run",
    )

    assert result.returncode == EXIT_REFUSAL


def test_rejects_forbidden_artifact_paths(tmp_path: Path) -> None:
    """Reject ASS, prior-run, and speaker-inference artifact paths."""
    attacks = (
        (
            "asr",
            "artifacts/reference.ass",
            {"kind": "asr", "run_id": RUN_ID},
        ),
        (
            "verified_speaker_turns_v3",
            "artifacts/speaker-inference.json",
            {
                "kind": "verified_speaker_turns_v3",
                "run_id": RUN_ID,
                "turn_ids": [
                    f"fixture-turn-cmp-{index}" for index in range(19)
                ],
            },
        ),
        (
            "candidate_transcript",
            "prior-run/candidate-transcript.json",
            {
                "kind": "candidate_transcript",
                "run_id": RUN_ID,
                "cue_ids": [f"fixture-cue-cmp-{index}" for index in range(19)],
            },
        ),
    )
    for index, (kind, relative_path, document) in enumerate(attacks):
        run_fixture = build_fixture_tree(tmp_path / f"attack-{index}")
        payload = (
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
        replace_artifact(
            run_fixture,
            kind=kind,
            relative_path=relative_path,
            payload=payload,
        )
        output = tmp_path / f"forbidden-{index}.json"

        result = _build(run_fixture, output)

        assert result.returncode == EXIT_REFUSAL
        assert _error_code(result) == "forbidden_evidence"
        assert not output.exists()


def test_rejects_foreign_artifact_run_identity(tmp_path: Path) -> None:
    """Reject artifact bytes whose run identity differs from the fixture run."""
    run_fixture = build_fixture_tree(tmp_path)
    fixture_run = load_fixture_run(run_fixture)
    candidate = next(
        item
        for item in fixture_run.artifacts
        if item.kind == "candidate_transcript"
    )
    document = _JSON_DICT.validate_json(
        (run_fixture / candidate.relative_path).read_bytes()
    )
    document["run_id"] = FOREIGN_RUN_ID
    payload = (
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    replace_artifact(
        run_fixture,
        kind="candidate_transcript",
        relative_path=candidate.relative_path,
        payload=payload,
    )
    output = tmp_path / "foreign-artifact.json"

    result = _build(run_fixture, output)

    assert result.returncode == EXIT_REFUSAL
    assert _error_code(result) == "foreign_run"
    assert not output.exists()


def test_evaluator_rejects_tampered_v2_input(tmp_path: Path) -> None:
    """Reject outcome, manifest, and assessment tampering after build."""
    run_fixture = build_fixture_tree(tmp_path)
    output = tmp_path / "diagnostic.json"
    assert _build(run_fixture, output).returncode == 0
    original = _payload(output)
    attacks: tuple[tuple[str, dict[str, JsonValue]], ...] = (
        ("manifest", deepcopy(original)),
        ("assessment", deepcopy(original)),
        ("outcomes", deepcopy(original)),
        ("safety", deepcopy(original)),
    )
    for label, payload in attacks:
        evidence = _evidence(payload)
        if label == "manifest":
            manifest = _JSON_LIST.validate_python(evidence["artifact_manifest"])
            evidence["artifact_manifest"] = list(reversed(manifest))
        elif label == "assessment":
            evidence["assessment_author_payload_sha256"] = "f" * 64
        elif label == "outcomes":
            outcomes = _JSON_DICT.validate_python(payload["outcomes"])
            outcomes["correct_meaning_case_ids"] = []
            payload["outcomes"] = outcomes
        else:
            safety = _JSON_DICT.validate_python(payload["safety_counts"])
            safety["unsupported_additions_before"] = 999
            payload["safety_counts"] = safety
        _rebind_v2_input(payload)
        payload["evidence"] = evidence
        tampered = tmp_path / f"tampered-{label}.json"
        _ = tampered.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        result = _evaluate(
            tampered,
            tmp_path / f"receipt-{label}",
            run_fixture,
        )

        assert result.returncode == EXIT_REFUSAL
        assert "No such option" not in result.stderr
        receipt = (
            tmp_path
            / f"receipt-{label}"
            / f"{tampered.stem}.receipt.json"
        )
        assert not receipt.exists()


def test_rejects_unapproved_review_and_unsupported_character(
    tmp_path: Path,
) -> None:
    """Reject review refusal and character traces absent from cited evidence."""
    review_fixture = build_fixture_tree(tmp_path / "review")
    review_assessment = load_assessment(review_fixture)
    rejected_review = review_assessment.review.model_copy(
        update={"decision": "reject"}
    )
    write_assessment(
        review_fixture,
        review_assessment.model_copy(update={"review": rejected_review}),
    )
    review_output = tmp_path / "review.json"
    review_result = _build(review_fixture, review_output)
    assert review_result.returncode == EXIT_REFUSAL
    assert _error_code(review_result) == "invalid_assessment"
    assert not review_output.exists()

    trace_fixture = build_fixture_tree(tmp_path / "trace")
    trace_assessment = load_assessment(trace_fixture)
    traced_index = next(
        index
        for index, case in enumerate(trace_assessment.author.cases)
        if case.character_traces
    )
    traced_case = trace_assessment.author.cases[traced_index]
    invalid_trace = traced_case.character_traces[0].model_copy(
        update={"source_value": "불일치"}
    )
    invalid_case = traced_case.model_copy(
        update={"character_traces": (invalid_trace,)}
    )
    cases = list(trace_assessment.author.cases)
    cases[traced_index] = invalid_case
    author = trace_assessment.author.model_copy(update={"cases": tuple(cases)})
    write_assessment(
        trace_fixture,
        trace_assessment.model_copy(update={"author": author}),
    )
    trace_output = tmp_path / "trace.json"
    trace_result = _build(trace_fixture, trace_output)
    assert trace_result.returncode == EXIT_REFUSAL
    assert _error_code(trace_result) == "invalid_assessment"
    assert not trace_output.exists()


def test_rejects_unresolved_candidate_and_turn_mappings(tmp_path: Path) -> None:
    """Reject reviewed mappings absent from candidate or verified-turn bytes."""
    for label in ("candidate", "turn"):
        run_fixture = build_fixture_tree(tmp_path / label)
        assessment = load_assessment(run_fixture)
        first_case = assessment.author.cases[0]
        first_mapping = first_case.interval_mappings[0]
        update = (
            {"candidate_cue_id": "missing-candidate-cue"}
            if label == "candidate"
            else {"authority_turn_ids": ("missing-authority-turn",)}
        )
        invalid_mapping = first_mapping.model_copy(update=update)
        invalid_case = first_case.model_copy(
            update={"interval_mappings": (invalid_mapping,)}
        )
        author = assessment.author.model_copy(
            update={"cases": (invalid_case, *assessment.author.cases[1:])}
        )
        rebound = rebind_assessment_author(
            assessment.model_copy(update={"author": author})
        )
        write_assessment(run_fixture, rebound)
        output = tmp_path / f"missing-{label}.json"

        result = _build(run_fixture, output)

        assert result.returncode == EXIT_REFUSAL
        assert _error_code(result) == "invalid_assessment"
        assert not output.exists()
