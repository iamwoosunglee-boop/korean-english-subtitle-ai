# Copyright 2026 Transcription Module contributors

"""Frozen cases and append-only evidence for visual prompt qualification."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from transcription_module.models import FrameId, FrozenModel, RunId
from transcription_module.visual_review import (
    RawVisualRole,  # noqa: TC001
    WindowVisualReviewResult,  # noqa: TC001
)


class QualificationCaseKind(StrEnum):
    """Development stage represented by one immutable prompt case."""

    DEVELOPMENT = "development"
    HELD_OUT = "held_out"
    FULL_WINDOW = "full_window"
    RETIRED = "retired"


class QualificationVerdict(StrEnum):
    """Machine-readable semantic qualification outcome."""

    SUCCEEDED = "pass"
    FAILED = "fail"


class ExpectedVisualFact(FrozenModel):
    """One frozen semantic fact outside the model prompt."""

    fact_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    frame_ids: tuple[FrameId, ...] = Field(min_length=1)


class ExpectedCandidateRole(FrozenModel):
    """One frozen visible-text role and its visual rationale."""

    text: str = Field(min_length=1)
    frame_ids: tuple[FrameId, ...] = Field(min_length=1)
    role: RawVisualRole
    evidence: str = Field(min_length=1)


class VisualPromptQualificationCase(FrozenModel):
    """One immutable development, held-out, or capacity case."""

    case_id: str = Field(min_length=1)
    case_kind: QualificationCaseKind
    run_id: RunId
    frame_ids: tuple[FrameId, ...] = Field(min_length=1)
    visible_text_facts: tuple[ExpectedVisualFact, ...] = ()
    context_facts: tuple[ExpectedVisualFact, ...] = ()
    candidate_roles: tuple[ExpectedCandidateRole, ...] = ()
    ambiguities: tuple[str, ...] = ()

    @model_validator(mode="after")
    def expected_facts_stay_inside_case(self) -> Self:
        """Reject duplicate case frames and foreign expected-fact references."""
        frame_ids = set(self.frame_ids)
        if len(frame_ids) != len(self.frame_ids):
            message = "qualification case frame IDs must be unique"
            raise ValueError(message)
        referenced = (
            *(
                frame_id
                for fact in (*self.visible_text_facts, *self.context_facts)
                for frame_id in fact.frame_ids
            ),
            *(
                frame_id
                for role in self.candidate_roles
                for frame_id in role.frame_ids
            ),
        )
        if any(frame_id not in frame_ids for frame_id in referenced):
            message = "qualification expected fact references a foreign frame"
            raise ValueError(message)
        return self


class VisualPromptQualificationAttempt(FrozenModel):
    """One append-only scored model invocation."""

    attempt_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    case_kind: QualificationCaseKind
    prompt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(min_length=1)
    settings_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_output_locator: str = Field(min_length=1)
    raw_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parsed_result_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    structural_pass: bool
    semantic_verdict: QualificationVerdict
    semantic_findings: tuple[str, ...] = ()


class VisualPromptQualificationReport(FrozenModel):
    """Selected prompt evidence without mutable experiment state."""

    schema_version: str = "visual-prompt-qualification.v1"
    attempts: tuple[VisualPromptQualificationAttempt, ...] = Field(min_length=1)
    selected_attempt_ids: tuple[str, ...] = Field(min_length=1)
    verdict: QualificationVerdict

    @model_validator(mode="after")
    def selected_attempts_are_exact(self) -> Self:
        """Reject duplicate, missing, or failed selected attempt identities."""
        attempt_ids = tuple(attempt.attempt_id for attempt in self.attempts)
        if len(attempt_ids) != len(set(attempt_ids)):
            message = "qualification attempt IDs must be unique"
            raise ValueError(message)
        if len(self.selected_attempt_ids) != len(set(self.selected_attempt_ids)):
            message = "selected qualification attempt IDs must be unique"
            raise ValueError(message)
        attempts = {attempt.attempt_id: attempt for attempt in self.attempts}
        if any(attempt_id not in attempts for attempt_id in self.selected_attempt_ids):
            message = "selected qualification attempt is missing"
            raise ValueError(message)
        if self.verdict is QualificationVerdict.SUCCEEDED and any(
            not attempts[attempt_id].structural_pass
            or attempts[attempt_id].semantic_verdict
            is not QualificationVerdict.SUCCEEDED
            for attempt_id in self.selected_attempt_ids
        ):
            message = "passing qualification selected a failed attempt"
            raise ValueError(message)
        return self


class VisualPromptCaseEvaluation(FrozenModel):
    """Deterministic structural and atomic-fact scoring evidence."""

    case_id: str = Field(min_length=1)
    machine_findings: tuple[str, ...]
    semantic_fact_ids: tuple[str, ...]


def evaluate_visual_review_case(
    case: VisualPromptQualificationCase,
    result: WindowVisualReviewResult,
) -> VisualPromptCaseEvaluation:
    """Score exact visible facts and roles; leave context meaning for review."""
    findings: list[str] = []
    observations = {
        observation.frame_id: set(observation.visible_screen_text)
        for observation in result.frame_observations
    }
    if tuple(observations) != case.frame_ids:
        findings.append("frame observation coverage differs from frozen case")
    findings.extend(
        f"missing visible fact: {fact.fact_id}"
        for fact in case.visible_text_facts
        if any(
            fact.text not in observations.get(frame_id, set())
            for frame_id in fact.frame_ids
        )
    )
    for expected in case.candidate_roles:
        covered = {
            frame_id
            for candidate in result.candidates
            if candidate.complete_text == expected.text
            and candidate.proposed_role is expected.role
            for frame_id in candidate.supporting_frame_ids
        }
        if not set(expected.frame_ids).issubset(covered):
            findings.append(
                f"missing candidate role: {expected.text}:{expected.role.value}"
            )
    return VisualPromptCaseEvaluation(
        case_id=case.case_id,
        machine_findings=tuple(findings),
        semantic_fact_ids=tuple(fact.fact_id for fact in case.context_facts),
    )


__all__ = [
    "ExpectedCandidateRole",
    "ExpectedVisualFact",
    "QualificationCaseKind",
    "QualificationVerdict",
    "VisualPromptCaseEvaluation",
    "VisualPromptQualificationAttempt",
    "VisualPromptQualificationCase",
    "VisualPromptQualificationReport",
    "evaluate_visual_review_case",
]
