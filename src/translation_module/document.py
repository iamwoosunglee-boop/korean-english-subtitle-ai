# Copyright 2026 Transcription Module contributors

"""Published context-rich translation document contracts."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from translation_module.common import (
    ContentKind,
    ContextId,
    EpisodeContext,
    EvidenceId,
    FrozenModel,
    NonNegativeMilliseconds,
    PositiveMilliseconds,
    Sha256Digest,
    SourceCueId,
    SourceLocale,
    TargetCueId,
    TargetLocale,
    canonical_digest,
)
from translation_module.workflow import ReviewDomain


class DocumentStatus(StrEnum):
    """Only canonical translation document states."""

    NEEDS_CONFIRMATION = "needs_confirmation"
    FINALIZED = "finalized"


class RetainedContext(FrozenModel):
    """Concise scene fact retained for downstream consumers."""

    context_id: ContextId
    summary: str = Field(min_length=1)
    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = ()


class TargetCue(FrozenModel):
    """One display-ready translated subtitle cue."""

    sequence: int = Field(ge=1, strict=True)
    cue_id: TargetCueId
    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    start_ms: NonNegativeMilliseconds
    end_ms: PositiveMilliseconds
    text: str = Field(min_length=1)
    speakers: tuple[str, ...] = Field(min_length=1)
    content_kind: ContentKind = Field(strict=True)
    context_ids: tuple[ContextId, ...] = ()
    evidence_ids: tuple[EvidenceId, ...] = ()

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed half-open intervals."""
        if self.end_ms <= self.start_ms:
            message = "end_ms must be greater than start_ms"
            raise ValueError(message)
        return self


class TranslationAlternative(FrozenModel):
    """One complete user-facing translation choice."""

    option_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    meaning: str = Field(min_length=1)
    strength: str = Field(min_length=1)
    risk: str = Field(min_length=1)


class UnresolvedQuestion(FrozenModel):
    """Exact machine-consumed scope for one user translation decision."""

    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    target_cue_ids: tuple[TargetCueId, ...] = Field(min_length=1)
    review_domain: ReviewDomain = Field(strict=True)
    source_digest: Sha256Digest
    candidate_digest: Sha256Digest
    assignment_digest: Sha256Digest
    revision_session_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def scope_is_ordered_and_unique(self) -> Self:
        """Reject empty or repeated affected cue IDs without rewriting scope."""
        _validate_unresolved_scope(self.source_cue_ids, self.target_cue_ids)
        return self


class UnresolvedItem(FrozenModel):
    """Translation choice that requires a user answer."""

    unresolved_id: str = Field(min_length=1)
    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    target_cue_ids: tuple[TargetCueId, ...] = Field(min_length=1)
    current_translation: str = Field(min_length=1)
    why_difficult: str = Field(min_length=1)
    evidence_ids: tuple[EvidenceId, ...] = ()
    alternatives: tuple[TranslationAlternative, ...] = Field(
        min_length=2,
        max_length=4,
    )
    recommendation_id: str = Field(min_length=1)
    recommendation_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def recommendation_names_an_option(self) -> Self:
        """Require unique scope and a recommendation from one complete option."""
        _validate_unresolved_scope(self.source_cue_ids, self.target_cue_ids)
        option_ids = tuple(option.option_id for option in self.alternatives)
        if len(option_ids) != len(set(option_ids)):
            message = "translation option IDs must be unique"
            raise ValueError(message)
        if self.recommendation_id not in option_ids:
            message = "recommendation_id must reference an alternative"
            raise ValueError(message)
        return self


def _validate_unresolved_scope(
    source_cue_ids: tuple[SourceCueId, ...],
    target_cue_ids: tuple[TargetCueId, ...],
) -> None:
    for cue_ids, kind in (
        (source_cue_ids, "source"),
        (target_cue_ids, "target"),
    ):
        if not cue_ids:
            message = f"{kind} cue IDs must be nonempty"
            raise ValueError(message)
        if len(cue_ids) != len(set(cue_ids)):
            message = f"{kind} cue IDs must be unique"
            raise ValueError(message)


def _validate_unresolved_questions(document: "TranslationDocument") -> None:
    unresolved_ids = tuple(item.unresolved_id for item in document.unresolved)
    if len(unresolved_ids) != len(set(unresolved_ids)):
        message = "unresolved item IDs must be unique"
        raise ValueError(message)
    question_ids = tuple(
        question.question_id for question in document.unresolved_questions
    )
    if len(question_ids) != len(set(question_ids)):
        message = "unresolved question IDs must be unique"
        raise ValueError(message)
    if set(question_ids) != set(unresolved_ids):
        message = "every unresolved item requires one authoritative question"
        raise ValueError(message)
    unresolved_by_id = {item.unresolved_id: item for item in document.unresolved}
    if any(
        question.source_digest != document.source_digest
        or question.candidate_digest != document.content_digest
        or question.source_cue_ids
        != unresolved_by_id[question.question_id].source_cue_ids
        or question.target_cue_ids
        != unresolved_by_id[question.question_id].target_cue_ids
        for question in document.unresolved_questions
    ):
        message = "authoritative question binding does not match document"
        raise ValueError(message)


def translation_digest(document: "TranslationDocument") -> str:
    """Hash canonical translation content without its self-referential digest."""
    return canonical_digest(
        document,
        exclude={"content_digest", "unresolved_questions"},
    )


class TranslationDocument(FrozenModel):
    """Canonical JSON published for confirmation or downstream use."""

    schema_version: Literal["translation-document.v1"] = "translation-document.v1"
    document_id: str = Field(min_length=1)
    content_digest: Sha256Digest
    source_document_id: str = Field(min_length=1)
    source_digest: Sha256Digest
    source_locale: SourceLocale
    target_locale: TargetLocale
    context: EpisodeContext
    retained_contexts: tuple[RetainedContext, ...]
    cues: tuple[TargetCue, ...]
    unresolved: tuple[UnresolvedItem, ...] = ()
    unresolved_questions: tuple[UnresolvedQuestion, ...] = ()
    status: DocumentStatus = Field(strict=True)

    @model_validator(mode="after")
    def document_is_internally_consistent(self) -> Self:
        """Enforce identity, references and canonical state semantics."""
        sequences = tuple(cue.sequence for cue in self.cues)
        if sequences != tuple(range(1, len(self.cues) + 1)):
            message = "target cue sequences must be contiguous"
            raise ValueError(message)
        cue_ids = tuple(cue.cue_id for cue in self.cues)
        if len(cue_ids) != len(set(cue_ids)):
            message = "target cue IDs must be unique"
            raise ValueError(message)
        context_ids = tuple(context.context_id for context in self.retained_contexts)
        if len(context_ids) != len(set(context_ids)):
            message = "retained context IDs must be unique"
            raise ValueError(message)
        known_contexts = set(context_ids)
        unknown_contexts = {
            context_id
            for cue in self.cues
            for context_id in cue.context_ids
            if context_id not in known_contexts
        }
        if unknown_contexts:
            message = f"target cues reference unknown context: {unknown_contexts}"
            raise ValueError(message)
        _validate_unresolved_questions(self)
        known_targets = set(cue_ids)
        unknown_targets = {
            target_id
            for item in self.unresolved
            for target_id in item.target_cue_ids
            if target_id not in known_targets
        }
        if unknown_targets:
            message = f"unresolved items reference unknown target: {unknown_targets}"
            raise ValueError(message)
        match self.status:
            case DocumentStatus.NEEDS_CONFIRMATION:
                if not self.unresolved:
                    message = "needs_confirmation requires unresolved items"
                    raise ValueError(message)
            case DocumentStatus.FINALIZED:
                if self.unresolved:
                    message = "finalized documents cannot be unresolved"
                    raise ValueError(message)
        return self
