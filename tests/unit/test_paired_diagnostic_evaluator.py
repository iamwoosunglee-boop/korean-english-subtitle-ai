# Copyright 2026 Transcription Module contributors

"""Task 16 paired-diagnostic acceptance behavior."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError
from scripts import qa

FROZEN_FIXTURE = Path("tests/fixtures/visual-correction-regressions.v1.json")
PASS_INPUT = Path("tests/fixtures/paired-diagnostic-pass.v1.json")
FAIL_INPUT = Path("tests/fixtures/paired-diagnostic-fail.v1.json")
EXPECTED_CASE_COUNT = 17
EXPECTED_INTERVAL_COUNT = 19
EXPECTED_PAIR_COUNT = 2
EXPECTED_TRACE_COUNT = 3
_JSON_DICT = TypeAdapter(dict[str, JsonValue])
_JSON_LIST = TypeAdapter(list[JsonValue])


def _metric(receipt: qa.AcceptanceReceipt, metric_id: str) -> qa.MetricReceipt:
    return {metric.metric_id.value: metric for metric in receipt.metrics}[metric_id]


def _write_variant(tmp_path: Path, payload: dict[str, JsonValue]) -> Path:
    path = tmp_path / "diagnostic.json"
    _ = path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _pass_payload() -> dict[str, JsonValue]:
    return _JSON_DICT.validate_json(PASS_INPUT.read_bytes())


def _json_dict(value: JsonValue) -> dict[str, JsonValue]:
    return _JSON_DICT.validate_python(value)


def _json_list(value: JsonValue) -> list[JsonValue]:
    return _JSON_LIST.validate_python(value)


def test_fixed_passing_dataset_emits_exact_acceptance_receipt() -> None:
    """Given aligned passing evidence, the receipt passes every frozen gate."""
    receipt = qa.evaluate_paired_diagnostic(FROZEN_FIXTURE, PASS_INPUT)

    assert receipt.overall_status.value == "pass"
    assert receipt.candidate_run.label == "policy-clean paired diagnostic"
    assert receipt.baseline_run.contamination.value == "contaminated"
    assert receipt.generalization_allowed is False
    assert receipt.alignment.aligned_cases == EXPECTED_CASE_COUNT
    assert receipt.alignment.aligned_intervals == EXPECTED_INTERVAL_COUNT
    assert _metric(receipt, "meaning_accuracy").rate == "17/17"
    assert _metric(receipt, "mandatory_omission").rate == "0/4"
    assert (
        _metric(receipt, "visual_correction").threshold_count
        == EXPECTED_PAIR_COUNT
    )
    assert (
        _metric(receipt, "visual_gap_insertion").threshold_count
        == EXPECTED_PAIR_COUNT
    )
    assert _metric(receipt, "mandatory_omission_reduction").rate == "4/4"
    assert receipt.correction_trace.rate == "3/3"
    assert receipt.safety.passed is True


def test_fixed_failing_dataset_fails_effectiveness_safety_and_traceability() -> None:
    """Given real metric misses, no stronger metric can compensate for them."""
    receipt = qa.evaluate_paired_diagnostic(FROZEN_FIXTURE, FAIL_INPUT)

    assert receipt.overall_status.value == "fail"
    assert _metric(receipt, "meaning_accuracy").passed is False
    assert _metric(receipt, "mandatory_omission").passed is False
    assert _metric(receipt, "visual_correction").passed is False
    assert _metric(receipt, "visual_gap_insertion").passed is False
    assert _metric(receipt, "mandatory_omission_reduction").passed is False
    assert receipt.correction_trace.untraced_character_ids == ("char-insert-1",)
    assert receipt.safety.counts.critical_wrong_owner == 1
    assert receipt.safety.passed is False


def test_missing_correction_population_keeps_frozen_denominator(
    tmp_path: Path,
) -> None:
    """Given missing correction alignment, its frozen denominator remains two."""
    payload = _pass_payload()
    claims = _json_list(payload["alignment_claims"])
    payload["alignment_claims"] = [
        claim
        for claim in claims
        if _json_dict(claim)["case_id"]
        not in {"local-span-repair", "full-cue-repair"}
    ]

    receipt = qa.evaluate_paired_diagnostic(
        FROZEN_FIXTURE,
        _write_variant(tmp_path, payload),
    )
    metric = _metric(receipt, "visual_correction")

    assert metric.numerator == 0
    assert metric.denominator == EXPECTED_PAIR_COUNT
    assert metric.rate == "0/2"
    assert metric.status.value == "counted"
    assert metric.passed is False
    assert receipt.alignment.population_complete is False
    assert receipt.overall_status.value == "fail"


def test_raw_cue_count_comparison_is_rejected(tmp_path: Path) -> None:
    """Given cue-count alignment, the typed input boundary rejects scoring."""
    payload = _pass_payload()
    payload["comparison_method"] = "raw_cue_counts"

    with pytest.raises(ValidationError):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


def test_missing_policy_digest_is_rejected(tmp_path: Path) -> None:
    """Given unbound evidence, evaluation cannot issue an acceptance receipt."""
    payload = _pass_payload()
    del payload["policy_digest"]

    with pytest.raises(ValidationError):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


def test_contaminated_baseline_cannot_masquerade_as_clean(tmp_path: Path) -> None:
    """Given a contaminated arm, a clean diagnostic label is invalid."""
    payload = _pass_payload()
    baseline = _json_dict(payload["baseline_run"])
    baseline["label"] = "policy-clean paired diagnostic"
    payload["baseline_run"] = baseline

    with pytest.raises(ValidationError):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


def test_ambiguous_and_tied_interval_rows_are_excluded_as_unknown(
    tmp_path: Path,
) -> None:
    """Given duplicate and equal-time claims, neither enters a denominator."""
    payload = _pass_payload()
    claims = _json_list(payload["alignment_claims"])
    duplicate = deepcopy(_json_dict(claims[0]))
    ambiguous = deepcopy(_json_dict(claims[8]))
    ambiguous["source_cue_ids"] = ["unknown-source"]
    ambiguous["speaker"] = "unknown-speaker"
    ambiguous["turn_id"] = "unknown-turn"
    claims.append(duplicate)
    claims[8] = ambiguous
    payload["alignment_claims"] = claims

    receipt = qa.evaluate_paired_diagnostic(
        FROZEN_FIXTURE,
        _write_variant(tmp_path, payload),
    )

    assert receipt.alignment.tied_rows == EXPECTED_PAIR_COUNT
    assert receipt.alignment.ambiguous_rows == 1
    assert receipt.alignment.excluded_cases == EXPECTED_PAIR_COUNT
    assert receipt.alignment.unknown_cases == EXPECTED_PAIR_COUNT
    assert (
        receipt.alignment.unmatched_reference_intervals
        == EXPECTED_PAIR_COUNT
    )


def test_unsupported_changed_character_value_is_rejected(tmp_path: Path) -> None:
    """Given a character absent from its source value, trace input is invalid."""
    payload = _pass_payload()
    outcomes = _json_dict(payload["outcomes"])
    traces = _json_list(outcomes["correction_trace"])
    first_trace = _json_dict(traces[0])
    first_trace["value"] = "못"
    traces[0] = first_trace
    outcomes["correction_trace"] = traces
    payload["outcomes"] = outcomes

    with pytest.raises(ValidationError):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


@pytest.mark.parametrize(
    ("metric_id", "denominator", "expected"),
    [
        (qa.MetricId.MEANING_ACCURACY, 20, 19),
        (qa.MetricId.MEANING_ACCURACY, 21, 20),
        (qa.MetricId.MANDATORY_OMISSION, 19, 0),
        (qa.MetricId.MANDATORY_OMISSION, 20, 1),
        (qa.MetricId.VISUAL_CORRECTION, 6, 5),
        (qa.MetricId.VISUAL_GAP_INSERTION, 6, 5),
    ],
)
def test_threshold_counts_are_rounded_in_the_approved_direction(
    metric_id: qa.MetricId,
    denominator: int,
    expected: int,
) -> None:
    """Given boundary denominators, ceil and floor cannot drift by one."""
    assert qa.threshold_for(metric_id, denominator) == expected


def test_incomplete_traceability_is_exact_and_never_passes() -> None:
    """Given one untraced character, the 100 percent safety gate fails."""
    receipt = qa.evaluate_paired_diagnostic(FROZEN_FIXTURE, FAIL_INPUT)

    assert receipt.correction_trace.numerator == EXPECTED_PAIR_COUNT
    assert receipt.correction_trace.denominator == EXPECTED_TRACE_COUNT
    assert receipt.correction_trace.rate == "2/3"
    assert receipt.correction_trace.passed is False


def test_receipt_and_sha256_manifest_are_deterministic() -> None:
    """Given identical bytes, evaluation emits an identical verifiable receipt."""
    first = qa.evaluate_paired_diagnostic(FROZEN_FIXTURE, PASS_INPUT)
    second = qa.evaluate_paired_diagnostic(FROZEN_FIXTURE, PASS_INPUT)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.manifest.fixture_sha256 == hashlib.sha256(
        FROZEN_FIXTURE.read_bytes()
    ).hexdigest()
    assert first.manifest.diagnostic_input_sha256 == hashlib.sha256(
        PASS_INPUT.read_bytes()
    ).hexdigest()
    assert qa.verify_receipt_manifest(first, FROZEN_FIXTURE, PASS_INPUT) is True
