# Copyright 2026 Transcription Module contributors

"""Legacy SourceSnapshot v1 evidence preparation compatibility."""

import hashlib
from collections.abc import Collection
from typing import Protocol

from translation_module.preparation_errors import PreparationError
from translation_module.source import EvidenceEntry, EvidenceKind, SourceSnapshot


class EvidenceResolver(Protocol):
    """Read one evidence resource without exposing run storage."""

    def read(self, base_uri: str, locator: str) -> bytes:
        """Return exact evidence resource bytes."""
        ...


def preflight_evidence(
    source: SourceSnapshot,
    resolver: EvidenceResolver,
    evidence_ids: Collection[str] | None = None,
) -> tuple[EvidenceEntry, ...]:
    """Resolve and verify evidence for the legacy SourceSnapshot v1 route."""
    referenced_ids: set[str] = set(source.context_evidence_ids)
    referenced_ids.update(
        (evidence_id for cue in source.cues for evidence_id in cue.evidence_ids)
        if evidence_ids is None
        else evidence_ids
    )
    known_ids = {entry.evidence_id for entry in source.evidence.entries}
    if unknown_ids := referenced_ids - known_ids:
        msg = f"unknown evidence requested: {unknown_ids}"
        raise PreparationError(msg)
    entries = tuple(
        entry
        for entry in source.evidence.entries
        if entry.evidence_id in referenced_ids
    )
    for entry in entries:
        if entry.kind is EvidenceKind.FRAME and (
            entry.pts_ms is None or entry.pts_ms >= source.duration_ms
        ):
            msg = f"evidence PTS is invalid: {entry.evidence_id}"
            raise PreparationError(msg)
        try:
            resource = resolver.read(source.evidence.base_uri, entry.locator)
        except (KeyError, OSError) as error:
            msg = f"evidence is unavailable: {entry.evidence_id}"
            raise PreparationError(msg) from error
        if hashlib.sha256(resource).hexdigest() != entry.resource_digest:
            msg = f"evidence digest mismatch: {entry.evidence_id}"
            raise PreparationError(msg)
    return entries
