# Copyright 2026 Transcription Module contributors
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pydantic>=2.12,<3",
#     "typer>=0.21,<1",
# ]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run from the repository root:
#      uv run python -m scripts.qa.paired_diagnostic --help
# 3. Evaluate one or more fixed inputs with --diagnostic and --output-dir.
# ──────────────────

"""Deterministic evaluator for same-video policy-clean paired diagnostics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 - Typer runtime field
from typing import Annotated, Final, override

import typer
from pydantic import Field, TypeAdapter, ValidationError
from tests.visual_correction_fixture_support import (
    VisualCorrectionFixture,
    load_visual_correction_fixture,
)

from scripts.qa.paired_diagnostic_models import (
    AcceptanceReceipt,
    AlignmentAudit,
    CorrectionTraceAudit,
    MetricId,
    MetricReceipt,
    MetricStatus,
    OverallStatus,
    PairedDiagnosticInput,
    SafetyAudit,
    Sha256Manifest,
    ThresholdRelation,
)
from scripts.qa.real_diagnostic_fixture_builder import (
    DiagnosticBuildError,
    build_fixture_input,
    canonical_model_bytes,
)
from scripts.qa.real_diagnostic_fixture_models import (
    EvidencePairedDiagnosticInput,
    InputMode,
)

_SHA_PLACEHOLDER: Final = "0" * 64
app = typer.Typer(add_completion=False)
type DiagnosticInput = Annotated[
    PairedDiagnosticInput | EvidencePairedDiagnosticInput,
    Field(discriminator="schema_version"),
]
_DIAGNOSTIC_INPUT_ADAPTER: Final[TypeAdapter[DiagnosticInput]] = TypeAdapter(
    DiagnosticInput
)


@dataclass(frozen=True, slots=True)
class PairedDiagnosticError(Exception):
    """Typed evaluator contract failure."""

    reason: str

    @override
    def __str__(self) -> str:
        """Render the evaluator failure for the CLI boundary."""
        return self.reason


@dataclass(frozen=True, slots=True)
class _AlignedPopulation:
    case_ids: frozenset[str]
    audit: AlignmentAudit


def threshold_for(metric_id: MetricId, denominator: int) -> int:
    """Return the approved integer threshold without floating-point drift."""
    percent, rounds_up = {
        MetricId.MEANING_ACCURACY: (95, True),
        MetricId.MANDATORY_OMISSION: (5, False),
        MetricId.VISUAL_CORRECTION: (80, True),
        MetricId.VISUAL_GAP_INSERTION: (80, True),
        MetricId.MANDATORY_OMISSION_REDUCTION: (50, True),
    }[metric_id]
    return (
        (percent * denominator + 99) // 100
        if rounds_up
        else (percent * denominator) // 100
    )


def _metric(
    metric_id: MetricId,
    numerator: int,
    denominator: int,
) -> MetricReceipt:
    relation = {
        MetricId.MEANING_ACCURACY: ThresholdRelation.AT_LEAST,
        MetricId.MANDATORY_OMISSION: ThresholdRelation.AT_MOST,
        MetricId.VISUAL_CORRECTION: ThresholdRelation.AT_LEAST,
        MetricId.VISUAL_GAP_INSERTION: ThresholdRelation.AT_LEAST,
        MetricId.MANDATORY_OMISSION_REDUCTION: ThresholdRelation.AT_LEAST,
    }[metric_id]
    threshold = threshold_for(metric_id, denominator)
    if denominator == 0:
        return MetricReceipt(
            metric_id=metric_id,
            numerator=numerator,
            denominator=denominator,
            rate="N/A",
            status=MetricStatus.NOT_APPLICABLE,
            threshold_relation=relation,
            threshold_count=threshold,
            passed=False,
        )
    passed = (
        numerator <= threshold
        if relation is ThresholdRelation.AT_MOST
        else numerator >= threshold
    )
    return MetricReceipt(
        metric_id=metric_id,
        numerator=numerator,
        denominator=denominator,
        rate=f"{numerator}/{denominator}",
        status=MetricStatus.COUNTED,
        threshold_relation=relation,
        threshold_count=threshold,
        passed=passed,
    )


def _align(
    fixture: VisualCorrectionFixture,
    data: DiagnosticInput,
) -> _AlignedPopulation:
    intervals = fixture.reference_only.comparable_intervals
    expected_by_case = {
        case.case_id: frozenset(case.comparable_interval_ids)
        for case in fixture.cases
    }
    interval_keys = {
        (
            interval.interval_id,
            interval.source_identity.source_cue_ids,
            tuple(interval.source_identity.speaker.split("|")),
            interval.source_identity.turn_id,
            interval.start_ms,
            interval.end_ms,
        ): interval
        for interval in intervals
    }
    exact_claims: dict[str, list[str]] = {}
    ambiguous_rows = 0
    unmatched_rows = 0
    for claim in data.alignment_claims:
        key = (
            claim.interval_id,
            claim.source_cue_ids,
            claim.speakers,
            claim.turn_id,
            claim.start_ms,
            claim.end_ms,
        )
        interval = interval_keys.get(key)
        if interval is not None and claim.case_id in expected_by_case:
            exact_claims.setdefault(interval.interval_id, []).append(claim.case_id)
            continue
        same_time = tuple(
            item
            for item in intervals
            if item.start_ms == claim.start_ms and item.end_ms == claim.end_ms
        )
        if len(same_time) > 1:
            ambiguous_rows += 1
        else:
            unmatched_rows += 1

    aligned_by_case: dict[str, set[str]] = {}
    aligned_interval_ids: set[str] = set()
    tied_rows = 0
    for interval_id, case_ids in exact_claims.items():
        if len(case_ids) > 1:
            tied_rows += len(case_ids)
            continue
        case_id = case_ids[0]
        if interval_id in expected_by_case[case_id]:
            aligned_by_case.setdefault(case_id, set()).add(interval_id)
            aligned_interval_ids.add(interval_id)
        else:
            unmatched_rows += 1

    aligned_cases = frozenset(
        case_id
        for case_id, expected_ids in expected_by_case.items()
        if frozenset(aligned_by_case.get(case_id, set())) == expected_ids
    )
    unknown_cases = len(expected_by_case) - len(aligned_cases)
    return _AlignedPopulation(
        case_ids=aligned_cases,
        audit=AlignmentAudit(
            reference_cases=len(expected_by_case),
            reference_intervals=len(intervals),
            input_rows=len(data.alignment_claims),
            population_complete=not any(
                (
                    ambiguous_rows,
                    tied_rows,
                    unmatched_rows,
                    unknown_cases,
                    len(intervals) - len(aligned_interval_ids),
                )
            ),
            aligned_cases=len(aligned_cases),
            aligned_intervals=len(aligned_interval_ids),
            ambiguous_rows=ambiguous_rows,
            tied_rows=tied_rows,
            unmatched_rows=unmatched_rows,
            excluded_cases=unknown_cases,
            unknown_cases=unknown_cases,
            unmatched_reference_intervals=(
                len(intervals) - len(aligned_interval_ids)
            ),
        ),
    )


def _population(
    fixture: VisualCorrectionFixture,
    metric_id: MetricId,
) -> frozenset[str]:
    population_id = {
        MetricId.MEANING_ACCURACY: MetricId.MEANING_ACCURACY,
        MetricId.MANDATORY_OMISSION: MetricId.MANDATORY_OMISSION,
        MetricId.VISUAL_CORRECTION: MetricId.VISUAL_CORRECTION,
        MetricId.VISUAL_GAP_INSERTION: MetricId.VISUAL_GAP_INSERTION,
        MetricId.MANDATORY_OMISSION_REDUCTION: MetricId.MANDATORY_OMISSION,
    }[metric_id]
    for population in fixture.metric_populations:
        if population.metric_id == population_id.value:
            return frozenset(population.case_ids)
    raise PairedDiagnosticError(
        reason=f"missing frozen population: {population_id.value}"
    )


def _semantic_payload_bytes(receipt: AcceptanceReceipt) -> bytes:
    payload = receipt.model_dump(mode="json", exclude={"manifest"})
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_input_mode(
    data: DiagnosticInput,
    required_input_mode: InputMode | None,
) -> None:
    if required_input_mode is None:
        return
    if isinstance(data, PairedDiagnosticInput):
        raise PairedDiagnosticError(
            reason="required input mode rejects unmodeled v1 diagnostic"
        )
    if data.input_mode is not required_input_mode:
        raise PairedDiagnosticError(
            reason=(
                f"required input mode is {required_input_mode.value}, "
                f"received {data.input_mode.value}"
            )
        )


def _verify_v2_source_replay(
    data: DiagnosticInput,
    fixture_path: Path,
    diagnostic_path: Path,
    run_fixture_path: Path | None,
) -> None:
    if isinstance(data, PairedDiagnosticInput):
        if run_fixture_path is not None:
            raise PairedDiagnosticError(
                reason="v1 diagnostic does not accept a fixture authority root"
            )
        return
    if data.input_mode is InputMode.REAL_RUN:
        raise PairedDiagnosticError(
            reason="real-run source replay is unavailable until authority v2/v3"
        )
    if run_fixture_path is None:
        raise PairedDiagnosticError(
            reason="fixture v2 diagnostic requires --run-fixture source replay"
        )
    rebuilt = build_fixture_input(fixture_path, run_fixture_path)
    canonical_input = canonical_model_bytes(data)
    if diagnostic_path.read_bytes() != canonical_input:
        raise PairedDiagnosticError(reason="v2 diagnostic bytes are not canonical")
    if canonical_input != canonical_model_bytes(rebuilt):
        raise PairedDiagnosticError(
            reason="v2 diagnostic differs from authoritative source replay"
        )


def evaluate_paired_diagnostic(
    fixture_path: Path,
    diagnostic_path: Path,
    *,
    required_input_mode: InputMode | None = None,
    run_fixture_path: Path | None = None,
) -> AcceptanceReceipt:
    """Evaluate one typed diagnostic against frozen comparable populations."""
    fixture = load_visual_correction_fixture(fixture_path)
    data = _DIAGNOSTIC_INPUT_ADAPTER.validate_json(
        diagnostic_path.read_text(encoding="utf-8")
    )
    _require_input_mode(data, required_input_mode)
    _verify_v2_source_replay(
        data,
        fixture_path,
        diagnostic_path,
        run_fixture_path,
    )
    if data.alignment_basis != fixture.reference_only.alignment_basis:
        raise PairedDiagnosticError(reason="alignment basis differs from fixture")
    known_cases = {case.case_id for case in fixture.cases}
    input_case_ids = {
        claim.case_id for claim in data.alignment_claims
    } | {
        item.case_id for item in data.outcomes.correction_trace
    }
    correct_meaning = set(data.outcomes.correct_meaning_case_ids)
    missing_before_outcomes = set(
        data.outcomes.mandatory_missing_before_case_ids
    )
    missing_after_outcomes = set(
        data.outcomes.mandatory_missing_after_case_ids
    )
    auto_corrected = set(data.outcomes.auto_corrected_case_ids)
    auto_inserted = set(data.outcomes.auto_inserted_case_ids)
    outcome_case_ids = (
        correct_meaning
        | missing_before_outcomes
        | missing_after_outcomes
        | auto_corrected
        | auto_inserted
    )
    if not input_case_ids | outcome_case_ids <= known_cases:
        raise PairedDiagnosticError(reason="diagnostic references an unknown case")

    aligned = _align(fixture, data)
    meaning_cases = _population(fixture, MetricId.MEANING_ACCURACY)
    mandatory_cases = _population(fixture, MetricId.MANDATORY_OMISSION)
    correction_cases = _population(fixture, MetricId.VISUAL_CORRECTION)
    insertion_cases = _population(fixture, MetricId.VISUAL_GAP_INSERTION)
    if not (
        correct_meaning <= meaning_cases
        and missing_before_outcomes <= mandatory_cases
        and missing_after_outcomes <= mandatory_cases
        and auto_corrected <= correction_cases
        and auto_inserted <= insertion_cases
    ):
        raise PairedDiagnosticError(
            reason="diagnostic outcome references case outside frozen population"
        )
    missing_before = aligned.case_ids & mandatory_cases & missing_before_outcomes
    missing_after = aligned.case_ids & mandatory_cases & missing_after_outcomes
    metrics = (
        _metric(
            MetricId.MEANING_ACCURACY,
            len(aligned.case_ids & meaning_cases & correct_meaning),
            len(meaning_cases),
        ),
        _metric(
            MetricId.MANDATORY_OMISSION,
            len(missing_after),
            len(mandatory_cases),
        ),
        _metric(
            MetricId.VISUAL_CORRECTION,
            len(aligned.case_ids & correction_cases & auto_corrected),
            len(correction_cases),
        ),
        _metric(
            MetricId.VISUAL_GAP_INSERTION,
            len(aligned.case_ids & insertion_cases & auto_inserted),
            len(insertion_cases),
        ),
        _metric(
            MetricId.MANDATORY_OMISSION_REDUCTION,
            len(missing_before) - len(missing_after),
            len(mandatory_cases),
        ),
    )
    untraced = tuple(
        item.character_id
        for item in data.outcomes.correction_trace
        if item.source_evidence_id is None
    )
    trace_denominator = data.outcomes.changed_character_count
    trace_numerator = trace_denominator - len(untraced)
    trace = CorrectionTraceAudit(
        numerator=trace_numerator,
        denominator=trace_denominator,
        rate=(
            "N/A"
            if trace_denominator == 0
            else f"{trace_numerator}/{trace_denominator}"
        ),
        untraced_character_ids=untraced,
        passed=trace_denominator > 0 and trace_numerator == trace_denominator,
    )
    safety_total = (
        data.safety_counts.disallowed_track_contribution
        + data.safety_counts.non_speech_unknown_elevation
        + data.safety_counts.user_confirmed_overwrite
        + data.safety_counts.critical_wrong_owner
        + data.safety_counts.correct_to_wrong_auto_damage
    )
    safety = SafetyAudit(
        counts=data.safety_counts,
        passed=(
            safety_total == 0
            and data.safety_counts.unsupported_additions_after
            <= data.safety_counts.unsupported_additions_before
        ),
    )
    overall = (
        OverallStatus.PASS
        if (
            all(metric.passed for metric in metrics)
            and safety.passed
            and trace.passed
            and aligned.audit.population_complete
        )
        else OverallStatus.FAIL
    )
    receipt = AcceptanceReceipt(
        schema_version="paired-diagnostic-receipt.v1",
        evaluation_kind="policy-clean paired diagnostic",
        comparison_method="comparable_intervals",
        alignment_basis="source_identity_time_speaker",
        scope="same_video_only",
        generalization_allowed=False,
        baseline_run=data.baseline_run,
        candidate_run=data.candidate_run,
        policy_digest=data.policy_digest,
        alignment=aligned.audit,
        metrics=metrics,
        safety=safety,
        correction_trace=trace,
        overall_status=overall,
        manifest=Sha256Manifest(
            fixture_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            diagnostic_input_sha256=hashlib.sha256(
                diagnostic_path.read_bytes()
            ).hexdigest(),
            receipt_payload_sha256=_SHA_PLACEHOLDER,
        ),
    )
    return receipt.model_copy(
        update={
            "manifest": receipt.manifest.model_copy(
                update={
                    "receipt_payload_sha256": hashlib.sha256(
                        _semantic_payload_bytes(receipt)
                    ).hexdigest()
                }
            )
        }
    )


def verify_receipt_manifest(
    receipt: AcceptanceReceipt,
    fixture_path: Path,
    diagnostic_path: Path,
) -> bool:
    """Verify both input hashes and the canonical semantic receipt hash."""
    return (
        receipt.manifest.fixture_sha256
        == hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        and receipt.manifest.diagnostic_input_sha256
        == hashlib.sha256(diagnostic_path.read_bytes()).hexdigest()
        and receipt.manifest.receipt_payload_sha256
        == hashlib.sha256(_semantic_payload_bytes(receipt)).hexdigest()
    )


@app.command()
def main(
    fixture: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    diagnostic: Annotated[
        list[Path], typer.Option(exists=True, dir_okay=False)
    ],
    output_dir: Annotated[Path, typer.Option(file_okay=False)],
    required_input_mode: Annotated[
        InputMode | None,
        typer.Option(
            "--required-input-mode",
            help="Require fixture or real-run evidence-bearing v2 input.",
        ),
    ] = None,
    run_fixture: Annotated[
        Path | None,
        typer.Option(
            "--run-fixture",
            exists=True,
            file_okay=False,
            help="Replay the fixture authority root for v2 input.",
        ),
    ] = None,
) -> None:
    """Write one deterministic receipt for every fixed diagnostic input."""
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    statuses: list[OverallStatus] = []
    try:
        for diagnostic_path in diagnostic:
            receipt = evaluate_paired_diagnostic(
                fixture,
                diagnostic_path,
                required_input_mode=required_input_mode,
                run_fixture_path=run_fixture,
            )
            output_path = output_dir / f"{diagnostic_path.stem}.receipt.json"
            _ = output_path.write_text(
                receipt.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            outputs.append(str(output_path))
            statuses.append(receipt.overall_status)
    except (
        DiagnosticBuildError,
        OSError,
        PairedDiagnosticError,
        ValidationError,
    ) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(json.dumps({"receipts": outputs}, sort_keys=True))
    if OverallStatus.FAIL in statuses:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
