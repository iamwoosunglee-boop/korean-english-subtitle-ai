# Copyright 2026 Transcription Module contributors

"""Canonical authority projection for legacy-v1 production CLI candidates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from pydantic import Field

from translation_module.canonical._attempt_models import (
    TranslationAttempt,
    TranslationAttemptBinding,
    TranslationAttemptKind,
    TranslationAttemptOutcome,
    TranslationLineRationale,
)
from translation_module.canonical._preflight_models import CandidatePreflightContext
from translation_module.canonical.common import CanonicalModel
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.migration import migrate_v1_project
from translation_module.canonical.runtime import CanonicalTranslationWorkflow
from translation_module.document import translation_digest

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical.context import ContentContextAsset
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import TranslationDocumentV2
    from translation_module.common import Sha256Digest
    from translation_module.document import TranslationDocument
    from translation_module.preparation import TranslationBatchPlan
    from translation_module.source import SourceSnapshot

class _AttemptPayload(TypedDict):
    attempt_id: str
    ordinal: int
    kind: TranslationAttemptKind
    outcome: TranslationAttemptOutcome
    binding: TranslationAttemptBinding
    parent_attempt_id: NotRequired[str]
    candidate_digest: NotRequired[Sha256Digest]
    rationales: NotRequired[tuple[TranslationLineRationale, ...]]


class LegacyAuthorityError(ValueError):
    """A legacy candidate cannot produce canonical attempt authority."""


class AttemptRationaleInput(CanonicalModel):
    """Explicit Korean rationale input consumed by production CLI attempts."""

    schema_version: Literal["translation-attempt-rationales.v1"] = (
        "translation-attempt-rationales.v1"
    )
    rationales: tuple[TranslationLineRationale, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class LegacyCandidateAuthority:
    """Deterministic canonical graph projected from one legacy candidate."""

    source: SourceTranscriptV2
    context: ContentContextAsset
    glossary: TranslationGlossaryAsset
    candidate: TranslationDocumentV2

    @property
    def preflight_context(self) -> CandidatePreflightContext:
        """Return the exact candidate/source/glossary preflight graph."""
        return CandidatePreflightContext(self.candidate, self.source, self.glossary)


def project_legacy_candidate(
    source: SourceSnapshot,
    document: TranslationDocument,
    authority_root: Path,
) -> LegacyCandidateAuthority:
    """Project stable v2 authority without changing either legacy contract."""
    bundle = migrate_v1_project(source, document, output_dir=authority_root)
    context = bundle.prepared_context
    glossary = bundle.glossary
    candidate = bundle.translation
    if context is None or glossary is None or candidate is None:
        message = "legacy candidate migration produced no translation authority"
        raise LegacyAuthorityError(message)
    revision = f"{candidate.document_id}:legacy:{document.content_digest}"
    candidate = bind_canonical_digests(
        candidate.model_copy(update={"revision_id": revision})
    )
    return LegacyCandidateAuthority(bundle.source, context, glossary, candidate)


def _binding(document: TranslationDocumentV2) -> TranslationAttemptBinding:
    return TranslationAttemptBinding(
        translation_document_id=document.document_id,
        translation_revision_id=document.revision_id,
        source_document_id=document.source_ref.document_id,
        source_revision_id=document.source_ref.revision_id,
        source_content_digest=document.source_ref.content_digest,
        prepared_context_asset_id=document.prepared_context_ref.asset_id,
        prepared_context_revision_id=document.prepared_context_ref.revision_id,
        prepared_context_content_digest=document.prepared_context_ref.content_digest,
        glossary_asset_id=document.glossary_ref.asset_id,
        glossary_revision_id=document.glossary_ref.revision_id,
        glossary_content_digest=document.glossary_ref.content_digest,
    )


@dataclass(frozen=True, slots=True)
class AttemptRecordIdentity:
    """Caller-injected immutable identity and lineage for one execution."""

    attempt_id: str
    kind: TranslationAttemptKind
    parent_attempt_id: str | None = None


def _attempt_ordinal(
    workflow: CanonicalTranslationWorkflow, attempt_id: str
) -> int:
    attempts = workflow.attempt_store.load()
    existing = next(
        (item for item in attempts if item.attempt_id == attempt_id), None
    )
    return existing.ordinal if existing is not None else len(attempts) + 1


def record_legacy_success(
    workflow: CanonicalTranslationWorkflow,
    authority: LegacyCandidateAuthority,
    identity: AttemptRecordIdentity,
    rationale_input: AttemptRationaleInput,
) -> TranslationAttempt:
    """Append one successful production attempt before legacy publication."""
    payload = _AttemptPayload(
        attempt_id=identity.attempt_id,
        ordinal=_attempt_ordinal(workflow, identity.attempt_id),
        kind=identity.kind,
        outcome=TranslationAttemptOutcome.SUCCEEDED,
        binding=_binding(authority.candidate),
        candidate_digest=authority.candidate.content_digest,
        rationales=rationale_input.rationales,
    )
    if identity.parent_attempt_id is not None:
        payload["parent_attempt_id"] = identity.parent_attempt_id
    attempt = TranslationAttempt.model_validate(payload)
    return workflow.record_attempt(attempt, authority.candidate)


def record_failed_attempt(
    workflow: CanonicalTranslationWorkflow,
    candidate: TranslationDocumentV2,
    identity: AttemptRecordIdentity,
) -> TranslationAttempt:
    """Append one failed attempt without candidate or rationale claims."""
    payload = _AttemptPayload(
        attempt_id=identity.attempt_id,
        ordinal=_attempt_ordinal(workflow, identity.attempt_id),
        kind=identity.kind,
        outcome=TranslationAttemptOutcome.FAILED,
        binding=_binding(candidate),
    )
    if identity.parent_attempt_id is not None:
        payload["parent_attempt_id"] = identity.parent_attempt_id
    attempt = TranslationAttempt.model_validate(payload)
    return workflow.record_attempt(attempt, candidate)


def record_failed_legacy_candidate(
    source: SourceSnapshot,
    document: TranslationDocument,
    authority_root: Path,
    identity: AttemptRecordIdentity,
) -> None:
    """Append a failed attempt for intended normalized legacy bytes."""
    bound = document.model_copy(update={"content_digest": translation_digest(document)})
    projected = project_legacy_candidate(source, bound, authority_root)
    _ = record_failed_attempt(
        CanonicalTranslationWorkflow(authority_root), projected.candidate, identity
    )


def record_failed_legacy_intent(
    source: SourceSnapshot,
    batch: TranslationBatchPlan,
    document_id: str,
    authority_root: Path,
    identity: AttemptRecordIdentity,
) -> None:
    """Append failure authority before any candidate can be assembled."""
    binding = intended_attempt_binding(
        source, batch, document_id, identity.attempt_id
    )
    _ = record_failed_intent(
        CanonicalTranslationWorkflow(authority_root), binding, identity
    )


def intended_attempt_binding(
    source: SourceSnapshot,
    batch: TranslationBatchPlan,
    document_id: str,
    attempt_id: str,
) -> TranslationAttemptBinding:
    """Bind a pre-assembly execution to validated immutable CLI inputs."""
    plan_digest = hashlib.sha256(batch.model_dump_json().encode()).hexdigest()
    glossary_digest = hashlib.sha256(
        f"legacy-empty-glossary:{source.content_digest}".encode()
    ).hexdigest()
    return TranslationAttemptBinding(
        translation_document_id=document_id,
        translation_revision_id=f"{document_id}:attempt:{attempt_id}",
        source_document_id=source.document_id,
        source_revision_id=f"legacy:{source.content_digest}",
        source_content_digest=source.content_digest,
        prepared_context_asset_id=batch.plan_id,
        prepared_context_revision_id=f"legacy-plan:{source.content_digest}",
        prepared_context_content_digest=plan_digest,
        glossary_asset_id=f"{source.document_id}:legacy-empty-glossary",
        glossary_revision_id=f"legacy:{source.content_digest}",
        glossary_content_digest=glossary_digest,
    )


def record_failed_intent(
    workflow: CanonicalTranslationWorkflow,
    binding: TranslationAttemptBinding,
    identity: AttemptRecordIdentity,
) -> TranslationAttempt:
    """Append a failed execution whose candidate was never constructed."""
    payload = _AttemptPayload(
        attempt_id=identity.attempt_id,
        ordinal=_attempt_ordinal(workflow, identity.attempt_id),
        kind=identity.kind,
        outcome=TranslationAttemptOutcome.FAILED,
        binding=binding,
    )
    if identity.parent_attempt_id is not None:
        payload["parent_attempt_id"] = identity.parent_attempt_id
    attempt = TranslationAttempt.model_validate(payload)
    return workflow.record_failed_attempt(attempt)


__all__ = [
    "AttemptRationaleInput",
    "AttemptRecordIdentity",
    "LegacyAuthorityError",
    "LegacyCandidateAuthority",
    "intended_attempt_binding",
    "project_legacy_candidate",
    "record_failed_attempt",
    "record_failed_intent",
    "record_failed_legacy_candidate",
    "record_failed_legacy_intent",
    "record_legacy_success",
]
