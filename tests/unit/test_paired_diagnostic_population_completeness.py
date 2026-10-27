# Copyright 2026 Transcription Module contributors

"""Frozen paired-diagnostic population completeness regressions."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError
from scripts import qa
from scripts.qa.paired_diagnostic import PairedDiagnosticError, app
from typer.testing import CliRunner

FROZEN_FIXTURE = Path("tests/fixtures/visual-correction-regressions.v1.json")
PASS_INPUT = Path("tests/fixtures/paired-diagnostic-pass.v1.json")
EXPECTED_DENOMINATORS = (17, 4, 2, 2, 4)
EXPECTED_TIED_ROWS = 2
EXPECTED_UNKNOWN_CASES = 2
FAILED_DIAGNOSTIC_EXIT_CODE = 2
_JSON_DICT = TypeAdapter(dict[str, JsonValue])
_JSON_LIST = TypeAdapter(list[JsonValue])


def _pass_payload() -> dict[str, JsonValue]:
    return _JSON_DICT.validate_json(PASS_INPUT.read_bytes())


def _json_dict(value: JsonValue) -> dict[str, JsonValue]:
    return _JSON_DICT.validate_python(value)


def _json_list(value: JsonValue) -> list[JsonValue]:
    return _JSON_LIST.validate_python(value)


def _write_variant(tmp_path: Path, payload: dict[str, JsonValue]) -> Path:
    path = tmp_path / "diagnostic.json"
    _ = path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _missing_meaning_payload() -> dict[str, JsonValue]:
    payload = _pass_payload()
    payload["alignment_claims"] = [
        claim
        for claim in _json_list(payload["alignment_claims"])
        if _json_dict(claim)["case_id"] != "condensed-genuine-caption"
    ]
    return payload


def _assert_incomplete(receipt: qa.AcceptanceReceipt) -> None:
    assert tuple(metric.denominator for metric in receipt.metrics) == (
        EXPECTED_DENOMINATORS
    )
    assert receipt.alignment.population_complete is False
    assert receipt.overall_status.value == "fail"


def test_missing_meaning_case_cannot_shrink_frozen_population(
    tmp_path: Path,
) -> None:
    """Given one missing meaning case, the receipt fails against all 17."""
    receipt = qa.evaluate_paired_diagnostic(
        FROZEN_FIXTURE,
        _write_variant(tmp_path, _missing_meaning_payload()),
    )

    _assert_incomplete(receipt)
    assert receipt.alignment.unknown_cases == 1


def test_tied_and_ambiguous_cases_cannot_shrink_frozen_population(
    tmp_path: Path,
) -> None:
    """Given tied and ambiguous claims, excluded cases remain denominator members."""
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

    _assert_incomplete(receipt)
    assert receipt.alignment.tied_rows == EXPECTED_TIED_ROWS
    assert receipt.alignment.ambiguous_rows == 1
    assert receipt.alignment.unknown_cases == EXPECTED_UNKNOWN_CASES


def test_unmatched_unknown_case_cannot_shrink_frozen_population(
    tmp_path: Path,
) -> None:
    """Given one unmatched claim, its unknown case remains a denominator member."""
    payload = _pass_payload()
    claims = _json_list(payload["alignment_claims"])
    unmatched = deepcopy(_json_dict(claims[16]))
    unmatched["source_cue_ids"] = ["unknown-source"]
    claims[16] = unmatched
    payload["alignment_claims"] = claims

    receipt = qa.evaluate_paired_diagnostic(
        FROZEN_FIXTURE,
        _write_variant(tmp_path, payload),
    )

    _assert_incomplete(receipt)
    assert receipt.alignment.unmatched_rows == 1
    assert receipt.alignment.unknown_cases == 1


def test_complete_population_preserves_exact_passing_receipt() -> None:
    """Given complete alignment, every existing frozen success remains unchanged."""
    receipt = qa.evaluate_paired_diagnostic(FROZEN_FIXTURE, PASS_INPUT)

    assert tuple(metric.denominator for metric in receipt.metrics) == (
        EXPECTED_DENOMINATORS
    )
    assert tuple(metric.rate for metric in receipt.metrics) == (
        "17/17",
        "0/4",
        "2/2",
        "2/2",
        "4/4",
    )
    assert receipt.overall_status.value == "pass"


def test_outcome_outside_frozen_metric_population_is_rejected(
    tmp_path: Path,
) -> None:
    """Given invalid metric membership, evaluation fails at the typed boundary."""
    payload = _pass_payload()
    outcomes = _json_dict(payload["outcomes"])
    auto_corrected = _json_list(outcomes["auto_corrected_case_ids"])
    auto_corrected.append("condensed-genuine-caption")
    outcomes["auto_corrected_case_ids"] = auto_corrected
    payload["outcomes"] = outcomes

    with pytest.raises(PairedDiagnosticError):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


def test_duplicate_outcome_case_id_is_rejected(tmp_path: Path) -> None:
    """Given duplicate outcome membership, parsing fails at the typed boundary."""
    payload = _pass_payload()
    outcomes = _json_dict(payload["outcomes"])
    correct_meaning = _json_list(outcomes["correct_meaning_case_ids"])
    correct_meaning.append(correct_meaning[0])
    outcomes["correct_meaning_case_ids"] = correct_meaning
    payload["outcomes"] = outcomes

    with pytest.raises(
        ValidationError,
        match="diagnostic outcome case IDs must be unique",
    ):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


def test_duplicate_changed_character_id_is_rejected(tmp_path: Path) -> None:
    """Given duplicate trace identity, parsing fails at the typed boundary."""
    payload = _pass_payload()
    outcomes = _json_dict(payload["outcomes"])
    correction_trace = _json_list(outcomes["correction_trace"])
    correction_trace.append(deepcopy(correction_trace[0]))
    outcomes["correction_trace"] = correction_trace
    payload["outcomes"] = outcomes

    with pytest.raises(
        ValidationError,
        match="changed character IDs must be unique",
    ):
        _ = qa.evaluate_paired_diagnostic(
            FROZEN_FIXTURE,
            _write_variant(tmp_path, payload),
        )


def test_cli_exits_two_after_writing_incomplete_population_receipt(
    tmp_path: Path,
) -> None:
    """Given incomplete alignment, the real CLI writes evidence and exits two."""
    diagnostic = _write_variant(tmp_path, _missing_meaning_payload())
    output_dir = tmp_path / "receipts"

    result = CliRunner().invoke(
        app,
        [
            "--fixture",
            str(FROZEN_FIXTURE),
            "--diagnostic",
            str(diagnostic),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == FAILED_DIAGNOSTIC_EXIT_CODE
    receipt = qa.AcceptanceReceipt.model_validate_json(
        (output_dir / "diagnostic.receipt.json").read_text(encoding="utf-8")
    )
    _assert_incomplete(receipt)
