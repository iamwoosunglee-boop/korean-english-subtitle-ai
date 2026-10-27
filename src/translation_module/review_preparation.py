# Copyright 2026 Transcription Module contributors

"""Digest-bound independent-review request preparation."""

from typing import Self

from pydantic import Field, model_validator

from translation_module.common import (
    EpisodeContext,
    FrozenModel,
    Sha256Digest,
    SourceCueId,
    TargetCueId,
)
from translation_module.document import TranslationDocument, translation_digest
from translation_module.evidence_preparation import EvidenceResolver, preflight_evidence
from translation_module.preparation import source_digest
from translation_module.preparation_errors import PreparationError
from translation_module.review_routing import (
    bind_review_assignment_digest,
)
from translation_module.review_state import ReviewAttempts
from translation_module.source import EvidenceEntry, SourceCue, SourceSnapshot
from translation_module.workflow import (
    ReviewAssignment,
    ReviewDomain,
)


class ReviewRequestValidationError(ValueError):
    """Review-request data violates its parent-issued contract."""


class ReviewScope(FrozenModel):
    """Ordered parent-issued review ownership."""

    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    target_cue_ids: tuple[TargetCueId, ...]
    candidate_digest: Sha256Digest | None = None
    reviewed_domains: tuple[ReviewDomain, ...] = Field(
        default=tuple(ReviewDomain),
        min_length=1,
    )

    @model_validator(mode="after")
    def scope_is_exact(self) -> Self:
        """Reject duplicate IDs, domains, and ambiguous focused scope."""
        if len(self.source_cue_ids) != len(set(self.source_cue_ids)):
            msg = "source review scope IDs must be unique"
            raise ReviewRequestValidationError(msg)
        if len(self.target_cue_ids) != len(set(self.target_cue_ids)):
            msg = "target review scope IDs must be unique"
            raise ReviewRequestValidationError(msg)
        if len(self.reviewed_domains) != len(set(self.reviewed_domains)):
            msg = "review scope domains must be unique"
            raise ReviewRequestValidationError(msg)
        complete_domains = tuple(ReviewDomain)
        if self.candidate_digest is None and self.reviewed_domains != complete_domains:
            msg = "focused review scope requires a candidate digest"
            raise ReviewRequestValidationError(msg)
        if self.candidate_digest is not None and set(self.reviewed_domains) == set(
            complete_domains
        ):
            msg = "focused review scope requires fewer than six domains"
            raise ReviewRequestValidationError(msg)
        return self


class ReviewRequest(FrozenModel):
    """Transient parent-built input for one independent or focused review."""

    schema_version: str = "translation-review-request.v1"
    source_document_id: str
    source_digest: Sha256Digest
    candidate_document_id: str
    candidate_digest: Sha256Digest
    source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    target_cue_ids: tuple[TargetCueId, ...]
    source_cues: tuple[SourceCue, ...] = Field(min_length=1)
    read_only_neighbors: tuple[SourceCue, ...] = ()
    context: EpisodeContext
    evidence: tuple[EvidenceEntry, ...] = ()
    candidate: TranslationDocument
    reviewed_domains: tuple[ReviewDomain, ...] = tuple(ReviewDomain)
    translator_session_id: str = Field(min_length=1)
    reviser_session_id: str = Field(min_length=1)
    assembler_session_id: str = Field(min_length=1)
    reviewer_session_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def scope_and_sessions_are_exact(self) -> Self:
        """Require exact whole or focused scope and independent sessions."""
        complete_domains = tuple(ReviewDomain)
        if len(self.reviewed_domains) != len(set(self.reviewed_domains)):
            msg = "review request domains must be unique"
            raise ReviewRequestValidationError(msg)
        if self.reviewed_domains != complete_domains:
            source_scope = set(self.source_cue_ids)
            complete_source_scope = {
                cue.cue_id for cue in (*self.source_cues, *self.read_only_neighbors)
            }
            target_scope = set(self.target_cue_ids)
            complete_target_scope = {cue.cue_id for cue in self.candidate.cues}
            if set(self.reviewed_domains) == set(complete_domains) or (
                source_scope == complete_source_scope
                and target_scope == complete_target_scope
            ):
                msg = (
                    "review request requires exactly six domains unless "
                    "focused cue scope is strict"
                )
                raise ReviewRequestValidationError(msg)
        sessions = (
            self.translator_session_id,
            self.reviser_session_id,
            self.assembler_session_id,
            self.reviewer_session_id,
        )
        if len(sessions) != len(set(sessions)):
            msg = "review request requires different runtime sessions"
            raise ReviewRequestValidationError(msg)
        return self


def review_assignment_from_request(
    request: ReviewRequest,
    *,
    attempts: ReviewAttempts | None = None,
) -> ReviewAssignment:
    """Bind a revalidated parent review request into its routing assignment."""
    try:
        request = ReviewRequest.model_validate(request.model_dump())
    except ValueError as error:
        raise PreparationError(str(error)) from error
    if (
        attempts is not None
        and attempts.current_candidate_digest != request.candidate_digest
    ):
        msg = "attempt state is stale"
        raise PreparationError(msg)
    root_candidate_digest = (
        attempts.root_candidate_digest
        if attempts is not None
        else request.candidate_digest
    )
    complete_domains = tuple(ReviewDomain)
    is_focused = request.reviewed_domains != complete_domains
    ordered_source_cue_ids = (
        tuple(
            cue.cue_id
            for cue in sorted(
                (*request.source_cues, *request.read_only_neighbors),
                key=lambda cue: cue.sequence,
            )
        )
        if is_focused
        else request.source_cue_ids
    )
    ordered_target_cue_ids = (
        tuple(cue.cue_id for cue in request.candidate.cues)
        if is_focused
        else request.target_cue_ids
    )
    return bind_review_assignment_digest(
        ReviewAssignment(
            assignment_digest="0" * 64,
            root_candidate_digest=root_candidate_digest,
            current_candidate_digest=request.candidate_digest,
            candidate_author_execution_id=request.assembler_session_id,
            reviewer_execution_id=request.reviewer_session_id,
            source_digest=request.source_digest,
            ordered_source_cue_ids=ordered_source_cue_ids,
            ordered_target_cue_ids=ordered_target_cue_ids,
            reviewed_domains=(
                complete_domains if is_focused else request.reviewed_domains
            ),
            translator_session_id=request.translator_session_id,
            reviser_session_id=request.reviser_session_id,
            assembler_session_id=request.assembler_session_id,
            focused_source_cue_ids=(request.source_cue_ids if is_focused else ()),
            focused_target_cue_ids=(request.target_cue_ids if is_focused else ()),
            focused_domains=(request.reviewed_domains if is_focused else ()),
        )
    )


def build_review_request(
    source: SourceSnapshot,
    candidate: TranslationDocument,
    scope: ReviewScope,
    session_ids: tuple[str, str, str, str],
    resolver: EvidenceResolver | None = None,
) -> ReviewRequest:
    """Build exact, rationale-free reviewer input from verified artifacts."""
    if source_digest(source) != source.content_digest:
        msg = "source snapshot digest mismatch"
        raise PreparationError(msg)
    if translation_digest(candidate) != candidate.content_digest:
        msg = "candidate digest mismatch"
        raise PreparationError(msg)
    if (
        scope.candidate_digest is not None
        and scope.candidate_digest != candidate.content_digest
    ):
        msg = "focused review scope candidate digest mismatch"
        raise PreparationError(msg)
    if len(session_ids) != len(set(session_ids)):
        msg = "review request requires different runtime sessions"
        raise PreparationError(msg)
    source_by_id = {cue.cue_id: cue for cue in source.cues}
    target_by_id = {cue.cue_id: cue for cue in candidate.cues}
    if unknown := set(scope.source_cue_ids) - set(source_by_id):
        msg = f"unknown source cue: {unknown}"
        raise PreparationError(msg)
    if unknown := set(scope.target_cue_ids) - set(target_by_id):
        msg = f"unknown target cue: {unknown}"
        raise PreparationError(msg)
    selected = tuple(source_by_id[cue_id] for cue_id in scope.source_cue_ids)
    evidence_ids = {
        evidence_id for cue in selected for evidence_id in cue.evidence_ids
    } | set(source.context_evidence_ids)
    if evidence_ids and resolver is None:
        msg = "evidence resolver is required for review"
        raise PreparationError(msg)
    evidence = preflight_evidence(source, resolver, evidence_ids) if resolver else ()
    return ReviewRequest(
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        candidate_document_id=candidate.document_id,
        candidate_digest=candidate.content_digest,
        source_cue_ids=scope.source_cue_ids,
        target_cue_ids=scope.target_cue_ids,
        source_cues=selected,
        read_only_neighbors=tuple(
            cue for cue in source.cues if cue.cue_id not in set(scope.source_cue_ids)
        ),
        context=source.context,
        evidence=evidence,
        candidate=candidate,
        reviewed_domains=scope.reviewed_domains,
        translator_session_id=session_ids[0],
        reviser_session_id=session_ids[1],
        assembler_session_id=session_ids[2],
        reviewer_session_id=session_ids[3],
    )
