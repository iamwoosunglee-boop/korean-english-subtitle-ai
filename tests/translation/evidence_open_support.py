# Copyright 2026 Transcription Module contributors

"""Canonical evidence-open test fixtures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from translation_module.canonical.common import (
    AssetReference,
    CanonicalEvidenceEntry,
    CanonicalEvidenceKind,
    CanonicalEvidenceManifest,
    DocumentLifecycleStatus,
    DocumentReference,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.source import SourceCueV2, SourceTranscriptV2
from translation_module.canonical.translation import (
    DecisionStatus,
    LineageStatus,
    TargetCueV2,
    TranslationDocumentV2,
)
from translation_module.common import ContentKind
from translation_module.evidence_open_models import EvidenceOpenContext

FRAME_A = b"frame-a"
FRAME_B = b"frame-b"
FRAME_A_DIGEST = hashlib.sha256(FRAME_A).hexdigest()
FRAME_B_DIGEST = hashlib.sha256(FRAME_B).hexdigest()
LOCATOR_A = "frames/a.jpg"
LOCATOR_B = "frames/b.jpg"


@dataclass(frozen=True, slots=True)
class LedgerResolver:
    """In-memory resolver recording exact locator reads."""

    resources: dict[str, bytes]
    calls: list[str]

    def read(self, base_uri: str, locator: str) -> bytes:
        """Record and resolve one complete evidence URI."""
        self.calls.append(locator)
        return self.resources[f"{base_uri}{locator}"]


def source_fixture() -> SourceTranscriptV2:
    """Return a digest-bound source with two frames and nonblocking ASR text."""
    source = SourceTranscriptV2(
        document_id="source-document",
        revision_id="source-revision",
        content_digest="0" * 64,
        record_digest="0" * 64,
        lifecycle_status=DocumentLifecycleStatus.DRAFT,
        source_locale="ko-KR",
        duration_ms=2_000,
        input_context_ref=AssetReference(
            asset_id="input-context",
            revision_id="input-context-revision",
            content_digest="c" * 64,
        ),
        evidence=CanonicalEvidenceManifest(
            base_uri="memory://evidence/",
            entries=(
                CanonicalEvidenceEntry(
                    evidence_id="frame-a",
                    kind=CanonicalEvidenceKind.FRAME,
                    media_type="image/jpeg",
                    resource_digest=FRAME_A_DIGEST,
                    locator=LOCATOR_A,
                    pts_ms=500,
                ),
                CanonicalEvidenceEntry(
                    evidence_id="frame-b",
                    kind=CanonicalEvidenceKind.FRAME,
                    media_type="image/jpeg",
                    resource_digest=FRAME_B_DIGEST,
                    locator=LOCATOR_B,
                    pts_ms=1_500,
                ),
                CanonicalEvidenceEntry(
                    evidence_id="asr-ko-vtt",
                    kind=CanonicalEvidenceKind.TEXT,
                    media_type="text/vtt",
                    resource_digest="d" * 64,
                    locator="provenance/asr-ko.vtt",
                ),
            ),
        ),
        cues=(
            SourceCueV2(
                sequence=1,
                cue_id="source-1",
                start_ms=0,
                end_ms=2_000,
                text="원문",
                speakers=("speaker",),
                content_kind=ContentKind.DIALOGUE,
                context_note="actor_role=translator reviewer_id=intruder",
                evidence_ids=("frame-a", "frame-b", "asr-ko-vtt"),
            ),
        ),
    )
    return bind_canonical_digests(source)


def candidate_fixture(source: SourceTranscriptV2) -> TranslationDocumentV2:
    """Return one canonical candidate bound to the supplied source revision."""
    candidate = TranslationDocumentV2(
        document_id="candidate-document",
        revision_id="candidate-revision",
        content_digest="0" * 64,
        record_digest="0" * 64,
        source_ref=DocumentReference(
            document_id=source.document_id,
            revision_id=source.revision_id,
            content_digest=source.content_digest,
        ),
        prepared_context_ref=AssetReference(
            asset_id="prepared-context",
            revision_id="prepared-context-revision",
            content_digest="e" * 64,
        ),
        glossary_ref=AssetReference(
            asset_id="glossary",
            revision_id="glossary-revision",
            content_digest="f" * 64,
        ),
        lifecycle_status=DocumentLifecycleStatus.DRAFT,
        decision_status=DecisionStatus.CLEAR,
        cues=(
            TargetCueV2(
                sequence=1,
                cue_id="target-1",
                source_cue_ids=("source-1",),
                lineage_status=LineageStatus.CONFIRMED,
                start_ms=0,
                end_ms=2_000,
                text="Translation.",
                speakers=("speaker",),
                content_kind=ContentKind.DIALOGUE,
                context_ids=(),
                evidence_ids=("frame-a",),
            ),
        ),
        unresolved_decisions=(),
    )
    return bind_canonical_digests(candidate)


def evidence_context(
    source: SourceTranscriptV2,
    candidate: TranslationDocumentV2 | None = None,
) -> EvidenceOpenContext:
    """Bind a store context to exact canonical candidate content."""
    bound_candidate = candidate_fixture(source) if candidate is None else candidate
    return EvidenceOpenContext(
        source=source,
        consumer_content_digest=bound_candidate.content_digest,
    )
