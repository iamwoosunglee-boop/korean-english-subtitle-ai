# Copyright 2026 Transcription Module contributors

"""Pure accepted-answer candidate and token-projection validation."""

from __future__ import annotations

from pydantic import ValidationError

from translation_module.answers import AcceptedTranslationAnswer, AnswerError
from translation_module.answers import apply_answer as validate_bound_answer
from translation_module.document import (
    DocumentStatus,
    TranslationDocument,
    UnresolvedQuestion,
    translation_digest,
)


class AnswerCandidateError(ValueError):
    """A proposed answer candidate violates its bound revision contract."""


def validate_answer_candidate(
    document: TranslationDocument,
    question: UnresolvedQuestion,
    answer: AcceptedTranslationAnswer,
    candidate: TranslationDocument,
) -> tuple[TranslationDocument, tuple[str, ...]]:
    """Return a fresh candidate and its preserved accepted wording tokens."""
    accepted_tokens = _accepted_tokens(document, question, answer)
    candidate = _validated_document(candidate)
    if (
        translation_digest(candidate) != candidate.content_digest
        or candidate.content_digest == document.content_digest
    ):
        message = "answer candidate digest is stale"
        raise AnswerCandidateError(message)
    _validate_stable_content(document, candidate, question)
    _validate_resolved_state(document, candidate, question)
    projected = _target_projection(candidate, question)
    if not _contains_contiguous_tokens(projected, accepted_tokens):
        message = "answer candidate loses accepted wording"
        raise AnswerCandidateError(message)
    return candidate, accepted_tokens


def _accepted_tokens(
    document: TranslationDocument,
    question: UnresolvedQuestion,
    answer: AcceptedTranslationAnswer,
) -> tuple[str, ...]:
    try:
        _ = validate_bound_answer(document, question, answer)
    except AnswerError as error:
        raise AnswerCandidateError(str(error)) from error
    if answer.direct_text is not None:
        text = answer.direct_text
    else:
        item = next(
            item
            for item in document.unresolved
            if item.unresolved_id == question.question_id
        )
        text = next(
            option.text
            for option in item.alternatives
            if option.option_id == answer.alternative_id
        )
    return tuple(text.split())


def _validated_document(
    candidate: TranslationDocument,
) -> TranslationDocument:
    try:
        return TranslationDocument.model_validate(candidate.model_dump())
    except ValidationError as error:
        message = "answer candidate is invalid"
        raise AnswerCandidateError(message) from error


def _validate_stable_content(
    document: TranslationDocument,
    candidate: TranslationDocument,
    question: UnresolvedQuestion,
) -> None:
    if (
        candidate.schema_version != document.schema_version
        or candidate.document_id != document.document_id
        or candidate.source_document_id != document.source_document_id
        or candidate.source_digest != document.source_digest
        or candidate.source_locale != document.source_locale
        or candidate.target_locale != document.target_locale
        or candidate.context != document.context
        or candidate.retained_contexts != document.retained_contexts
    ):
        message = "answer candidate changes stable document identity"
        raise AnswerCandidateError(message)
    original_ids = tuple(cue.cue_id for cue in document.cues)
    candidate_ids = tuple(cue.cue_id for cue in candidate.cues)
    if candidate_ids != original_ids:
        message = "answer candidate changes target cue identity"
        raise AnswerCandidateError(message)
    scope = set(question.target_cue_ids)
    original = {cue.cue_id: cue for cue in document.cues}
    for cue in candidate.cues:
        prior = original[cue.cue_id]
        if cue.cue_id not in scope and cue != prior:
            message = "answer candidate changes cues outside question scope"
            raise AnswerCandidateError(message)
        normalized = cue.model_copy(
            update={
                "start_ms": prior.start_ms,
                "end_ms": prior.end_ms,
                "text": prior.text,
            }
        )
        if cue.cue_id in scope and normalized != prior:
            message = "answer candidate changes non-display cue fields"
            raise AnswerCandidateError(message)


def _validate_resolved_state(
    document: TranslationDocument,
    candidate: TranslationDocument,
    question: UnresolvedQuestion,
) -> None:
    expected_unresolved = tuple(
        item
        for item in document.unresolved
        if item.unresolved_id != question.question_id
    )
    expected_questions = tuple(
        item.model_copy(update={"candidate_digest": candidate.content_digest})
        for item in document.unresolved_questions
        if item.question_id != question.question_id
    )
    expected_status = (
        DocumentStatus.NEEDS_CONFIRMATION
        if expected_unresolved
        else DocumentStatus.FINALIZED
    )
    if (
        candidate.unresolved != expected_unresolved
        or candidate.unresolved_questions != expected_questions
        or candidate.status is not expected_status
    ):
        message = "answer candidate does not resolve exactly one question"
        raise AnswerCandidateError(message)


def _target_projection(
    candidate: TranslationDocument,
    question: UnresolvedQuestion,
) -> tuple[str, ...]:
    scope = set(question.target_cue_ids)
    return tuple(
        token
        for cue in candidate.cues
        if cue.cue_id in scope
        for token in cue.text.split()
    )


def _contains_contiguous_tokens(
    projected: tuple[str, ...],
    accepted: tuple[str, ...],
) -> bool:
    return any(
        projected[index : index + len(accepted)] == accepted
        for index in range(len(projected) - len(accepted) + 1)
    )
