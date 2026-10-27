# Copyright 2026 Transcription Module contributors

"""Production translation attempt, preflight, selection, and lifecycle workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from translation_module.canonical._attempt_store import TranslationAttemptStore
from translation_module.canonical._attempt_store_io import locked_attempt_namespace
from translation_module.canonical._authority_namespace import open_authority_namespace
from translation_module.canonical._selected_store import (
    persist_candidate_in_namespace,
    preflight_path,
)
from translation_module.canonical.finalization_authority import (
    finalize_translation_core,
)
from translation_module.canonical.preflight import (
    PreflightedSelection,
    build_preflight_report,
    select_preflighted_attempt,
    write_preflight_report,
)
from translation_module.canonical.review_authority import (
    ReviewAssemblyInputs,
    assemble_review_artifact_core,
)
from translation_module.canonical.validation import validate_canonical_save
from translation_module.common import FrozenModel, Sha256Digest
from translation_module.preparation import MAX_CONTEXT_CUES_PER_SIDE

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical._attempt_models import (
        TranslationAttempt,
        TranslationSelectionReceipt,
    )
    from translation_module.canonical._preflight_models import (
        CandidatePreflightContext,
        PreflightCheckerEvidence,
    )
    from translation_module.canonical.review import TranslationReviewArtifact
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import (
        TranslationDocumentV2,
        TranslationFinalization,
    )
    from translation_module.canonical.validation import TranslationValidationContext
    from translation_module.preparation import TranslationBatchPlan


class TranslationReviewHarnessError(ValueError):
    """Translation and review chunk ownership cannot be shared safely."""


class SharedReviewChunkScope(FrozenModel):
    """One reviewer scope derived from one translator-owned chunk."""

    chunk_id: str = Field(min_length=1)
    source_cue_ids: tuple[str, ...] = Field(min_length=1)
    target_cue_ids: tuple[str, ...] = Field(min_length=1)
    context_before_source_cue_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_CONTEXT_CUES_PER_SIDE,
    )
    context_after_source_cue_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_CONTEXT_CUES_PER_SIDE,
    )


class TranslationReviewHarness(FrozenModel):
    """Candidate-bound reviewer scopes sharing one translation plan."""

    schema_version: Literal["translation-review-harness.v1"] = (
        "translation-review-harness.v1"
    )
    plan_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    source_revision_id: str = Field(min_length=1)
    source_content_digest: Sha256Digest
    translation_document_id: str = Field(min_length=1)
    translation_revision_id: str = Field(min_length=1)
    candidate_content_digest: Sha256Digest
    chunks: tuple[SharedReviewChunkScope, ...] = Field(min_length=1)


def _validate_plan(
    source: SourceTranscriptV2,
    plan: TranslationBatchPlan,
) -> tuple[tuple[str, ...], dict[str, str]]:
    source_ids = tuple(cue.cue_id for cue in source.cues)
    if plan.source_digest != source.content_digest:
        message = "translation chunk plan source digest mismatch"
        raise TranslationReviewHarnessError(message)
    chunk_ids = tuple(chunk.chunk_id for chunk in plan.chunks)
    if len(chunk_ids) != len(set(chunk_ids)):
        message = "translation chunk IDs must be unique"
        raise TranslationReviewHarnessError(message)
    owned_ids = tuple(
        str(cue_id) for chunk in plan.chunks for cue_id in chunk.owned_source_cue_ids
    )
    if owned_ids != source_ids:
        message = "translation chunks must own every source cue once in source order"
        raise TranslationReviewHarnessError(message)
    source_positions = {cue_id: index for index, cue_id in enumerate(source_ids)}
    owner_by_source: dict[str, str] = {}
    for chunk in plan.chunks:
        owned = tuple(str(cue_id) for cue_id in chunk.owned_source_cue_ids)
        start = source_positions[owned[0]]
        end = start + len(owned)
        before = tuple(str(cue_id) for cue_id in chunk.context_before_source_cue_ids)
        after = tuple(str(cue_id) for cue_id in chunk.context_after_source_cue_ids)
        if before != source_ids[start - len(before) : start]:
            message = f"translation chunk {chunk.chunk_id} has invalid prior context"
            raise TranslationReviewHarnessError(message)
        if after != source_ids[end : end + len(after)]:
            message = f"translation chunk {chunk.chunk_id} has invalid next context"
            raise TranslationReviewHarnessError(message)
        owner_by_source.update(dict.fromkeys(owned, chunk.chunk_id))
    return source_ids, owner_by_source


def build_translation_review_harness(
    source: SourceTranscriptV2,
    translation: TranslationDocumentV2,
    plan: TranslationBatchPlan,
) -> TranslationReviewHarness:
    """Bind each reviewer to the exact source range used by its translator."""
    validate_canonical_save(source)
    validate_canonical_save(translation)
    if (
        translation.source_ref.document_id != source.document_id
        or translation.source_ref.revision_id != source.revision_id
        or translation.source_ref.content_digest != source.content_digest
    ):
        message = "translation source binding does not match harness source"
        raise TranslationReviewHarnessError(message)
    source_ids, owner_by_source = _validate_plan(source, plan)
    targets_by_chunk: dict[str, list[str]] = {
        chunk.chunk_id: [] for chunk in plan.chunks
    }
    covered_source_ids: set[str] = set()
    for target in translation.cues:
        unknown = set(target.source_cue_ids) - set(owner_by_source)
        if unknown:
            message = f"target references source outside translation plan: {unknown}"
            raise TranslationReviewHarnessError(message)
        owners = {owner_by_source[source_id] for source_id in target.source_cue_ids}
        if len(owners) != 1:
            message = f"target {target.cue_id} spans translation chunks"
            raise TranslationReviewHarnessError(message)
        owner = owners.pop()
        targets_by_chunk[owner].append(target.cue_id)
        covered_source_ids.update(target.source_cue_ids)
    if covered_source_ids != set(source_ids):
        message = "translation target lineage does not cover every planned source cue"
        raise TranslationReviewHarnessError(message)
    chunks = tuple(
        SharedReviewChunkScope(
            chunk_id=chunk.chunk_id,
            source_cue_ids=tuple(str(item) for item in chunk.owned_source_cue_ids),
            target_cue_ids=tuple(targets_by_chunk[chunk.chunk_id]),
            context_before_source_cue_ids=tuple(
                str(item) for item in chunk.context_before_source_cue_ids
            ),
            context_after_source_cue_ids=tuple(
                str(item) for item in chunk.context_after_source_cue_ids
            ),
        )
        for chunk in plan.chunks
    )
    return TranslationReviewHarness(
        plan_id=plan.plan_id,
        source_document_id=source.document_id,
        source_revision_id=source.revision_id,
        source_content_digest=source.content_digest,
        translation_document_id=translation.document_id,
        translation_revision_id=translation.revision_id,
        candidate_content_digest=translation.content_digest,
        chunks=chunks,
    )


@dataclass(frozen=True, slots=True)
class CanonicalTranslationWorkflow:
    """Production boundary that records and selects canonical candidates."""

    root: Path

    @property
    def attempt_store(self) -> TranslationAttemptStore:
        """Return the revision-shared append-only attempt store."""
        return TranslationAttemptStore(self.root / "attempts")

    def record_attempt(
        self,
        attempt: TranslationAttempt,
        candidate: TranslationDocumentV2,
    ) -> TranslationAttempt:
        """Persist a candidate and append its execution result."""
        attempt = self.attempt_store.validate_append(attempt, candidate)
        with (
            open_authority_namespace(self.root, create=True) as namespace,
            locked_attempt_namespace(namespace),
        ):
            attempt = self.attempt_store.validate_append(attempt, candidate)
            _ = persist_candidate_in_namespace(namespace, candidate)
            return self.attempt_store.append(attempt, candidate)

    def record_failed_attempt(self, attempt: TranslationAttempt) -> TranslationAttempt:
        """Append a failed intended execution that produced no candidate."""
        attempt = self.attempt_store.validate_failed_append(attempt)
        with (
            open_authority_namespace(self.root, create=True) as namespace,
            locked_attempt_namespace(namespace),
        ):
            attempt = self.attempt_store.validate_failed_append(attempt)
            return self.attempt_store.append_failed(attempt)

    def preflight_path(self, candidate: TranslationDocumentV2) -> Path:
        """Derive the sole report path for one translation revision."""
        return preflight_path(self.root, candidate.document_id, candidate.revision_id)

    def authorize(self, selection: PreflightedSelection) -> None:
        """Persist current preflight and immutable selection."""
        report_path = self.preflight_path(selection.context.candidate)
        _ = write_preflight_report(report_path, selection.report)
        _ = select_preflighted_attempt(self.attempt_store, selection)

    def preflight_and_authorize(
        self,
        context: CandidatePreflightContext,
        evidence: PreflightCheckerEvidence,
        receipt: TranslationSelectionReceipt,
    ) -> None:
        """Build, persist, and enforce deterministic authority for one revision."""
        report = build_preflight_report(context, evidence)
        self.authorize(PreflightedSelection(receipt, context, report))


def assemble_selected_review(
    root: Path,
    document_id: str,
    revision_id: str,
    inputs: ReviewAssemblyInputs,
) -> TranslationReviewArtifact:
    """Assemble review from independently reloaded persisted authority."""
    return assemble_review_artifact_core(root, document_id, revision_id, inputs)


def finalize_selected_translation(
    root: Path,
    document_id: str,
    revision_id: str,
    finalization: TranslationFinalization,
    environment: TranslationValidationContext,
) -> TranslationDocumentV2:
    """Finalize from independently reloaded persisted authority."""
    return finalize_translation_core(
        root, document_id, revision_id, finalization, environment
    )


__all__ = [
    "CanonicalTranslationWorkflow",
    "ReviewAssemblyInputs",
    "SharedReviewChunkScope",
    "TranslationReviewHarness",
    "TranslationReviewHarnessError",
    "assemble_selected_review",
    "build_translation_review_harness",
    "finalize_selected_translation",
]
