# Copyright 2026 Transcription Module contributors

"""Canonical v2 candidate-attempt recording boundary."""

from pathlib import Path

from pydantic import TypeAdapter

from translation_module.canonical._attempt_models import (
    AttemptBindingError,
    TranslationAttempt,
    TranslationAttemptBinding,
    TranslationAttemptOutcome,
    TranslationLineRationale,
)
from translation_module.canonical.legacy_authority import AttemptRecordIdentity
from translation_module.canonical.runtime import CanonicalTranslationWorkflow
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.canonical.validation import validate_canonical_save
from translation_module.cli_io import read_json

type CanonicalRecordOptions = tuple[Path, AttemptRecordIdentity, Path]
RationaleAdapter = TypeAdapter(tuple[TranslationLineRationale, ...])


def record_canonical_candidate(
    source: SourceTranscriptV2,
    candidate: TranslationDocumentV2,
    options: CanonicalRecordOptions,
) -> None:
    """Validate and append one successful canonical v2 candidate attempt."""
    authority_root, identity, rationale_path = options
    validate_canonical_save(source)
    validate_canonical_save(candidate)
    reference = candidate.source_ref
    if (
        reference.document_id != source.document_id
        or reference.revision_id != source.revision_id
        or reference.content_digest != source.content_digest
    ):
        message = "candidate source reference is stale"
        raise AttemptBindingError(message)
    rationales = RationaleAdapter.validate_json(read_json(rationale_path))
    workflow = CanonicalTranslationWorkflow(authority_root)
    attempts = workflow.attempt_store.load()
    existing = next(
        (item for item in attempts if item.attempt_id == identity.attempt_id),
        None,
    )
    binding = TranslationAttemptBinding(
        translation_document_id=candidate.document_id,
        translation_revision_id=candidate.revision_id,
        source_document_id=reference.document_id,
        source_revision_id=reference.revision_id,
        source_content_digest=reference.content_digest,
        prepared_context_asset_id=candidate.prepared_context_ref.asset_id,
        prepared_context_revision_id=candidate.prepared_context_ref.revision_id,
        prepared_context_content_digest=candidate.prepared_context_ref.content_digest,
        glossary_asset_id=candidate.glossary_ref.asset_id,
        glossary_revision_id=candidate.glossary_ref.revision_id,
        glossary_content_digest=candidate.glossary_ref.content_digest,
    )
    payload = {
        "attempt_id": identity.attempt_id,
        "ordinal": existing.ordinal if existing is not None else len(attempts) + 1,
        "kind": identity.kind,
        "outcome": TranslationAttemptOutcome.SUCCEEDED,
        "binding": binding,
        "candidate_digest": candidate.content_digest,
        "rationales": rationales,
    }
    if identity.parent_attempt_id is not None:
        payload["parent_attempt_id"] = identity.parent_attempt_id
    attempt = TranslationAttempt.model_validate(payload)
    _ = workflow.record_attempt(attempt, candidate)


__all__ = ["record_canonical_candidate"]
