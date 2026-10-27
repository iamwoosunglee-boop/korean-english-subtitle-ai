# Copyright 2026 Transcription Module contributors

"""Explicit evidence-open and canonical review assembly workflow support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.review import (
    CanonicalReviewKind,
    ReviewArtifactResult,
    ReviewProfile,
    TranslationReviewArtifact,
)
from translation_module.canonical.review_assembly import (
    ReviewAssemblyManifest,
    ReviewChunkReport,
    ReviewGlobalAudit,
    review_chunk_digest,
    review_dependency_binding,
)
from translation_module.canonical.runtime import (
    ReviewAssemblyInputs,
    assemble_selected_review,
)
from translation_module.evidence import FileEvidenceResolver
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenContext,
    EvidenceOpenError,
    EvidenceOpenRecordError,
    verify_evidence_open_digest,
)
from translation_module.evidence_open_preparation import (
    EvidenceOpenSelection,
    OpenedReviewInput,
    build_opened_review_request,
)
from translation_module.evidence_open_store import EvidenceOpenStore

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e.canonical_workflow_translation_support import (
        TranslationWorkflowResult,
    )


@dataclass(frozen=True, slots=True)
class LedgerResolver:
    """Fixed frame resolver retaining exact read order."""

    resources: dict[str, bytes]
    calls: list[str]

    def read(self, base_uri: str, locator: str) -> bytes:
        """Record and return one exact local fixture payload."""
        self.calls.append(locator)
        return self.resources[f"{base_uri}{locator}"]


@dataclass(frozen=True, slots=True)
class ReviewWorkflowResult:
    """Assembled review and explicit evidence-open observations."""

    review: TranslationReviewArtifact
    evidence_store: EvidenceOpenStore
    observations: dict[str, str | int | bool | list[str]]


def _selection(
    workflow: TranslationWorkflowResult,
    evidence_ids: tuple[str, ...],
) -> EvidenceOpenSelection:
    return EvidenceOpenSelection(
        source=workflow.source,
        evidence_ids=evidence_ids,
        actor_role=EvidenceActorRole.REVIEWER,
        consumer_id="review-open",
        consumer_content_digest=workflow.candidate.content_digest,
    )


def _assemble(workflow: TranslationWorkflowResult) -> TranslationReviewArtifact:
    profile = ReviewProfile(profile_id="editorial-review", version="1")
    candidate = workflow.candidate
    source = workflow.source
    target_ids = tuple(cue.cue_id for cue in candidate.cues)
    source_ids = tuple(cue.cue_id for cue in source.cues)
    binding = review_dependency_binding(candidate, profile)
    chunk = ReviewChunkReport(
        chunk_id="all-targets",
        binding=binding,
        target_scope_digest=review_chunk_digest(candidate, target_ids),
        source_cue_ids=source_ids,
        target_cue_ids=target_ids,
        findings=(),
        reviewer_execution_id="review-open",
    )
    audit = ReviewGlobalAudit(
        audit_id="global-audit",
        binding=binding,
        candidate_content_digest=candidate.content_digest,
        continuity_audit_completed=True,
        terminology_audit_completed=True,
        target_boundary_pairs=tuple(
            (left.cue_id, right.cue_id)
            for left, right in zip(candidate.cues, candidate.cues[1:], strict=False)
        ),
        glossary_entry_ids=tuple(entry.entry_id for entry in workflow.glossary.entries),
        findings=(),
        reviewer_execution_id="review-global",
    )
    return assemble_selected_review(
        workflow.authority_root,
        workflow.candidate.document_id,
        workflow.candidate.revision_id,
        ReviewAssemblyInputs(
            source,
            workflow.context,
            workflow.glossary,
            ReviewAssemblyManifest(
            review_id="review-artifact",
            review_kind=CanonicalReviewKind.INDEPENDENT_AI,
            review_profile=profile,
            completed_at="2026-08-18T12:00:00Z",
            model="fixed-reviewer",
            chunk_reports=(chunk,),
                global_audit=audit,
            ),
        ),
    )


def run_review_workflow(
    workflow: TranslationWorkflowResult,
    root: Path,
) -> ReviewWorkflowResult:
    """Open only A, reject unopened/corrupt B, then assemble review."""
    source = workflow.source
    first_entry = next(
        entry for entry in source.evidence.entries if entry.evidence_id == "frame-a"
    )
    assert (
        FileEvidenceResolver(workflow.evidence_root).read(
            source.evidence.base_uri,
            first_entry.locator,
        )
        == b"frame-a"
    )
    resources = {
        f"{source.evidence.base_uri}frames/frame-a.jpg": b"frame-a",
        f"{source.evidence.base_uri}frames/frame-b.jpg": b"corrupt-frame-b",
    }
    resolver = LedgerResolver(resources, [])
    store = EvidenceOpenStore(
        root / "evidence-open",
        EvidenceOpenContext(
            source=source,
            consumer_content_digest=workflow.candidate.content_digest,
        ),
        resolver,
    )
    record = store.open_frame(
        "frame-a",
        EvidenceActorRole.REVIEWER,
        "review-open",
    )
    verify_evidence_open_digest(record)
    request = build_opened_review_request(
        OpenedReviewInput(
            selection=_selection(workflow, ("frame-a",)),
            candidate=workflow.candidate,
        ),
        store,
    )
    assert "rationale" not in request.model_dump()
    calls_before_b = list(resolver.calls)
    with pytest.raises(EvidenceOpenRecordError, match="not explicitly opened"):
        _ = build_opened_review_request(
            OpenedReviewInput(
                selection=_selection(workflow, ("frame-a", "frame-b")),
                candidate=workflow.candidate,
            ),
            store,
        )
    assert resolver.calls == calls_before_b
    with pytest.raises(EvidenceOpenError, match="digest mismatch"):
        _ = store.open_frame(
            "frame-b",
            EvidenceActorRole.REVIEWER,
            "review-open",
        )
    review = _assemble(workflow)
    assert review.result is ReviewArtifactResult.PASSED
    verify_canonical_digests(review)
    return ReviewWorkflowResult(
        review,
        store,
        {
            "read_ledger": resolver.calls,
            "unopened_b_preserved_reads": True,
            "corrupt_b_typed": True,
            "reviewer_input_has_rationale": False,
            "opened_record_digest": record.record_digest,
        },
    )
