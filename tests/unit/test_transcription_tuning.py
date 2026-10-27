# Copyright 2026 Transcription Module contributors

"""Deterministic SFW-reference chunk and evidence-layout tuning."""

from pathlib import Path

import pytest

from transcription_module.tuning import (
    CandidateObservation,
    TuningAggregateExperiment,
    TuningCandidate,
    TuningExperiment,
    TuningPlan,
    evaluate_tuning,
    evaluate_tuning_aggregates,
    write_tuning_plan,
    write_tuning_report,
)

REFERENCE_COUNT = 2
HALF_RATE_PPM = 500_000


def _candidate(
    candidate_id: str,
    chunk_ms: int,
    evidence_layout: tuple[str, ...],
) -> TuningCandidate:
    return TuningCandidate(
        candidate_id=candidate_id,
        run_id=f"run-{candidate_id}",
        transcript_digest="c" * 64,
        chunk_ms=chunk_ms,
        evidence_layout=evidence_layout,
    )


def _experiment(
    candidate_id: str,
    observations: tuple[CandidateObservation, ...],
    cost_units: int,
) -> TuningExperiment:
    return TuningExperiment(
        candidate_id=candidate_id,
        observations=observations,
        cost_units=cost_units,
        elapsed_ms=1_000,
    )


def test_plan_rejects_candidate_that_changes_two_variables() -> None:
    """Every candidate changes chunk size or evidence layout, never both."""
    baseline = _candidate("baseline", 120_000, ("asr", "ko-orig"))

    with pytest.raises(ValueError, match="exactly one variable"):
        _ = TuningPlan(
            reference_digest="a" * 64,
            reference_cue_ids=("sfw-1",),
            baseline_candidate_id=baseline.candidate_id,
            candidates=(
                baseline,
                _candidate("invalid", 60_000, ("asr", "ko-orig", "frames")),
            ),
        )


def test_fixed_denominators_metrics_and_best_candidate_are_deterministic(
    tmp_path: Path,
) -> None:
    """One frozen SFW plan yields complete metrics and one stable winner."""
    baseline = _candidate("baseline", 120_000, ("asr", "ko-orig"))
    shorter = _candidate("shorter", 60_000, baseline.evidence_layout)
    frames = _candidate(
        "frames",
        baseline.chunk_ms,
        ("asr", "ko-orig", "frames"),
    )
    plan = TuningPlan(
        reference_digest="a" * 64,
        reference_cue_ids=("sfw-1", "sfw-2"),
        baseline_candidate_id=baseline.candidate_id,
        candidates=(baseline, shorter, frames),
    )
    path = tmp_path / "tuning-plan.json"
    write_tuning_plan(path, plan)
    before = path.read_bytes()
    baseline_observations = (
        CandidateObservation(
            reference_cue_id="sfw-1",
            meaning_preserved=True,
            required_meaning_missing=False,
            speaker_correct=True,
            unsupported_additions=0,
        ),
        CandidateObservation(
            reference_cue_id="sfw-2",
            meaning_preserved=False,
            required_meaning_missing=True,
            speaker_correct=False,
            unsupported_additions=1,
        ),
    )
    improved = tuple(
        item.model_copy(
            update={
                "meaning_preserved": True,
                "required_meaning_missing": False,
                "speaker_correct": True,
                "unsupported_additions": 0,
            }
        )
        for item in baseline_observations
    )

    report = evaluate_tuning(
        plan,
        (
            _experiment("baseline", baseline_observations, 900),
            _experiment("shorter", improved, 800),
            _experiment("frames", improved, 1_200),
        ),
    )

    assert path.read_bytes() == before
    assert report.selected_candidate_id == "shorter"
    baseline_metrics = report.results[0].metrics
    assert baseline_metrics.denominator == REFERENCE_COUNT
    assert baseline_metrics.meaning_accuracy_ppm == HALF_RATE_PPM
    assert baseline_metrics.required_omission_count == 1
    assert baseline_metrics.speaker_accuracy_ppm == HALF_RATE_PPM
    assert baseline_metrics.unsupported_addition_count == 1
    report_path = tmp_path / "tuning-report.json"
    write_tuning_report(report_path, report)
    assert report_path.read_text(encoding="utf-8").endswith("\n")


def test_evaluation_rejects_missing_fixed_reference_observation() -> None:
    """An experiment cannot silently omit a hard SFW reference cue."""
    baseline = _candidate("baseline", 120_000, ("asr",))
    plan = TuningPlan(
        reference_digest="b" * 64,
        reference_cue_ids=("sfw-1", "sfw-2"),
        baseline_candidate_id=baseline.candidate_id,
        candidates=(baseline,),
    )
    incomplete = (
        CandidateObservation(
            reference_cue_id="sfw-1",
            meaning_preserved=True,
            required_meaning_missing=False,
            speaker_correct=True,
            unsupported_additions=0,
        ),
    )

    with pytest.raises(ValueError, match="fixed reference"):
        _ = evaluate_tuning(
            plan,
            (_experiment("baseline", incomplete, 100),),
        )


def test_aggregate_evaluation_uses_the_same_frozen_denominator() -> None:
    """Segment-reviewed aggregates produce the same deterministic selection."""
    baseline = _candidate("baseline", 120_000, ("asr",))
    frames = _candidate("frames", 120_000, ("asr", "frames"))
    plan = TuningPlan(
        reference_digest="d" * 64,
        reference_cue_ids=("sfw-1", "sfw-2"),
        baseline_candidate_id=baseline.candidate_id,
        candidates=(baseline, frames),
    )
    summaries = (
        TuningAggregateExperiment(
            candidate_id="baseline",
            denominator=2,
            meaning_preserved_count=1,
            required_omission_count=1,
            speaker_correct_count=1,
            unsupported_addition_count=1,
            cost_units=100,
            elapsed_ms=10,
            assessment_digest="e" * 64,
        ),
        TuningAggregateExperiment(
            candidate_id="frames",
            denominator=2,
            meaning_preserved_count=2,
            required_omission_count=0,
            speaker_correct_count=2,
            unsupported_addition_count=0,
            cost_units=120,
            elapsed_ms=12,
            assessment_digest="f" * 64,
        ),
    )

    report = evaluate_tuning_aggregates(plan, summaries)

    assert report.selected_candidate_id == "frames"
