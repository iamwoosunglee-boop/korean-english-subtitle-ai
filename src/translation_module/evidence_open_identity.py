# Copyright 2026 Transcription Module contributors

"""Canonical full-authority identity for evidence-open record paths."""

import posixpath
from urllib.parse import unquote, urlsplit, urlunsplit

from translation_module.canonical.common import CanonicalId, CanonicalModel
from translation_module.canonical.digest import canonical_sha256
from translation_module.common import Sha256Digest
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenContext,
    EvidenceOpenRecord,
    EvidenceOpenRequest,
)


class EvidenceOpenIdentity(CanonicalModel):
    """Every authority field selecting one immutable open record."""

    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: Sha256Digest
    evidence_id: CanonicalId
    normalized_locator: str
    declared_resource_digest: Sha256Digest
    observed_resource_digest: Sha256Digest
    actor_role: EvidenceActorRole
    consumer_id: CanonicalId
    consumer_content_digest: Sha256Digest


def normalize_evidence_locator(locator: str) -> str:
    """Return the stable decoded POSIX representation used only for identity."""
    parsed = urlsplit(locator)
    normalized_path = posixpath.normpath(unquote(parsed.path))
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            parsed.query,
            parsed.fragment,
        )
    )


def evidence_open_identity(
    context: EvidenceOpenContext,
    request: EvidenceOpenRequest,
    observed_resource_digest: str,
) -> EvidenceOpenIdentity:
    """Build the full record identity from canonical source authority."""
    entry = next(
        item
        for item in context.source.evidence.entries
        if item.evidence_id == request.evidence_id
    )
    return EvidenceOpenIdentity(
        source_document_id=context.source.document_id,
        source_revision_id=context.source.revision_id,
        source_content_digest=context.source.content_digest,
        evidence_id=request.evidence_id,
        normalized_locator=normalize_evidence_locator(entry.locator),
        declared_resource_digest=entry.resource_digest,
        observed_resource_digest=observed_resource_digest,
        actor_role=request.actor_role,
        consumer_id=request.consumer_id,
        consumer_content_digest=request.consumer_content_digest,
    )


def evidence_open_record_identity(record: EvidenceOpenRecord) -> EvidenceOpenIdentity:
    """Project a persisted record into the exact path identity schema."""
    return EvidenceOpenIdentity(
        source_document_id=record.source_document_id,
        source_revision_id=record.source_revision_id,
        source_content_digest=record.source_content_digest,
        evidence_id=record.evidence_id,
        normalized_locator=normalize_evidence_locator(record.locator),
        declared_resource_digest=record.declared_resource_digest,
        observed_resource_digest=record.observed_resource_digest,
        actor_role=record.actor_role,
        consumer_id=record.consumer_id,
        consumer_content_digest=record.consumer_content_digest,
    )


def evidence_open_record_name(identity: EvidenceOpenIdentity) -> str:
    """Hash canonical JSON identity without delimiter or concatenation ambiguity."""
    return f"evidence-open.{canonical_sha256(identity)}.json"


__all__ = [
    "EvidenceOpenIdentity",
    "evidence_open_identity",
    "evidence_open_record_identity",
    "evidence_open_record_name",
    "normalize_evidence_locator",
]
