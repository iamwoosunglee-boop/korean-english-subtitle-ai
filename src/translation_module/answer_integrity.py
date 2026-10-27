# Copyright 2026 Transcription Module contributors

"""Canonical integrity binding for accepted translation answers."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Protocol

from translation_module.document import UnresolvedQuestion

if TYPE_CHECKING:
    from translation_module.common import (
        Sha256Digest,
        SourceCueId,
        TargetCueId,
    )
    from translation_module.workflow import ReviewDomain


class _AcceptedAnswerPayload(Protocol):
    """Exact accepted-answer fields consumed by integrity binding."""

    question_id: str
    question_text: str
    source_cue_ids: tuple[SourceCueId, ...]
    target_cue_ids: tuple[TargetCueId, ...]
    review_domain: ReviewDomain
    source_digest: Sha256Digest
    candidate_digest: Sha256Digest
    assignment_digest: Sha256Digest
    revision_session_id: str
    alternative_id: str | None
    direct_text: str | None

QUESTION_BINDING_FIELDS = (
    "question_id",
    "question_text",
    "source_cue_ids",
    "target_cue_ids",
    "review_domain",
    "source_digest",
    "candidate_digest",
    "assignment_digest",
    "revision_session_id",
)


def answer_integrity_token(answer: _AcceptedAnswerPayload) -> str:
    """Hash all machine-consumed accepted-answer fields except its token."""
    question = UnresolvedQuestion(
        question_id=answer.question_id,
        question_text=answer.question_text,
        source_cue_ids=answer.source_cue_ids,
        target_cue_ids=answer.target_cue_ids,
        review_domain=answer.review_domain,
        source_digest=answer.source_digest,
        candidate_digest=answer.candidate_digest,
        assignment_digest=answer.assignment_digest,
        revision_session_id=answer.revision_session_id,
    )
    return integrity_token(question, answer.alternative_id, answer.direct_text)


def integrity_token(
    question: UnresolvedQuestion,
    alternative_id: str | None,
    direct_text: str | None,
) -> str:
    """Hash one authoritative question and its selected answer value."""
    payload = question.model_dump(mode="json")
    payload.update(alternative_id=alternative_id, direct_text=direct_text)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
