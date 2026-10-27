# Copyright 2026 Transcription Module contributors

"""Visual prompt qualification evidence remains append-only and exact."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from transcription_module import visual_prompt_qualification
from transcription_module.models import FrameId, RunId
from transcription_module.visual_prompt_qualification import (
    QualificationCaseKind,
    QualificationVerdict,
    VisualPromptQualificationCase,
)
from transcription_module.visual_review import (
    FrameVisualObservation,
    RawVisualCandidate,
    RawVisualRole,
    WindowVisualReviewResult,
)

CASES = Path("tests/fixtures/visual-review-prompt-cases.v1.json")


def test_qualification_report_rejects_duplicate_attempt_identity() -> None:
    """An attempt cannot be overwritten by repeating its identity."""
    attempt = visual_prompt_qualification.VisualPromptQualificationAttempt(
        attempt_id="attempt-001",
        case_id="development-13-frames",
        case_kind=QualificationCaseKind.DEVELOPMENT,
        prompt_digest="1" * 64,
        reviewer_input_digest="2" * 64,
        model_id="model-sentinel",
        settings_digest="3" * 64,
        raw_output_locator="task://attempt-001",
        raw_output_sha256="4" * 64,
        parsed_result_sha256="5" * 64,
        structural_pass=True,
        semantic_verdict=QualificationVerdict.SUCCEEDED,
    )

    with pytest.raises(ValidationError, match="attempt IDs must be unique"):
        _ = visual_prompt_qualification.VisualPromptQualificationReport(
            attempts=(attempt, attempt),
            selected_attempt_ids=("attempt-001",),
            verdict=QualificationVerdict.SUCCEEDED,
        )


def test_qualification_case_rejects_foreign_fact_frame() -> None:
    """Frozen expected facts cannot reference frames outside their case."""
    with pytest.raises(ValidationError, match="foreign frame"):
        _ = visual_prompt_qualification.VisualPromptQualificationCase(
            case_id="development-13-frames",
            case_kind=QualificationCaseKind.DEVELOPMENT,
            run_id=RunId("run-sentinel"),
            frame_ids=(FrameId("frame-1"),),
            visible_text_facts=(
                visual_prompt_qualification.ExpectedVisualFact(
                    fact_id="visible-1",
                    text="complete visible text",
                    frame_ids=(FrameId("foreign-frame"),),
                ),
            ),
        )


def test_frozen_development_and_heldout_cases_parse() -> None:
    """The rubric freezes one development case and one untouched holdout."""
    cases = TypeAdapter(tuple[VisualPromptQualificationCase, ...]).validate_json(
        CASES.read_bytes()
    )

    assert tuple(case.case_kind for case in cases) == (
        QualificationCaseKind.DEVELOPMENT,
        QualificationCaseKind.RETIRED,
        QualificationCaseKind.DEVELOPMENT,
        QualificationCaseKind.HELD_OUT,
    )


def test_case_evaluation_reports_missing_visible_text_and_role() -> None:
    """Machine scoring reports atomic omissions without judging context prose."""
    case = visual_prompt_qualification.VisualPromptQualificationCase(
        case_id="development-one-frame",
        case_kind=QualificationCaseKind.DEVELOPMENT,
        run_id=RunId("run-sentinel"),
        frame_ids=(FrameId("frame-1"),),
        visible_text_facts=(
            visual_prompt_qualification.ExpectedVisualFact(
                fact_id="visible-hello",
                text="안녕하세요",
                frame_ids=(FrameId("frame-1"),),
            ),
        ),
        candidate_roles=(
            visual_prompt_qualification.ExpectedCandidateRole(
                text="안녕하세요",
                frame_ids=(FrameId("frame-1"),),
                role=RawVisualRole.SPEECH_CAPTION,
                evidence="대화형 문구",
            ),
        ),
    )
    result = WindowVisualReviewResult(
        window_index=0,
        reviewer_input_digest="1" * 64,
        frame_observations=(
            FrameVisualObservation(frame_id=FrameId("frame-1")),
        ),
        candidates=(
            RawVisualCandidate(
                candidate_id="candidate-other",
                complete_text="다른 문구",
                proposed_role=RawVisualRole.UNKNOWN,
                supporting_frame_ids=(FrameId("frame-1"),),
            ),
        ),
    )

    evaluation = visual_prompt_qualification.evaluate_visual_review_case(
        case,
        result,
    )

    assert evaluation.machine_findings == (
        "missing visible fact: visible-hello",
        "missing candidate role: 안녕하세요:speech_caption",
    )
