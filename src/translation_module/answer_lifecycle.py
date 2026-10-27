# Copyright 2026 Transcription Module contributors

"""Concurrent completion boundary for accepted translation answers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from pydantic import Field, ValidationError

from translation_module import answer_candidate
from translation_module.common import FrozenModel, Sha256Digest
from translation_module.document import (
    DocumentStatus,
    TranslationDocument,
    UnresolvedQuestion,
    translation_digest,
)
from translation_module.workflow import (
    AffectedKind,
    AffectedReference,
    ReviewKind,
    ReviewResult,
)

if TYPE_CHECKING:
    from translation_module.answers import AcceptedTranslationAnswer


class AnswerLifecycleError(Exception):
    """An answer cannot complete against the current arbitration state."""


class AcceptedAnswerCompletion(FrozenModel):
    """Exact signal emitted for the one accepted concurrent answer."""

    question_id: str = Field(min_length=1)
    previous_candidate_digest: Sha256Digest
    candidate_digest: Sha256Digest
    integrity_token: Sha256Digest
    accepted_tokens: tuple[str, ...] = Field(min_length=1)
    candidate: TranslationDocument


class AcceptedAnswerResult(FrozenModel):
    """The answer that won arbitration."""

    completion: AcceptedAnswerCompletion


class DuplicateAnswerResult(FrozenModel):
    """A competing answer rejected in favor of the exact completion."""

    completion: AcceptedAnswerCompletion


class AcceptedAnswerSignal:
    """Awaitable one-shot accepted-answer completion."""

    def __init__(self) -> None:
        """Create an unset one-shot completion signal."""
        self._event: anyio.Event = anyio.Event()
        self._completion: AcceptedAnswerCompletion | None = None

    async def wait(self) -> AcceptedAnswerCompletion:
        """Wait for completion without polling or timing assumptions."""
        await self._event.wait()
        if self._completion is None:
            message = "answer completion signal has no result"
            raise AnswerLifecycleError(message)
        return self._completion

    def complete(self, completion: AcceptedAnswerCompletion) -> None:
        """Publish the result before releasing exact-signal waiters."""
        if self._completion is not None:
            message = "answer completion signal is already set"
            raise AnswerLifecycleError(message)
        self._completion = completion
        self._event.set()

    @property
    def result(self) -> AcceptedAnswerCompletion | None:
        """Return the completed result without waiting."""
        return self._completion


class AnswerArbiter:
    """Accept exactly one valid candidate for each unresolved question."""

    def __init__(self, document: TranslationDocument) -> None:
        """Create independent locks and signals for authoritative questions."""
        self._document: TranslationDocument = document
        self._signals: dict[str, AcceptedAnswerSignal] = {
            question.question_id: AcceptedAnswerSignal()
            for question in document.unresolved_questions
        }
        self._locks: dict[str, anyio.Lock] = {
            question.question_id: anyio.Lock()
            for question in document.unresolved_questions
        }

    def completion_signal(self, question_id: str) -> AcceptedAnswerSignal:
        """Return the exact one-shot signal for an authoritative question."""
        signal = self._signals.get(question_id)
        if signal is None:
            message = "answer references unknown authoritative question"
            raise AnswerLifecycleError(message)
        return signal

    async def complete_answer(
        self,
        question: UnresolvedQuestion,
        answer: AcceptedTranslationAnswer,
        candidate: TranslationDocument,
    ) -> AcceptedAnswerCompletion:
        """Apply one answer after subscribing to its exact completion."""
        signal = self.completion_signal(question.question_id)
        _ = await self.apply_answer(question, answer, candidate)
        return await signal.wait()

    async def apply_answer(
        self,
        question: UnresolvedQuestion,
        answer: AcceptedTranslationAnswer,
        candidate: TranslationDocument,
    ) -> AcceptedAnswerResult | DuplicateAnswerResult:
        """Validate a proposed revision and arbitrate concurrent answers."""
        try:
            candidate, accepted_tokens = answer_candidate.validate_answer_candidate(
                self._document,
                question,
                answer,
                candidate,
            )
        except answer_candidate.AnswerCandidateError as error:
            raise AnswerLifecycleError(str(error)) from error
        signal = self.completion_signal(question.question_id)
        async with self._locks[question.question_id]:
            if signal.result is not None:
                return DuplicateAnswerResult(completion=signal.result)
            completion = AcceptedAnswerCompletion(
                question_id=question.question_id,
                previous_candidate_digest=self._document.content_digest,
                candidate_digest=candidate.content_digest,
                integrity_token=answer.integrity_token,
                accepted_tokens=accepted_tokens,
                candidate=candidate,
            )
            signal.complete(completion)
            return AcceptedAnswerResult(completion=completion)


def prepare_answer_arbitration(
    document: TranslationDocument,
    escalation_review: ReviewResult,
) -> AnswerArbiter:
    """Validate blocker bijection before accepting concurrent answers."""
    document, escalation_review = _validated_inputs(document, escalation_review)
    _validate_blocker_bijection(document, escalation_review)
    return AnswerArbiter(document)


def _validated_inputs(
    document: TranslationDocument,
    review: ReviewResult,
) -> tuple[TranslationDocument, ReviewResult]:
    try:
        document = TranslationDocument.model_validate(document.model_dump())
        review = ReviewResult.model_validate(review.model_dump())
    except ValidationError as error:
        message = "answer arbitration input is invalid"
        raise AnswerLifecycleError(message) from error
    if (
        document.status is not DocumentStatus.NEEDS_CONFIRMATION
        or translation_digest(document) != document.content_digest
    ):
        message = "answer arbitration document is stale or not awaiting confirmation"
        raise AnswerLifecycleError(message)
    if (
        review.kind is not ReviewKind.INDEPENDENT
        or review.candidate_digest != document.content_digest
    ):
        message = "answer arbitration review is stale or not independent"
        raise AnswerLifecycleError(message)
    return document, review


def _validate_blocker_bijection(
    document: TranslationDocument,
    review: ReviewResult,
) -> None:
    blockers = tuple(finding for finding in review.findings if finding.blocking)
    if any(blocker.required_change is not None for blocker in blockers):
        message = "answer arbitration requires unresolvable blockers"
        raise AnswerLifecycleError(message)
    blocker_ids = tuple(finding.finding_id for finding in blockers)
    unresolved_ids = tuple(item.unresolved_id for item in document.unresolved)
    question_ids = tuple(
        question.question_id for question in document.unresolved_questions
    )
    id_groups = (blocker_ids, unresolved_ids, question_ids)
    if any(len(ids) != len(set(ids)) for ids in id_groups) or not (
        set(blocker_ids) == set(unresolved_ids) == set(question_ids)
    ):
        message = (
            "blocking findings, unresolved items, and questions must be bijective"
        )
        raise AnswerLifecycleError(message)
    questions = {
        question.question_id: question
        for question in document.unresolved_questions
    }
    for blocker in blockers:
        question = questions[blocker.finding_id]
        if (
            blocker.domain is not question.review_domain
            or question.source_digest != document.source_digest
            or question.candidate_digest != document.content_digest
            or question.assignment_digest != review.assignment_digest
        ):
            message = "blocking finding does not match authoritative question"
            raise AnswerLifecycleError(message)
        _validate_affected_scope(blocker.affected, question)


def _validate_affected_scope(
    affected: tuple[AffectedReference, ...],
    question: UnresolvedQuestion,
) -> None:
    source_ids = tuple(
        item.item_id
        for item in affected
        if item.kind is AffectedKind.SOURCE_CUE
    )
    target_ids = tuple(
        item.item_id
        for item in affected
        if item.kind is AffectedKind.TARGET_CUE
    )
    expected_sources = tuple(str(cue_id) for cue_id in question.source_cue_ids)
    expected_targets = tuple(str(cue_id) for cue_id in question.target_cue_ids)
    invalid = (
        (not source_ids and not target_ids)
        or (bool(source_ids) and source_ids != expected_sources)
        or (bool(target_ids) and target_ids != expected_targets)
    )
    if invalid:
        message = "blocking finding scope does not match authoritative question"
        raise AnswerLifecycleError(message)
