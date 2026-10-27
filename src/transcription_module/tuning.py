# Copyright 2026 Transcription Module contributors

"""Fixed-denominator SFW-reference transcription tuning."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Self

from pydantic import Field, model_validator

from transcription_module.models import FrozenModel
from transcription_module.storage import write_model_atomic

if TYPE_CHECKING:
    from pathlib import Path


class TuningCandidate(FrozenModel):
    """One fixed chunk-size and evidence-layout configuration."""

    candidate_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    transcript_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_ms: int = Field(gt=0)
    evidence_layout: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_layout_is_unique(self) -> Self:
        """Reject repeated evidence labels."""
        if len(self.evidence_layout) != len(set(self.evidence_layout)):
            message = "tuning evidence layout must be unique"
            raise ValueError(message)
        return self


class TuningPlan(FrozenModel):
    """Frozen SFW reference, denominator, and one-variable candidates."""

    schema_version: str = "transcription-tuning-plan.v1"
    reference_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_cue_ids: tuple[str, ...] = Field(min_length=1)
    baseline_candidate_id: str = Field(min_length=1)
    candidates: tuple[TuningCandidate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def candidates_change_exactly_one_variable(self) -> Self:
        """Require fixed identities and one changed configuration field."""
        if len(self.reference_cue_ids) != len(set(self.reference_cue_ids)):
            message = "tuning reference cue IDs must be unique"
            raise ValueError(message)
        by_id = {candidate.candidate_id: candidate for candidate in self.candidates}
        if len(by_id) != len(self.candidates):
            message = "tuning candidate IDs must be unique"
            raise ValueError(message)
        baseline = by_id.get(self.baseline_candidate_id)
        if baseline is None:
            message = "tuning baseline candidate is missing"
            raise ValueError(message)
        for candidate in self.candidates:
            if candidate.candidate_id == baseline.candidate_id:
                continue
            differences = (
                candidate.chunk_ms != baseline.chunk_ms,
                candidate.evidence_layout != baseline.evidence_layout,
            )
            if sum(differences) != 1:
                message = "tuning candidate must change exactly one variable"
                raise ValueError(message)
        return self


class CandidateObservation(FrozenModel):
    """One manually adjudicated SFW-reference outcome."""

    reference_cue_id: str = Field(min_length=1)
    meaning_preserved: bool
    required_meaning_missing: bool
    speaker_correct: bool
    unsupported_additions: int = Field(ge=0)

    @model_validator(mode="after")
    def omission_is_not_preserved(self) -> Self:
        """Keep required-meaning omission consistent with semantic accuracy."""
        if self.required_meaning_missing and self.meaning_preserved:
            message = "missing required meaning cannot be marked preserved"
            raise ValueError(message)
        return self


class TuningExperiment(FrozenModel):
    """One candidate's complete observations and measured cost."""

    candidate_id: str = Field(min_length=1)
    observations: tuple[CandidateObservation, ...] = Field(min_length=1)
    cost_units: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def observations_are_unique(self) -> Self:
        """Reject repeated SFW reference observations."""
        identities = tuple(item.reference_cue_id for item in self.observations)
        if len(identities) != len(set(identities)):
            message = "tuning observations must be unique"
            raise ValueError(message)
        return self


class TuningAggregateExperiment(FrozenModel):
    """One independently reviewed fixed-denominator aggregate."""

    candidate_id: str = Field(min_length=1)
    denominator: int = Field(gt=0)
    meaning_preserved_count: int = Field(ge=0)
    required_omission_count: int = Field(ge=0)
    speaker_correct_count: int = Field(ge=0)
    unsupported_addition_count: int = Field(ge=0)
    cost_units: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    assessment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def counts_match_denominator(self) -> Self:
        """Reject aggregates that shrink or overrun the reviewed population."""
        if (
            self.meaning_preserved_count + self.required_omission_count
            != self.denominator
            or self.speaker_correct_count > self.denominator
        ):
            message = "tuning aggregate counts do not match denominator"
            raise ValueError(message)
        return self


class TuningMetrics(FrozenModel):
    """Deterministic fixed-denominator metrics for one candidate."""

    denominator: int = Field(gt=0)
    meaning_accuracy_ppm: int = Field(ge=0, le=1_000_000)
    required_omission_count: int = Field(ge=0)
    speaker_accuracy_ppm: int = Field(ge=0, le=1_000_000)
    unsupported_addition_count: int = Field(ge=0)
    cost_units: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)


class TuningResult(FrozenModel):
    """One evaluated candidate and its complete metrics."""

    candidate: TuningCandidate
    metrics: TuningMetrics


class TuningReport(FrozenModel):
    """Deterministic comparison and selected best configuration."""

    schema_version: str = "transcription-tuning-report.v1"
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: tuple[TuningResult, ...]
    selected_candidate_id: str = Field(min_length=1)


def _plan_digest(plan: TuningPlan) -> str:
    return sha256(plan.model_dump_json().encode()).hexdigest()


def _metrics(experiment: TuningExperiment) -> TuningMetrics:
    denominator = len(experiment.observations)
    meaning = sum(item.meaning_preserved for item in experiment.observations)
    speakers = sum(item.speaker_correct for item in experiment.observations)
    return TuningMetrics(
        denominator=denominator,
        meaning_accuracy_ppm=meaning * 1_000_000 // denominator,
        required_omission_count=sum(
            item.required_meaning_missing for item in experiment.observations
        ),
        speaker_accuracy_ppm=speakers * 1_000_000 // denominator,
        unsupported_addition_count=sum(
            item.unsupported_additions for item in experiment.observations
        ),
        cost_units=experiment.cost_units,
        elapsed_ms=experiment.elapsed_ms,
    )


def _aggregate_metrics(experiment: TuningAggregateExperiment) -> TuningMetrics:
    return TuningMetrics(
        denominator=experiment.denominator,
        meaning_accuracy_ppm=(
            experiment.meaning_preserved_count
            * 1_000_000
            // experiment.denominator
        ),
        required_omission_count=experiment.required_omission_count,
        speaker_accuracy_ppm=(
            experiment.speaker_correct_count * 1_000_000 // experiment.denominator
        ),
        unsupported_addition_count=experiment.unsupported_addition_count,
        cost_units=experiment.cost_units,
        elapsed_ms=experiment.elapsed_ms,
    )


def _report(plan: TuningPlan, results: list[TuningResult]) -> TuningReport:
    selected = min(
        results,
        key=lambda item: (
            -item.metrics.meaning_accuracy_ppm,
            item.metrics.required_omission_count,
            -item.metrics.speaker_accuracy_ppm,
            item.metrics.unsupported_addition_count,
            item.metrics.cost_units,
            item.metrics.elapsed_ms,
            item.candidate.candidate_id,
        ),
    )
    return TuningReport(
        plan_digest=_plan_digest(plan),
        results=tuple(results),
        selected_candidate_id=selected.candidate.candidate_id,
    )


def evaluate_tuning(
    plan: TuningPlan,
    experiments: tuple[TuningExperiment, ...],
) -> TuningReport:
    """Evaluate exact candidates and select the best result deterministically."""
    experiment_by_id = {item.candidate_id: item for item in experiments}
    candidate_ids = tuple(item.candidate_id for item in plan.candidates)
    if set(experiment_by_id) != set(candidate_ids) or len(experiment_by_id) != len(
        experiments
    ):
        message = "tuning experiments must exactly cover planned candidates"
        raise ValueError(message)
    results: list[TuningResult] = []
    for candidate in plan.candidates:
        experiment = experiment_by_id[candidate.candidate_id]
        observed = tuple(
            item.reference_cue_id for item in experiment.observations
        )
        if observed != plan.reference_cue_ids:
            message = "tuning experiment must preserve the fixed reference denominator"
            raise ValueError(message)
        results.append(TuningResult(candidate=candidate, metrics=_metrics(experiment)))
    return _report(plan, results)


def evaluate_tuning_aggregates(
    plan: TuningPlan,
    experiments: tuple[TuningAggregateExperiment, ...],
) -> TuningReport:
    """Evaluate independently reviewed aggregates against the frozen plan."""
    experiment_by_id = {item.candidate_id: item for item in experiments}
    candidate_ids = tuple(item.candidate_id for item in plan.candidates)
    if set(experiment_by_id) != set(candidate_ids) or len(experiment_by_id) != len(
        experiments
    ):
        message = "tuning aggregates must exactly cover planned candidates"
        raise ValueError(message)
    denominator = len(plan.reference_cue_ids)
    results: list[TuningResult] = []
    for candidate in plan.candidates:
        experiment = experiment_by_id[candidate.candidate_id]
        if experiment.denominator != denominator:
            message = "tuning aggregate must preserve the fixed reference denominator"
            raise ValueError(message)
        results.append(
            TuningResult(
                candidate=candidate,
                metrics=_aggregate_metrics(experiment),
            )
        )
    return _report(plan, results)


def write_tuning_plan(path: Path, plan: TuningPlan) -> None:
    """Persist the immutable pre-execution plan before running candidates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_model_atomic(path, plan)


def write_tuning_report(path: Path, report: TuningReport) -> None:
    """Persist the deterministic comparison and selected configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_model_atomic(path, report)


__all__ = [
    "CandidateObservation",
    "TuningAggregateExperiment",
    "TuningCandidate",
    "TuningExperiment",
    "TuningMetrics",
    "TuningPlan",
    "TuningReport",
    "TuningResult",
    "evaluate_tuning",
    "evaluate_tuning_aggregates",
    "write_tuning_plan",
    "write_tuning_report",
]
