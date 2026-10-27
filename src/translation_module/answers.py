# Copyright 2026 Transcription Module contributors

"""Binding user-answer boundary for unresolved translations."""

from typing import Self

from pydantic import Field, model_validator

from translation_module.answer_integrity import (
    QUESTION_BINDING_FIELDS,
    answer_integrity_token,
    integrity_token,
)
from translation_module.common import (
    FrozenModel,
    Sha256Digest,
    SourceCueId,
    TargetCueId,
)
from translation_module.document import (
    DocumentStatus,
    TranslationDocument,
    UnresolvedItem,
    UnresolvedQuestion,
    translation_digest,
)
from translation_module.workflow import ReviewDomain


class AnswerError(Exception):
    """User answer cannot apply to the published document."""


class TranslationAnswer(FrozenModel):
    """Legacy boundary command for one user-selected translation."""

    document_id: str = Field(min_length=1)
    document_digest: Sha256Digest
    unresolved_id: str = Field(min_length=1)
    alternative_id: str | None = None
    direct_text: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def has_exactly_one_answer(self) -> Self:
        """Require one alternative ID or one non-empty direct answer."""
        if _answer_value_count(self.alternative_id, self.direct_text) != 1:
            msg = "answer requires exactly one value"
            raise ValueError(msg)
        return self


class AcceptedTranslationAnswer(FrozenModel):
    """Tamper-evident accepted answer bound to one exact candidate defect."""

    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    target_cue_ids: tuple[TargetCueId, ...] = Field(min_length=1)
    review_domain: ReviewDomain
    source_digest: Sha256Digest
    candidate_digest: Sha256Digest
    assignment_digest: Sha256Digest
    revision_session_id: str = Field(min_length=1)
    alternative_id: str | None = None
    direct_text: str | None = Field(default=None, min_length=1)
    integrity_token: Sha256Digest

    @model_validator(mode="after")
    def is_complete_and_intact(self) -> Self:
        """Reject partial answers and forged machine-consumed bindings."""
        if _answer_value_count(self.alternative_id, self.direct_text) != 1:
            msg = "answer requires exactly one value"
            raise ValueError(msg)
        if self.integrity_token != answer_integrity_token(self):
            msg = "answer integrity token is invalid"
            raise ValueError(msg)
        return self


class AnswerConstraint(FrozenModel):
    """Binding user wording supplied to the next translation candidate."""

    unresolved_id: str
    source_cue_ids: tuple[SourceCueId, ...]
    target_cue_ids: tuple[TargetCueId, ...]
    binding_text: str


def unresolved_question(
    document: TranslationDocument,
    unresolved_id: str,
) -> UnresolvedQuestion:
    """Return the parent-issued authoritative question for one defect."""
    _ = _current_item(document, unresolved_id)
    question = next(
        (
            question
            for question in document.unresolved_questions
            if question.question_id == unresolved_id
        ),
        None,
    )
    if question is None:
        msg = "answer references unknown authoritative question"
        raise AnswerError(msg)
    return question


def accept_answer(
    question: UnresolvedQuestion,
    *,
    alternative_id: str | None = None,
    direct_text: str | None = None,
) -> AcceptedTranslationAnswer:
    """Create an integrity-bound accepted answer from an exact question."""
    return AcceptedTranslationAnswer(
        question_id=question.question_id,
        question_text=question.question_text,
        source_cue_ids=question.source_cue_ids,
        target_cue_ids=question.target_cue_ids,
        review_domain=question.review_domain,
        source_digest=question.source_digest,
        candidate_digest=question.candidate_digest,
        assignment_digest=question.assignment_digest,
        revision_session_id=question.revision_session_id,
        alternative_id=alternative_id,
        direct_text=direct_text,
        integrity_token=integrity_token(question, alternative_id, direct_text),
    )


def apply_answer(
    document: TranslationDocument,
    question: UnresolvedQuestion,
    answer: AcceptedTranslationAnswer,
) -> TranslationDocument:
    """Apply an exact answer as a fresh candidate that remains review-blocked."""
    item = _current_item(document, question.question_id)
    authority = unresolved_question(document, question.question_id)
    if translation_digest(document) != document.content_digest:
        msg = "candidate is stale"
        raise AnswerError(msg)
    if question.candidate_digest != document.content_digest:
        msg = "question is stale"
        raise AnswerError(msg)
    if any(
        getattr(question, field) != getattr(authority, field)
        or getattr(answer, field) != getattr(authority, field)
        for field in QUESTION_BINDING_FIELDS
    ):
        msg = "answer binding does not match authoritative question"
        raise AnswerError(msg)
    if answer_integrity_token(answer) != answer.integrity_token:
        msg = "answer integrity token is invalid"
        raise AnswerError(msg)
    constraint = _bound_constraint(item, answer)
    cues = tuple(
        cue.model_copy(update={"text": constraint.binding_text})
        if cue.cue_id in constraint.target_cue_ids
        else cue
        for cue in document.cues
    )
    candidate = document.model_copy(update={"cues": cues})
    candidate_digest = translation_digest(candidate)
    questions = tuple(
        item.model_copy(update={"candidate_digest": candidate_digest})
        for item in document.unresolved_questions
    )
    return candidate.model_copy(
        update={
            "content_digest": candidate_digest,
            "unresolved_questions": questions,
        }
    )


def answer_constraint(
    document: TranslationDocument,
    answer: TranslationAnswer,
) -> AnswerConstraint:
    """Resolve a legacy current answer without mutating the document."""
    if (
        answer.document_id != document.document_id
        or answer.document_digest != document.content_digest
    ):
        msg = "answer is stale"
        raise AnswerError(msg)
    return _bound_constraint(_current_item(document, answer.unresolved_id), answer)


def _current_item(
    document: TranslationDocument,
    unresolved_id: str,
) -> UnresolvedItem:
    if document.status is not DocumentStatus.NEEDS_CONFIRMATION:
        msg = "document does not accept answers"
        raise AnswerError(msg)
    item = next(
        (item for item in document.unresolved if item.unresolved_id == unresolved_id),
        None,
    )
    if item is None:
        msg = "answer references unknown unresolved item"
        raise AnswerError(msg)
    return item


def _bound_constraint(
    item: UnresolvedItem,
    answer: TranslationAnswer | AcceptedTranslationAnswer,
) -> AnswerConstraint:
    if answer.alternative_id is not None:
        alternative = next(
            (
                option
                for option in item.alternatives
                if option.option_id == answer.alternative_id
            ),
            None,
        )
        if alternative is None:
            msg = "answer references unknown alternative"
            raise AnswerError(msg)
        binding_text = alternative.text
    elif answer.direct_text is not None:
        binding_text = answer.direct_text
    else:
        msg = "direct answer is missing"
        raise AnswerError(msg)
    return AnswerConstraint(
        unresolved_id=item.unresolved_id,
        source_cue_ids=item.source_cue_ids,
        target_cue_ids=item.target_cue_ids,
        binding_text=binding_text,
    )


def _answer_value_count(
    alternative_id: str | None,
    direct_text: str | None,
) -> int:
    return int(alternative_id is not None) + int(direct_text is not None)
