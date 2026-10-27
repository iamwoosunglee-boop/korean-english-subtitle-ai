# Copyright 2026 Transcription Module contributors

"""User-answer boundary characterization and binding regressions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.translation.type_builders import milliseconds
from translation_module.answers import (
    AnswerError,
    TranslationAnswer,
    accept_answer,
    answer_constraint,
    apply_answer,
    unresolved_question,
)
from translation_module.common import ContentKind, SourceCueId, TargetCueId
from translation_module.contracts import (
    DocumentStatus,
    EpisodeContext,
    TargetCue,
    TranslationAlternative,
    TranslationDocument,
    UnresolvedItem,
    UnresolvedQuestion,
)
from translation_module.validation import bind_translation_digest
from translation_module.workflow import ReviewDomain

ASSIGNMENT_DIGEST = "b" * 64


def _document() -> TranslationDocument:
    draft = TranslationDocument.model_construct(
        document_id="translation-1",
        content_digest="0" * 64,
        source_document_id="source-1",
        source_digest="a" * 64,
        source_locale="ko-KR",
        target_locale="en-US",
        context=EpisodeContext(summary="A talk show."),
        retained_contexts=(),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="That was amazing.",
                speakers=("speaker",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        unresolved=(
            UnresolvedItem(
                unresolved_id="question-1",
                source_cue_ids=(SourceCueId("source-1"),),
                target_cue_ids=(TargetCueId("target-1"),),
                current_translation="That was amazing.",
                why_difficult="The intensity is ambiguous.",
                alternatives=(
                    TranslationAlternative(
                        option_id="option-a",
                        text="That was wild.",
                        meaning="strong surprise",
                        strength="natural",
                        risk="stronger wording",
                    ),
                    TranslationAlternative(
                        option_id="option-b",
                        text="That was amazing.",
                        meaning="general praise",
                        strength="safe",
                        risk="weaker wording",
                    ),
                ),
                recommendation_id="option-a",
                recommendation_reason="The reaction is strong.",
            ),
        ),
        status=DocumentStatus.NEEDS_CONFIRMATION,
    )
    document = bind_translation_digest(draft)
    return TranslationDocument.model_validate(
        document.model_dump()
        | {
            "unresolved_questions": (
                UnresolvedQuestion(
                    question_id="question-1",
                    question_text="Which intensity should this subtitle use?",
                    source_cue_ids=(SourceCueId("source-1"),),
                    target_cue_ids=(TargetCueId("target-1"),),
                    review_domain=ReviewDomain.NATURALNESS,
                    source_digest=document.source_digest,
                    candidate_digest=document.content_digest,
                    assignment_digest=ASSIGNMENT_DIGEST,
                    revision_session_id="revision-1",
                ),
            ),
        }
    )


def _question(document: TranslationDocument) -> UnresolvedQuestion:
    return unresolved_question(document, "question-1")


def test_baseline_answer_still_resolves_selected_alternative() -> None:
    """The existing answer boundary resolves an option to its full text."""
    document = _document()
    constraint = answer_constraint(
        document,
        TranslationAnswer(
            document_id=document.document_id,
            document_digest=document.content_digest,
            unresolved_id="question-1",
            alternative_id="option-a",
        ),
    )

    assert constraint.binding_text == "That was wild."


def test_answer_by_cue_id_alone_is_rejected() -> None:
    """A cue ID cannot stand in for an exact defect-bound question."""
    with pytest.raises(ValidationError):
        _ = TranslationAnswer.model_validate(
            {"source_cue_ids": ("source-1",), "alternative_id": "option-a"}
        )


@pytest.mark.parametrize(
    "field",
    ["source_digest", "candidate_digest", "assignment_digest"],
)
def test_answer_with_foreign_binding_is_rejected(field: str) -> None:
    """Answers from another source, candidate, or assignment cannot apply."""
    document = _document()
    question = _question(document)
    answer = accept_answer(question, alternative_id="option-a")

    with pytest.raises(AnswerError, match="binding"):
        _ = apply_answer(
            document,
            question,
            answer.model_copy(update={field: "d" * 64}),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("question_text", "Forged control text."),
        ("review_domain", ReviewDomain.CULTURE),
        ("assignment_digest", "d" * 64),
        ("revision_session_id", "forged-session"),
    ],
)
def test_apply_answer_rejects_forged_caller_question(
    field: str,
    value: str | ReviewDomain,
) -> None:
    """Caller-provided questions cannot override the document authority."""
    document = _document()
    question = _question(document)
    forged_question = question.model_copy(update={field: value})
    answer = accept_answer(forged_question, alternative_id="option-a")

    with pytest.raises(AnswerError, match="binding"):
        _ = apply_answer(document, forged_question, answer)


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (UnresolvedItem, "source_cue_ids"),
        (UnresolvedItem, "target_cue_ids"),
        (UnresolvedQuestion, "source_cue_ids"),
        (UnresolvedQuestion, "target_cue_ids"),
    ],
)
def test_unresolved_scopes_reject_duplicate_cue_ids(
    model: type[UnresolvedItem | UnresolvedQuestion],
    field: str,
) -> None:
    """Both unresolved contracts preserve ordered, unique cue scopes."""
    document = _document()
    value = document.unresolved[0] if model is UnresolvedItem else _question(document)
    cue_ids = (
        value.source_cue_ids if field == "source_cue_ids" else value.target_cue_ids
    )

    with pytest.raises(ValidationError) as caught:
        _ = model.model_validate(value.model_dump() | {field: (cue_ids[0],) * 2})

    error = caught.value.errors()[0]
    assert error["loc"] == ()
    assert error["type"] == "value_error"


def test_document_rejects_duplicate_unresolved_ids() -> None:
    """Each authoritative question has exactly one unresolved defect."""
    document = _document()

    with pytest.raises(ValidationError, match="unresolved"):
        _ = TranslationDocument.model_validate(
            document.model_dump()
            | {"unresolved": (document.unresolved[0], document.unresolved[0])}
        )


def test_stale_answer_cannot_be_reused_after_candidate_revision() -> None:
    """An accepted answer is single-candidate input, not reusable state."""
    document = _document()
    question = _question(document)
    answer = accept_answer(question, alternative_id="option-a")
    revised = apply_answer(document, question, answer)

    with pytest.raises(AnswerError, match="stale"):
        _ = apply_answer(revised, question, answer)


def test_unresolved_question_requires_exact_scope_domain_and_session() -> None:
    """Questions without every machine-consumed binding cannot be created."""
    document = _document()

    with pytest.raises(ValidationError):
        _ = UnresolvedQuestion(
            question_id="question-1",
            question_text="Which intensity should this subtitle use?",
            source_cue_ids=(SourceCueId("source-1"),),
            target_cue_ids=(TargetCueId("target-1"),),
            review_domain=ReviewDomain.NATURALNESS,
            source_digest=document.source_digest,
            candidate_digest=document.content_digest,
            assignment_digest=ASSIGNMENT_DIGEST,
            revision_session_id="",
        )


def test_applying_answer_creates_fresh_blocked_candidate() -> None:
    """Applying an exact answer creates bytes requiring fresh review."""
    document = _document()
    question = _question(document)
    revised = apply_answer(
        document,
        question,
        accept_answer(question, alternative_id="option-a"),
    )

    assert revised.content_digest != document.content_digest
    assert revised.cues[0].text == "That was wild."
    assert revised.status is DocumentStatus.NEEDS_CONFIRMATION
    assert revised.unresolved == document.unresolved
