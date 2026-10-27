# Copyright 2026 Transcription Module contributors

"""Synthetic, fixture-only authority for Task-4 RED tests."""

from __future__ import annotations

import hashlib
import json
import stat
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

FIXTURE = Path("tests/fixtures/visual-correction-regressions.v1.json")
RUN_ID = "11111111-1111-4111-8111-111111111111"
FOREIGN_RUN_ID = "22222222-2222-4222-8222-222222222222"
BASELINE_RUN_ID = "e00f79ca-c58d-4bb8-bf5e-023326759c40"
POLICY_DIGEST = "e7cb480c5a18879945e67c65bd42a72def32f8aa5e5ff5b0195eb76f3756721f"
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonBlank = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class FixtureCase(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    case_id: NonBlank
    comparable_interval_ids: tuple[NonBlank, ...] = Field(min_length=1)
    expected_action: Literal[
        "KEEP", "REPLACE_SPAN", "INSERT_CUE", "HOLD_FOR_CONFIRMATION"
    ]


class MetricPopulation(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    metric_id: NonBlank
    case_ids: tuple[NonBlank, ...]
    numerator_case_ids: tuple[NonBlank, ...]
    expected_denominator: int
    expected_numerator: int
    status: NonBlank


class FrozenFixture(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal["visual-correction-regressions.v1"]
    cases: tuple[FixtureCase, ...]
    metric_populations: tuple[MetricPopulation, ...]


class Meaning(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    MISSING = "missing"
    UNASSESSABLE = "unassessable"


class Presence(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNASSESSABLE = "unassessable"
    NOT_IN_POPULATION = "not_in_population"


class AutomaticAction(StrEnum):
    KEEP = "keep"
    REPLACE_SPAN = "replace_span"
    INSERT_CUE = "insert_cue"
    HOLD_FOR_CONFIRMATION = "hold_for_confirmation"


class Owner(StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"
    NOT_APPLICABLE = "not_applicable"


class Damage(StrEnum):
    PRESERVED = "preserved"
    DAMAGED = "damaged"
    NOT_APPLICABLE = "not_applicable"


class IntervalMapping(StrictModel):
    interval_id: NonBlank
    candidate_cue_id: NonBlank
    authority_turn_ids: tuple[NonBlank, ...] = Field(min_length=1)


class FixtureCharacterTrace(StrictModel):
    character_id: NonBlank
    value: Annotated[str, Field(min_length=1, max_length=1)]
    source_evidence_id: NonBlank
    source_value: NonBlank


class CaseAssessment(StrictModel):
    case_id: NonBlank
    interval_mappings: tuple[IntervalMapping, ...] = Field(min_length=1)
    final_meaning: Meaning
    clean_asr_mandatory_meaning: Presence
    final_mandatory_meaning: Presence
    automatic_action: AutomaticAction
    critical_owner: Owner
    automatic_damage: Damage
    unsupported_additions_before: int = Field(ge=0)
    unsupported_additions_after: int = Field(ge=0)
    character_traces: tuple[FixtureCharacterTrace, ...]


class AssessmentAuthor(StrictModel):
    execution_id: NonBlank
    cases: tuple[CaseAssessment, ...] = Field(min_length=1)


class AssessmentReview(StrictModel):
    execution_id: NonBlank
    author_payload_sha256: Sha256
    independently_opened_snapshot_digest: Sha256
    decision: Literal["approve", "reject"]


class Assessment(StrictModel):
    """Synthetic independently reviewed assessment persisted by tests."""

    schema_version: Literal["paired-diagnostic-assessment.v1"]
    fixture_sha256: Sha256
    candidate_run_id: NonBlank
    artifact_snapshot_digest: Sha256
    assessment_rule_version: Literal["paired-diagnostic-assessment-rules.v1"]
    author: AssessmentAuthor
    review: AssessmentReview

    @model_validator(mode="after")
    def identities_are_independent(self) -> Self:
        """Require distinct synthetic author and reviewer identities."""
        if self.author.execution_id == self.review.execution_id:
            message = "assessment identities must differ"
            raise ValueError(message)
        return self


class ArtifactRecord(StrictModel):
    relative_path: NonBlank
    kind: NonBlank
    mode: int = Field(ge=0)
    size: int = Field(ge=0)
    sha256: Sha256


class FixtureRun(StrictModel):
    """Synthetic fixture-run artifact manifest persisted by tests."""

    schema_version: Literal["paired-diagnostic-fixture-run.v1"]
    input_mode: Literal["fixture"]
    run_id: NonBlank
    baseline_run_id: NonBlank
    policy_digest: Sha256
    artifacts: tuple[ArtifactRecord, ...] = Field(min_length=1)
    artifact_snapshot_digest: Sha256
    disallowed_track_contribution: int = Field(ge=0)
    non_speech_unknown_elevation: int = Field(ge=0)
    user_confirmed_overwrite: int = Field(ge=0)


ARTIFACT_KINDS = (
    "candidate_transcript",
    "policy",
    "inventory",
    "asr",
    "vad",
    "observations",
    "corrections",
    "provenance",
    "speaker_authority_v2",
    "verified_speaker_turns_v3",
    "final_review_assignment",
    "final_review_result",
    "final_review_receipt",
    "final_review_proposal",
    "final_review_decision",
    "final_review_application",
    "confirmation_anchor",
)
ARTIFACT_PATHS = (
    "artifacts/candidate-transcript.json",
    "artifacts/visual-evidence-policy.json",
    "artifacts/subtitle-inventory.json",
    "artifacts/asr.json",
    "artifacts/vad.json",
    "artifacts/visual-observations.json",
    "artifacts/correction-receipts.json",
    "artifacts/cue-provenance.json",
    "artifacts/speaker-turn-authority.v2.json",
    "artifacts/verified-speaker-turns.v3.json",
    "artifacts/final-review-assignment.json",
    "artifacts/final-review-result.json",
    "artifacts/final-review-receipt.json",
    "artifacts/final-review-proposal.json",
    "artifacts/final-review-decision.json",
    "artifacts/final-review-application.json",
    "artifacts/confirmation-anchor.json",
)


def _canonical(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _snapshot_digest(records: tuple[ArtifactRecord, ...]) -> str:
    return _sha(
        json.dumps(
            [record.model_dump(mode="json") for record in records],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _fixture() -> FrozenFixture:
    return FrozenFixture.model_validate_json(FIXTURE.read_bytes())


def _author() -> AssessmentAuthor:
    fixture = _fixture()
    mandatory = next(
        item
        for item in fixture.metric_populations
        if item.metric_id == "mandatory_omission"
    )
    mandatory_ids = frozenset(mandatory.case_ids)
    traced = next(
        item
        for item in fixture.metric_populations
        if item.metric_id == "character_traceability"
    )
    traced_ids = frozenset(traced.case_ids)
    actions = {
        "KEEP": AutomaticAction.KEEP,
        "REPLACE_SPAN": AutomaticAction.REPLACE_SPAN,
        "INSERT_CUE": AutomaticAction.INSERT_CUE,
        "HOLD_FOR_CONFIRMATION": AutomaticAction.HOLD_FOR_CONFIRMATION,
    }
    return AssessmentAuthor(
        execution_id="fixture-author",
        cases=tuple(
            CaseAssessment(
                case_id=case.case_id,
                interval_mappings=tuple(
                    IntervalMapping(
                        interval_id=interval_id,
                        candidate_cue_id=f"fixture-cue-{interval_id}",
                        authority_turn_ids=(f"fixture-turn-{interval_id}",),
                    )
                    for interval_id in case.comparable_interval_ids
                ),
                final_meaning=Meaning.CORRECT,
                clean_asr_mandatory_meaning=(
                    Presence.MISSING
                    if case.case_id in mandatory_ids
                    else Presence.NOT_IN_POPULATION
                ),
                final_mandatory_meaning=(
                    Presence.PRESENT
                    if case.case_id in mandatory_ids
                    else Presence.NOT_IN_POPULATION
                ),
                automatic_action=actions[case.expected_action],
                critical_owner=Owner.CORRECT,
                automatic_damage=Damage.PRESERVED,
                unsupported_additions_before=1,
                unsupported_additions_after=0,
                character_traces=(
                    (
                        FixtureCharacterTrace(
                            character_id=f"fixture-character-{case.case_id}",
                            value="근",
                            source_evidence_id=(
                                f"fixture-evidence-{case.case_id}"
                            ),
                            source_value="근거",
                        ),
                    )
                    if case.case_id in traced_ids
                    else ()
                ),
            )
            for case in fixture.cases
        ),
    )


def build_fixture_tree(root: Path) -> Path:
    """Create one complete fixture-only artifact tree under a temp root."""
    run_fixture = root / "run-fixture"
    artifacts_dir = run_fixture / "artifacts"
    artifacts_dir.mkdir(parents=True)
    interval_ids = tuple(
        interval_id
        for case in _fixture().cases
        for interval_id in case.comparable_interval_ids
    )
    records: list[ArtifactRecord] = []
    for kind, relative in zip(ARTIFACT_KINDS, ARTIFACT_PATHS, strict=True):
        path = run_fixture / relative
        artifact: dict[str, JsonValue] = {"kind": kind, "run_id": RUN_ID}
        if kind == "candidate_transcript":
            artifact["cue_ids"] = [
                f"fixture-cue-{interval_id}" for interval_id in interval_ids
            ]
        if kind == "verified_speaker_turns_v3":
            artifact["turn_ids"] = [
                f"fixture-turn-{interval_id}" for interval_id in interval_ids
            ]
        payload = (
            json.dumps(
                artifact,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        _ = path.write_bytes(payload)
        records.append(
            ArtifactRecord(
                relative_path=relative,
                kind=kind,
                mode=stat.S_IMODE(path.stat().st_mode),
                size=len(payload),
                sha256=_sha(payload),
            )
        )
    ordered = tuple(records)
    snapshot_digest = _snapshot_digest(ordered)
    fixture_run = FixtureRun(
        schema_version="paired-diagnostic-fixture-run.v1",
        input_mode="fixture",
        run_id=RUN_ID,
        baseline_run_id=BASELINE_RUN_ID,
        policy_digest=POLICY_DIGEST,
        artifacts=ordered,
        artifact_snapshot_digest=snapshot_digest,
        disallowed_track_contribution=0,
        non_speech_unknown_elevation=0,
        user_confirmed_overwrite=0,
    )
    author = _author()
    assessment = Assessment(
        schema_version="paired-diagnostic-assessment.v1",
        fixture_sha256=_sha(FIXTURE.read_bytes()),
        candidate_run_id=RUN_ID,
        artifact_snapshot_digest=snapshot_digest,
        assessment_rule_version="paired-diagnostic-assessment-rules.v1",
        author=author,
        review=AssessmentReview(
            execution_id="fixture-reviewer",
            author_payload_sha256=_sha(_canonical(author)),
            independently_opened_snapshot_digest=snapshot_digest,
            decision="approve",
        ),
    )
    _ = (run_fixture / "fixture-run.json").write_bytes(_canonical(fixture_run))
    _ = (run_fixture / "paired-diagnostic-assessment.json").write_bytes(
        _canonical(assessment)
    )
    return run_fixture


def load_assessment(run_fixture: Path) -> Assessment:
    """Read the test assessment after an adversarial mutation."""
    return Assessment.model_validate_json(
        (run_fixture / "paired-diagnostic-assessment.json").read_bytes()
    )


def write_assessment(run_fixture: Path, assessment: Assessment) -> None:
    """Replace the test assessment with one controlled mutation."""
    _ = (run_fixture / "paired-diagnostic-assessment.json").write_bytes(
        _canonical(assessment)
    )


def write_fixture_run(run_fixture: Path, fixture_run: FixtureRun) -> None:
    """Replace the fixture manifest with one controlled mutation."""
    _ = (run_fixture / "fixture-run.json").write_bytes(_canonical(fixture_run))


def load_fixture_run(run_fixture: Path) -> FixtureRun:
    """Read the test fixture manifest before an adversarial mutation."""
    return FixtureRun.model_validate_json(
        (run_fixture / "fixture-run.json").read_bytes()
    )


def write_copied_sample_assessment(run_fixture: Path) -> None:
    """Replace assessment authority with a forbidden v1 outcome sample."""
    source = Path("tests/fixtures/paired-diagnostic-pass.v1.json")
    _ = (run_fixture / "paired-diagnostic-assessment.json").write_bytes(
        source.read_bytes()
    )


def replace_artifact(
    run_fixture: Path,
    *,
    kind: str,
    relative_path: str,
    payload: bytes,
) -> None:
    """Replace one artifact and rebind only its synthetic snapshot hashes."""
    path = run_fixture / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(payload)
    fixture_run = load_fixture_run(run_fixture)
    records = tuple(
        (
            record.model_copy(
                update={
                    "relative_path": relative_path,
                    "mode": stat.S_IMODE(path.stat().st_mode),
                    "size": len(payload),
                    "sha256": _sha(payload),
                }
            )
            if record.kind == kind
            else record
        )
        for record in fixture_run.artifacts
    )
    snapshot_digest = _snapshot_digest(records)
    write_fixture_run(
        run_fixture,
        fixture_run.model_copy(
            update={
                "artifacts": records,
                "artifact_snapshot_digest": snapshot_digest,
            }
        ),
    )
    assessment = load_assessment(run_fixture)
    review = assessment.review.model_copy(
        update={"independently_opened_snapshot_digest": snapshot_digest}
    )
    write_assessment(
        run_fixture,
        assessment.model_copy(
            update={
                "artifact_snapshot_digest": snapshot_digest,
                "review": review,
            }
        ),
    )


def rebind_assessment_author(assessment: Assessment) -> Assessment:
    """Recompute reviewer binding after one controlled author mutation."""
    review = assessment.review.model_copy(
        update={"author_payload_sha256": _sha(_canonical(assessment.author))}
    )
    return assessment.model_copy(update={"review": review})


__all__ = [
    "FIXTURE",
    "FOREIGN_RUN_ID",
    "RUN_ID",
    "Assessment",
    "FixtureRun",
    "build_fixture_tree",
    "load_assessment",
    "load_fixture_run",
    "rebind_assessment_author",
    "replace_artifact",
    "write_assessment",
    "write_copied_sample_assessment",
    "write_fixture_run",
]
