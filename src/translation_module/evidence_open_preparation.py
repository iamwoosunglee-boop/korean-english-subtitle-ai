# Copyright 2026 Transcription Module contributors

"""Prepare canonical requests from persisted explicit frame opens."""

from typing import Protocol, Self, assert_never

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    CanonicalEvidenceKind,
    CanonicalId,
    CanonicalModel,
)
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.source import SourceCueV2, SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.common import Sha256Digest, SourceCueId, TargetCueId
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenRecord,
    EvidenceOpenRecordError,
    verify_evidence_open_digest,
)
from translation_module.review_routing import bind_review_assignment_digest
from translation_module.workflow import ReviewAssignment, ReviewDomain


class EvidenceOpenRecordReader(Protocol):
    """Load persisted explicit opens without reading frame bytes."""

    def records_for(
        self,
        evidence_ids: tuple[str, ...],
        actor_role: EvidenceActorRole,
        consumer_id: str,
    ) -> tuple[EvidenceOpenRecord, ...]:
        """Return only exact, fully revalidated open records."""
        ...


class EvidenceOpenSelection(CanonicalModel):
    """Canonical evidence scope and consumer binding for preparation."""

    source: SourceTranscriptV2
    evidence_ids: tuple[CanonicalId, ...]
    actor_role: EvidenceActorRole
    consumer_id: CanonicalId
    consumer_content_digest: Sha256Digest

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> Self:
        """Reject ambiguous repeated requested evidence identities."""
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            message = "requested evidence IDs must be unique"
            raise ValueError(message)
        return self


class OpenedReviewInput(CanonicalModel):
    """Canonical source, candidate, and explicit reviewer evidence scope."""

    selection: EvidenceOpenSelection
    candidate: TranslationDocumentV2

    @model_validator(mode="after")
    def consumer_is_exact_reviewer(self) -> Self:
        """Require reviewer role and the exact candidate content binding."""
        if self.selection.actor_role is EvidenceActorRole.REVIEWER:
            if (
                self.selection.consumer_content_digest
                != self.candidate.content_digest
            ):
                message = "evidence consumer candidate digest mismatch"
                raise ValueError(message)
            return self
        if self.selection.actor_role is EvidenceActorRole.TRANSLATOR:
            message = "review preparation requires reviewer-open evidence"
            raise ValueError(message)
        assert_never(self.selection.actor_role)


class OpenedReviewRequest(CanonicalModel):
    """Rationale-free canonical reviewer input with explicit frame proof."""

    schema_version: str = "translation-review-request.v2"
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_document_id: CanonicalId
    candidate_revision_id: CanonicalId
    candidate_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: CanonicalId
    source_cues: tuple[SourceCueV2, ...]
    candidate: TranslationDocumentV2
    evidence: tuple[EvidenceOpenRecord, ...]


def prepare_opened_evidence(
    selection: EvidenceOpenSelection,
    records: EvidenceOpenRecordReader,
) -> tuple[EvidenceOpenRecord, ...]:
    """Resolve persisted frame proof without reading evidence resources."""
    try:
        verify_canonical_digests(selection.source)
    except ValueError as error:
        raise EvidenceOpenRecordError(
            evidence_id="<source>",
            reason="source digest mismatch",
        ) from error
    entries = selection.source.evidence.entries
    by_id = {entry.evidence_id: entry for entry in entries}
    if len(entries) != len(by_id):
        raise EvidenceOpenRecordError(
            evidence_id="<source>",
            reason="source contains duplicate evidence IDs",
        )
    unknown = set(selection.evidence_ids) - set(by_id)
    if unknown:
        raise EvidenceOpenRecordError(
            evidence_id=next(iter(sorted(unknown))),
            reason="evidence is not in source",
        )
    requested_frames = tuple(
        evidence_id
        for evidence_id in selection.evidence_ids
        if by_id[evidence_id].kind is CanonicalEvidenceKind.FRAME
    )
    opened = records.records_for(
        requested_frames,
        selection.actor_role,
        selection.consumer_id,
    )
    if tuple(record.evidence_id for record in opened) != requested_frames:
        raise EvidenceOpenRecordError(
            evidence_id="<request>",
            reason="persisted record scope does not match requested frames",
        )
    for record in opened:
        verify_evidence_open_digest(record)
        if record.consumer_content_digest != selection.consumer_content_digest:
            raise EvidenceOpenRecordError(
                evidence_id=record.evidence_id,
                reason="record candidate content binding is stale",
            )
        entry = by_id[record.evidence_id]
        if not (
            record.source_document_id == selection.source.document_id
            and record.source_revision_id == selection.source.revision_id
            and record.source_content_digest == selection.source.content_digest
            and record.locator == entry.locator
            and record.declared_resource_digest == entry.resource_digest
            and record.observed_resource_digest == entry.resource_digest
            and record.actor_role is selection.actor_role
            and record.consumer_id == selection.consumer_id
            and record.consumer_content_digest == selection.consumer_content_digest
        ):
            raise EvidenceOpenRecordError(
                evidence_id=record.evidence_id,
                reason="persisted record binding does not match preparation input",
            )
    return opened


def build_opened_review_request(
    inputs: OpenedReviewInput,
    records: EvidenceOpenRecordReader,
) -> OpenedReviewRequest:
    """Assemble canonical review input from matching persisted frame opens."""
    try:
        verify_canonical_digests(inputs.candidate)
    except ValueError as error:
        raise EvidenceOpenRecordError(
            evidence_id="<candidate>",
            reason="candidate digest mismatch",
        ) from error
    source = inputs.selection.source
    source_ref = inputs.candidate.source_ref
    if (
        source_ref.document_id != source.document_id
        or source_ref.revision_id != source.revision_id
        or source_ref.content_digest != source.content_digest
    ):
        raise EvidenceOpenRecordError(
            evidence_id="<candidate>",
            reason="candidate source binding is stale",
        )
    evidence = prepare_opened_evidence(inputs.selection, records)
    return OpenedReviewRequest(
        source_document_id=source.document_id,
        source_revision_id=source.revision_id,
        source_content_digest=source.content_digest,
        candidate_document_id=inputs.candidate.document_id,
        candidate_revision_id=inputs.candidate.revision_id,
        candidate_content_digest=inputs.candidate.content_digest,
        reviewer_id=inputs.selection.consumer_id,
        source_cues=source.cues,
        candidate=inputs.candidate,
        evidence=evidence,
    )


def review_assignment_from_opened_request(
    request: OpenedReviewRequest,
    session_ids: tuple[str, str, str, str],
) -> ReviewAssignment:
    """Bind canonical reviewer input into the existing routing assignment."""
    if len(session_ids) != len(set(session_ids)):
        raise EvidenceOpenRecordError(
            evidence_id="<review>",
            reason="review sessions must be distinct",
        )
    return bind_review_assignment_digest(
        ReviewAssignment(
            assignment_digest="0" * 64,
            root_candidate_digest=request.candidate_content_digest,
            current_candidate_digest=request.candidate_content_digest,
            candidate_author_execution_id=session_ids[2],
            reviewer_execution_id=session_ids[3],
            source_digest=request.source_content_digest,
            ordered_source_cue_ids=tuple(
                SourceCueId(cue.cue_id) for cue in request.source_cues
            ),
            ordered_target_cue_ids=tuple(
                TargetCueId(cue.cue_id) for cue in request.candidate.cues
            ),
            reviewed_domains=tuple(ReviewDomain),
            translator_session_id=session_ids[0],
            reviser_session_id=session_ids[1],
            assembler_session_id=session_ids[2],
        )
    )
