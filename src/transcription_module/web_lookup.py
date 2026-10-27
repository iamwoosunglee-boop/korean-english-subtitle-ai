# Copyright 2026 Transcription Module contributors

"""Supporting-only web provenance for genuinely uncertain external facts."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, Self

from pydantic import Field, HttpUrl, model_validator

from transcription_module._drafting_models import (
    UnresolvedItem,
    UnresolvedReasonCode,
)
from transcription_module.models import FrozenModel, RunId
from transcription_module.storage import write_model_atomic

if TYPE_CHECKING:
    from pathlib import Path


class ExternalFactKind(StrEnum):
    """External fact classes eligible when video evidence is unclear."""

    PROPER_NAME = "proper_name"
    TEAM_NAME = "team_name"
    LYRICS = "lyrics"
    EXTERNAL_FACT = "external_fact"


class WebLookupRelation(StrEnum):
    """How one web hit relates to the video-grounded candidate."""

    SUPPORTS = "supports"
    CONFLICTS = "conflicts"
    INCONCLUSIVE = "inconclusive"


class WebLookupStatus(StrEnum):
    """The only allowed downstream states for web evidence."""

    SUPPORTING_ONLY = "supporting_only"
    NEEDS_CONFIRMATION = "needs_confirmation"


class WebLookupRequest(FrozenModel):
    """A typed proof that an external fact is genuinely unresolved."""

    request_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    kind: ExternalFactKind
    at_ms: int = Field(ge=0)
    target_cue_index: int | None = Field(default=None, ge=0)
    current_evidence_text: str = Field(min_length=1)
    uncertainty_reason: str = Field(min_length=1)
    source_evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> Self:
        """Keep the current video/audio evidence scope explicit and unique."""
        if len(self.source_evidence_ids) != len(set(self.source_evidence_ids)):
            message = "web lookup source evidence IDs must be unique"
            raise ValueError(message)
        return self


class WebLookupHit(FrozenModel):
    """One typed URL and excerpt returned by an injected provider."""

    url: HttpUrl
    excerpt: str = Field(min_length=1)
    relation: WebLookupRelation


class WebLookupRecord(FrozenModel):
    """One immutable lookup that can never become source truth."""

    request: WebLookupRequest
    looked_up_at: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    )
    hits: tuple[WebLookupHit, ...] = Field(min_length=1)
    status: WebLookupStatus

    @model_validator(mode="after")
    def status_matches_relations(self) -> Self:
        """Route every conflict to confirmation and nothing to auto-confirmed."""
        expected = (
            WebLookupStatus.NEEDS_CONFIRMATION
            if any(hit.relation is WebLookupRelation.CONFLICTS for hit in self.hits)
            else WebLookupStatus.SUPPORTING_ONLY
        )
        if self.status is not expected:
            message = "web lookup status does not match result relations"
            raise ValueError(message)
        return self


class WebLookupArtifact(FrozenModel):
    """Append-only run sidecar for typed web lookup provenance."""

    schema_version: str = "web-lookup-provenance.v1"
    run_id: RunId
    records: tuple[WebLookupRecord, ...]

    @model_validator(mode="after")
    def request_ids_are_unique(self) -> Self:
        """Reject rewritten or repeated lookup identities."""
        request_ids = tuple(record.request.request_id for record in self.records)
        if len(request_ids) != len(set(request_ids)):
            message = "web lookup request IDs must be unique"
            raise ValueError(message)
        return self


class WebLookupProvider(Protocol):
    """Injected search boundary; the workflow owns no network service."""

    def search(self, request: WebLookupRequest) -> tuple[WebLookupHit, ...]:
        """Return candidates without promoting any candidate to truth."""
        ...


def lookup_external_fact(
    request: WebLookupRequest,
    provider: WebLookupProvider,
    *,
    looked_up_at: str,
) -> WebLookupRecord:
    """Run one lookup only after a typed uncertainty request exists."""
    hits = provider.search(request)
    if not hits:
        message = "web lookup provider returned no provenance candidates"
        raise ValueError(message)
    status = (
        WebLookupStatus.NEEDS_CONFIRMATION
        if any(hit.relation is WebLookupRelation.CONFLICTS for hit in hits)
        else WebLookupStatus.SUPPORTING_ONLY
    )
    return WebLookupRecord(
        request=request,
        looked_up_at=looked_up_at,
        hits=hits,
        status=status,
    )


def unresolved_web_conflict(record: WebLookupRecord) -> UnresolvedItem | None:
    """Route a web/video conflict to confirmation without replacement text."""
    if record.status is WebLookupStatus.SUPPORTING_ONLY:
        return None
    request = record.request
    return UnresolvedItem(
        at_ms=request.at_ms,
        field="external_fact",
        reason=request.uncertainty_reason,
        reason_code=UnresolvedReasonCode.EXTERNAL_FACT_CONFLICT,
        target_cue_index=request.target_cue_index,
        source_evidence_ids=(
            *request.source_evidence_ids,
            f"web:{request.request_id}",
        ),
    )


def append_web_lookup_record(
    path: Path,
    run_id: str,
    record: WebLookupRecord,
) -> WebLookupArtifact:
    """Append one immutable lookup without rewriting prior provenance."""
    if path.exists():
        current = WebLookupArtifact.model_validate_json(path.read_bytes())
        if current.run_id != run_id:
            message = "web lookup sidecar run ID mismatch"
            raise ValueError(message)
    else:
        current = WebLookupArtifact(run_id=RunId(run_id), records=())
    existing = next(
        (
            item
            for item in current.records
            if item.request.request_id == record.request.request_id
        ),
        None,
    )
    if existing is not None:
        if existing == record:
            return current
        message = "web lookup request ID already exists with different content"
        raise ValueError(message)
    updated = WebLookupArtifact(
        run_id=current.run_id,
        records=(*current.records, record),
    )
    write_model_atomic(path, updated)
    return updated


__all__ = [
    "ExternalFactKind",
    "WebLookupArtifact",
    "WebLookupHit",
    "WebLookupProvider",
    "WebLookupRecord",
    "WebLookupRelation",
    "WebLookupRequest",
    "WebLookupStatus",
    "append_web_lookup_record",
    "lookup_external_fact",
    "unresolved_web_conflict",
]
