# Copyright 2026 Transcription Module contributors

"""Atomic storage for explicit canonical frame-open evidence."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol, assert_never, final

from pydantic import ValidationError

from translation_module import evidence_open_identity as identity
from translation_module.canonical.common import (
    CanonicalEvidenceEntry,
    CanonicalEvidenceKind,
)
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.cli_io import write_model_atomic
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenContext,
    EvidenceOpenError,
    EvidenceOpenRecord,
    EvidenceOpenRecordError,
    EvidenceOpenRequest,
    bind_evidence_open_digest,
    verify_evidence_open_digest,
)

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical.source import SourceTranscriptV2


class FrameResolver(Protocol):
    """Read one evidence resource without exposing workflow storage."""

    def read(self, base_uri: str, locator: str) -> bytes:
        """Return exact evidence resource bytes."""
        ...


def _validate_source(source: SourceTranscriptV2) -> None:
    try:
        verify_canonical_digests(source)
    except ValueError as error:
        raise EvidenceOpenRecordError(
            evidence_id="<source>",
            reason="source digest mismatch",
        ) from error
    evidence_ids = tuple(entry.evidence_id for entry in source.evidence.entries)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise EvidenceOpenRecordError(
            evidence_id="<source>",
            reason="source contains duplicate evidence IDs",
        )


@final
class EvidenceOpenStore:
    """Persist explicit frame opens for one canonical source authority."""

    def __init__(
        self,
        root: Path,
        context: EvidenceOpenContext,
        resolver: FrameResolver | None,
    ) -> None:
        """Bind a dedicated record root, canonical source, and frame resolver."""
        self._root = root
        self._context = context
        self._resolver = resolver

    def record_path(
        self,
        request: EvidenceOpenRequest,
        observed_resource_digest: str | None = None,
    ) -> Path:
        """Return the deterministic full-authority path for one open request."""
        entry = self._entry(request.evidence_id)
        record_identity = identity.evidence_open_identity(
            self._context,
            request,
            observed_resource_digest or entry.resource_digest,
        )
        return self._root / identity.evidence_open_record_name(record_identity)

    def _entry(self, evidence_id: str) -> CanonicalEvidenceEntry:
        matches = tuple(
            entry
            for entry in self._context.source.evidence.entries
            if entry.evidence_id == evidence_id
        )
        if len(matches) != 1:
            reason = (
                "evidence is not in source"
                if not matches
                else "source contains conflicting evidence IDs"
            )
            raise EvidenceOpenError(evidence_id=evidence_id, reason=reason)
        entry = matches[0]
        if entry.kind is CanonicalEvidenceKind.FRAME:
            return entry
        if entry.kind in (CanonicalEvidenceKind.IMAGE, CanonicalEvidenceKind.TEXT):
            raise EvidenceOpenError(
                evidence_id=evidence_id,
                reason="evidence is not a frame",
            )
        assert_never(entry.kind)

    def _validate_record(
        self,
        record: EvidenceOpenRecord,
        request: EvidenceOpenRequest,
        path: Path,
    ) -> EvidenceOpenRecord:
        verify_evidence_open_digest(record)
        entry = self._entry(record.evidence_id)
        source = self._context.source
        expected = (
            record.source_document_id == source.document_id
            and record.source_revision_id == source.revision_id
            and record.source_content_digest == source.content_digest
            and record.evidence_id == request.evidence_id
            and record.locator == entry.locator
            and record.declared_resource_digest == entry.resource_digest
            and record.observed_resource_digest == entry.resource_digest
            and record.actor_role is request.actor_role
            and record.consumer_id == request.consumer_id
            and record.consumer_content_digest == request.consumer_content_digest
            and path == self.record_path(request)
            and path
            == self._root
            / identity.evidence_open_record_name(
                identity.evidence_open_record_identity(record)
            )
        )
        if not expected:
            raise EvidenceOpenRecordError(
                evidence_id=record.evidence_id,
                reason="record binding is stale or conflicting",
            )
        return record

    def _load(
        self,
        path: Path,
        request: EvidenceOpenRequest,
    ) -> EvidenceOpenRecord:
        try:
            record = EvidenceOpenRecord.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            raise EvidenceOpenRecordError(
                evidence_id=request.evidence_id,
                reason="record is unavailable or invalid",
            ) from error
        return self._validate_record(record, request, path)

    def open_frame(
        self,
        evidence_id: str,
        actor_role: EvidenceActorRole,
        attempt_or_review_id: str,
    ) -> EvidenceOpenRecord:
        """Read exactly one named frame and atomically persist its proof."""
        request = EvidenceOpenRequest(
            evidence_id=evidence_id,
            actor_role=actor_role,
            consumer_id=attempt_or_review_id,
            consumer_content_digest=self._context.consumer_content_digest,
        )
        source = self._context.source
        _validate_source(source)
        entry = self._entry(request.evidence_id)
        path = self.record_path(request)
        if path.exists():
            return self._load(path, request)
        resolver = self._resolver
        if resolver is None:
            raise EvidenceOpenError(
                evidence_id=request.evidence_id,
                reason="frame resolver is unavailable",
            )
        try:
            resource = resolver.read(
                source.evidence.base_uri,
                entry.locator,
            )
        except (KeyError, OSError) as error:
            raise EvidenceOpenError(
                evidence_id=request.evidence_id,
                reason="frame is unavailable",
            ) from error
        observed_digest = hashlib.sha256(resource).hexdigest()
        if observed_digest != entry.resource_digest:
            raise EvidenceOpenError(
                evidence_id=request.evidence_id,
                reason="frame digest mismatch",
            )
        record = bind_evidence_open_digest(
            EvidenceOpenRecord(
                source_document_id=source.document_id,
                source_revision_id=source.revision_id,
                source_content_digest=source.content_digest,
                evidence_id=request.evidence_id,
                locator=entry.locator,
                declared_resource_digest=entry.resource_digest,
                observed_resource_digest=observed_digest,
                actor_role=request.actor_role,
                consumer_id=request.consumer_id,
                consumer_content_digest=request.consumer_content_digest,
                record_digest="0" * 64,
            )
        )
        write_model_atomic(path, record)
        return record

    def records_for(
        self,
        evidence_ids: tuple[str, ...],
        actor_role: EvidenceActorRole,
        consumer_id: str,
    ) -> tuple[EvidenceOpenRecord, ...]:
        """Load matching persisted proofs without reading any frame bytes."""
        _validate_source(self._context.source)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise EvidenceOpenRecordError(
                evidence_id="<request>",
                reason="requested evidence IDs are duplicated",
            )
        records: list[EvidenceOpenRecord] = []
        for evidence_id in evidence_ids:
            request = EvidenceOpenRequest(
                evidence_id=evidence_id,
                actor_role=actor_role,
                consumer_id=consumer_id,
                consumer_content_digest=self._context.consumer_content_digest,
            )
            path = self.record_path(request)
            if not path.exists():
                raise EvidenceOpenRecordError(
                    evidence_id=evidence_id,
                    reason="frame was not explicitly opened",
                )
            records.append(self._load(path, request))
        return tuple(records)


__all__ = ["EvidenceOpenStore", "FrameResolver"]
