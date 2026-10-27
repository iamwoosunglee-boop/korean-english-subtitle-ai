# Copyright 2026 Transcription Module contributors

"""Assemble immutable review artifacts from scoped chunk and global audits."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    AssetLifecycleStatus,
    CanonicalId,
    CanonicalModel,
    DocumentLifecycleStatus,
    UtcTimestamp,
    validate_unique_ids,
)
from translation_module.canonical.digest import (
    bind_canonical_digests,
    canonical_sha256,
)
from translation_module.canonical.review import (
    CanonicalReviewFinding,
    CanonicalReviewKind,
    ReviewArtifactResult,
    ReviewBinding,
    ReviewCoverage,
    ReviewProfile,
    ReviewProvenance,
    TranslationReviewArtifact,
)
from translation_module.canonical.review_authority import (
    ReviewAssemblyInputs,
    assemble_review_artifact_core,
)
from translation_module.canonical.validation import validate_canonical_save
from translation_module.common import Sha256Digest  # noqa: TC001 - Pydantic runtime

if TYPE_CHECKING:
    from translation_module.canonical.context import ContentContextAsset
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import (
        TargetCueV2,
        TranslationDocumentV2,
    )


def _is_none(value: object) -> bool:
    return value is None


class ReviewAssemblyError(ValueError):
    """Raised when scoped review evidence cannot bind to current artifacts."""


class ReviewDependencyBinding(CanonicalModel):
    """Stable dependencies shared by chunk reports and the global audit."""

    translation_document_id: CanonicalId
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: Sha256Digest
    prepared_context_asset_id: CanonicalId
    prepared_context_revision_id: CanonicalId
    prepared_context_content_digest: Sha256Digest
    glossary_asset_id: CanonicalId
    glossary_revision_id: CanonicalId
    glossary_content_digest: Sha256Digest
    review_profile: ReviewProfile


class ReviewChunkReport(CanonicalModel):
    """One reviewer result bound to the exact target cues it inspected."""

    chunk_id: CanonicalId
    binding: ReviewDependencyBinding
    target_scope_digest: Sha256Digest
    source_cue_ids: tuple[CanonicalId, ...]
    target_cue_ids: tuple[CanonicalId, ...] = Field(min_length=1)
    findings: tuple[CanonicalReviewFinding, ...]
    reviewer_execution_id: CanonicalId

    @model_validator(mode="after")
    def scoped_ids_are_unique(self) -> Self:
        """Reject ambiguous repeated scope and finding identities."""
        validate_unique_ids(self.source_cue_ids, "chunk review source cue")
        validate_unique_ids(self.target_cue_ids, "chunk review target cue")
        validate_unique_ids(
            tuple(finding.finding_id for finding in self.findings),
            "chunk review finding",
        )
        return self


class ReviewGlobalAudit(CanonicalModel):
    """Candidate-wide continuity and terminology audit result."""

    audit_id: CanonicalId
    binding: ReviewDependencyBinding
    candidate_content_digest: Sha256Digest
    continuity_audit_completed: bool
    terminology_audit_completed: bool
    target_boundary_pairs: tuple[tuple[CanonicalId, CanonicalId], ...]
    glossary_entry_ids: tuple[CanonicalId, ...]
    findings: tuple[CanonicalReviewFinding, ...]
    reviewer_execution_id: CanonicalId

    @model_validator(mode="after")
    def audit_scope_is_consistent(self) -> Self:
        """Reject duplicate or contradictory global audit declarations."""
        validate_unique_ids(self.glossary_entry_ids, "global audit glossary entry")
        validate_unique_ids(
            tuple(finding.finding_id for finding in self.findings),
            "global audit finding",
        )
        if len(self.target_boundary_pairs) != len(set(self.target_boundary_pairs)):
            message = "global audit target boundary pairs must be unique"
            raise ValueError(message)
        if any(left == right for left, right in self.target_boundary_pairs):
            message = "global audit target boundary pair must contain distinct cues"
            raise ValueError(message)
        if not self.continuity_audit_completed and self.target_boundary_pairs:
            message = "incomplete continuity audit cannot claim boundary coverage"
            raise ValueError(message)
        if not self.terminology_audit_completed and self.glossary_entry_ids:
            message = "incomplete terminology audit cannot claim glossary coverage"
            raise ValueError(message)
        return self


class ReviewAssemblyManifest(CanonicalModel):
    """Typed inputs used to derive one translation-review.v1 artifact."""

    schema_version: Literal["translation-review-assembly.v1"] = (
        "translation-review-assembly.v1"
    )
    review_id: CanonicalId
    supersedes_review_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    review_kind: CanonicalReviewKind
    review_profile: ReviewProfile
    completed_at: UtcTimestamp
    model: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    chunk_reports: tuple[ReviewChunkReport, ...] = Field(min_length=1)
    global_audit: ReviewGlobalAudit

    @model_validator(mode="after")
    def report_ids_are_unique(self) -> Self:
        """Require one stable identity per scoped reviewer report."""
        validate_unique_ids(
            tuple(report.chunk_id for report in self.chunk_reports),
            "chunk review",
        )
        if self.global_audit.audit_id in {
            report.chunk_id for report in self.chunk_reports
        }:
            message = "global audit ID must differ from chunk review IDs"
            raise ValueError(message)
        return self


def review_dependency_binding(
    translation: TranslationDocumentV2,
    review_profile: ReviewProfile,
) -> ReviewDependencyBinding:
    """Return the dependency identity reusable across unchanged target chunks."""
    return ReviewDependencyBinding(
        translation_document_id=translation.document_id,
        source_document_id=translation.source_ref.document_id,
        source_revision_id=translation.source_ref.revision_id,
        source_content_digest=translation.source_ref.content_digest,
        prepared_context_asset_id=translation.prepared_context_ref.asset_id,
        prepared_context_revision_id=translation.prepared_context_ref.revision_id,
        prepared_context_content_digest=translation.prepared_context_ref.content_digest,
        glossary_asset_id=translation.glossary_ref.asset_id,
        glossary_revision_id=translation.glossary_ref.revision_id,
        glossary_content_digest=translation.glossary_ref.content_digest,
        review_profile=review_profile,
    )


def _target_cues(
    translation: TranslationDocumentV2,
    target_cue_ids: tuple[str, ...],
) -> tuple[TargetCueV2, ...]:
    if not target_cue_ids:
        message = "review chunk requires target cue IDs"
        raise ReviewAssemblyError(message)
    if len(target_cue_ids) != len(set(target_cue_ids)):
        message = "review chunk repeats target cue IDs"
        raise ReviewAssemblyError(message)
    by_id = {cue.cue_id: cue for cue in translation.cues}
    unknown = tuple(target_id for target_id in target_cue_ids if target_id not in by_id)
    if unknown:
        message = f"review chunk references unknown target cues: {unknown}"
        raise ReviewAssemblyError(message)
    positions = tuple(
        index
        for index, cue in enumerate(translation.cues)
        if cue.cue_id in target_cue_ids
    )
    expected_positions = tuple(range(positions[0], positions[0] + len(positions)))
    ordered_ids = tuple(translation.cues[index].cue_id for index in positions)
    if positions != expected_positions or ordered_ids != target_cue_ids:
        message = "review chunk target cues must form one ordered contiguous range"
        raise ReviewAssemblyError(message)
    return tuple(by_id[target_id] for target_id in target_cue_ids)


def review_chunk_digest(
    translation: TranslationDocumentV2,
    target_cue_ids: tuple[str, ...],
) -> str:
    """Hash only one owned target range so unchanged chunk reviews remain reusable."""
    cues = _target_cues(translation, target_cue_ids)
    target_set = set(target_cue_ids)
    decisions = tuple(
        decision
        for decision in translation.unresolved_decisions
        if target_set.intersection(decision.target_cue_ids)
    )
    return canonical_sha256(
        {
            "translation_document_id": translation.document_id,
            "source_locale": translation.source_locale,
            "target_locale": translation.target_locale,
            "target_cues": [
                cue.model_dump(mode="json", exclude_none=True) for cue in cues
            ],
            "unresolved_decisions": [
                decision.model_dump(mode="json", exclude_none=True)
                for decision in decisions
            ],
        }
    )


def _artifact_binding(
    translation: TranslationDocumentV2,
    profile: ReviewProfile,
) -> ReviewBinding:
    return ReviewBinding(
        translation_document_id=translation.document_id,
        translation_revision_id=translation.revision_id,
        candidate_content_digest=translation.content_digest,
        source_document_id=translation.source_ref.document_id,
        source_revision_id=translation.source_ref.revision_id,
        source_content_digest=translation.source_ref.content_digest,
        prepared_context_asset_id=translation.prepared_context_ref.asset_id,
        prepared_context_revision_id=translation.prepared_context_ref.revision_id,
        prepared_context_content_digest=translation.prepared_context_ref.content_digest,
        glossary_asset_id=translation.glossary_ref.asset_id,
        glossary_revision_id=translation.glossary_ref.revision_id,
        glossary_content_digest=translation.glossary_ref.content_digest,
        review_profile=profile,
    )


def _dependencies_match(
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2,
    context: ContentContextAsset,
    glossary: TranslationGlossaryAsset,
) -> bool:
    source_ref = translation.source_ref
    context_ref = translation.prepared_context_ref
    glossary_ref = translation.glossary_ref
    return (
        source_ref.document_id == source.document_id
        and source_ref.revision_id == source.revision_id
        and source_ref.content_digest == source.content_digest
        and context_ref.asset_id == context.asset_id
        and context_ref.revision_id == context.revision_id
        and context_ref.content_digest == context.content_digest
        and glossary_ref.asset_id == glossary.asset_id
        and glossary_ref.revision_id == glossary.revision_id
        and glossary_ref.content_digest == glossary.content_digest
    )


def _ordered_subset[T](items: tuple[T, ...], selected: set[T]) -> tuple[T, ...]:
    return tuple(item for item in items if item in selected)


def _validate_finding_scope(
    finding: CanonicalReviewFinding,
    source_scope: set[str],
    target_scope: set[str],
    scope_name: str,
) -> None:
    if not set(finding.affected_source_cue_ids) <= source_scope:
        message = f"finding {finding.finding_id} exceeds {scope_name} source scope"
        raise ReviewAssemblyError(message)
    if not set(finding.affected_target_cue_ids) <= target_scope:
        message = f"finding {finding.finding_id} exceeds {scope_name} target scope"
        raise ReviewAssemblyError(message)


def _validate_chunk_report(
    report: ReviewChunkReport,
    translation: TranslationDocumentV2,
    known_sources: set[str],
    covered_targets: set[str],
    expected_binding: ReviewDependencyBinding,
) -> tuple[set[str], set[str]]:
    if report.binding != expected_binding:
        message = f"chunk {report.chunk_id} dependency binding is stale"
        raise ReviewAssemblyError(message)
    targets = _target_cues(translation, report.target_cue_ids)
    expected_digest = review_chunk_digest(translation, report.target_cue_ids)
    if report.target_scope_digest != expected_digest:
        message = f"chunk {report.chunk_id} target scope digest mismatch"
        raise ReviewAssemblyError(message)
    report_sources = set(report.source_cue_ids)
    if report_sources - known_sources:
        message = f"chunk {report.chunk_id} references unknown source cues"
        raise ReviewAssemblyError(message)
    report_targets = set(report.target_cue_ids)
    duplicate_targets = covered_targets.intersection(report_targets)
    if duplicate_targets:
        message = (
            f"target cues are owned by multiple review chunks: {duplicate_targets}"
        )
        raise ReviewAssemblyError(message)
    lineage = {source_id for target in targets for source_id in target.source_cue_ids}
    if not lineage <= report_sources:
        message = f"chunk {report.chunk_id} omits target source lineage"
        raise ReviewAssemblyError(message)
    for finding in report.findings:
        _validate_finding_scope(
            finding,
            report_sources,
            report_targets,
            f"chunk {report.chunk_id}",
        )
    return report_sources, report_targets


def _validate_global_audit(
    audit: ReviewGlobalAudit,
    translation: TranslationDocumentV2,
    known_scope: tuple[set[str], set[str], set[str]],
    expected_binding: ReviewDependencyBinding,
) -> tuple[set[tuple[str, str]], set[str]]:
    known_sources, known_targets, known_glossary = known_scope
    if audit.binding != expected_binding:
        message = "global audit dependency binding is stale"
        raise ReviewAssemblyError(message)
    if audit.candidate_content_digest != translation.content_digest:
        message = "global audit candidate binding is stale"
        raise ReviewAssemblyError(message)
    expected_boundaries = {
        (left.cue_id, right.cue_id)
        for left, right in zip(translation.cues, translation.cues[1:], strict=False)
    }
    boundaries: set[tuple[str, str]] = set(audit.target_boundary_pairs)
    unknown_boundaries = boundaries - expected_boundaries
    if unknown_boundaries:
        message = (
            f"global audit references unknown target boundaries: {unknown_boundaries}"
        )
        raise ReviewAssemblyError(message)
    glossary_ids: set[str] = set(audit.glossary_entry_ids)
    unknown_glossary = glossary_ids - known_glossary
    if unknown_glossary:
        message = (
            f"global audit references unknown glossary entries: {unknown_glossary}"
        )
        raise ReviewAssemblyError(message)
    for finding in audit.findings:
        _validate_finding_scope(finding, known_sources, known_targets, "global audit")
    return (
        boundaries if audit.continuity_audit_completed else set(),
        glossary_ids if audit.terminology_audit_completed else set(),
    )


def _validate_report_scopes(
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2,
    glossary: TranslationGlossaryAsset,
    manifest: ReviewAssemblyManifest,
    expected_binding: ReviewDependencyBinding,
) -> tuple[
    set[str],
    set[str],
    set[tuple[str, str]],
    set[str],
    tuple[CanonicalReviewFinding, ...],
    tuple[str, ...],
]:
    known_sources = {cue.cue_id for cue in source.cues}
    known_targets = {cue.cue_id for cue in translation.cues}
    known_glossary = {entry.entry_id for entry in glossary.entries}
    covered_sources: set[str] = set()
    covered_targets: set[str] = set()
    findings: list[CanonicalReviewFinding] = []
    execution_ids: list[str] = []
    for report in manifest.chunk_reports:
        report_sources, report_targets = _validate_chunk_report(
            report,
            translation,
            known_sources,
            covered_targets,
            expected_binding,
        )
        covered_sources.update(report_sources)
        covered_targets.update(report_targets)
        findings.extend(report.findings)
        execution_ids.append(report.reviewer_execution_id)
    boundaries, glossary_ids = _validate_global_audit(
        manifest.global_audit,
        translation,
        (known_sources, known_targets, known_glossary),
        expected_binding,
    )
    findings.extend(manifest.global_audit.findings)
    finding_ids = tuple(finding.finding_id for finding in findings)
    if len(finding_ids) != len(set(finding_ids)):
        message = "assembled review finding IDs must be unique"
        raise ReviewAssemblyError(message)
    execution_ids.append(manifest.global_audit.reviewer_execution_id)
    return (
        covered_sources,
        covered_targets,
        boundaries,
        glossary_ids,
        tuple(findings),
        tuple(dict.fromkeys(execution_ids)),
    )


def assemble_review_artifact(
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2,
    context: ContentContextAsset,
    glossary: TranslationGlossaryAsset,
    manifest: ReviewAssemblyManifest,
) -> TranslationReviewArtifact:
    """Derive one immutable review from exact chunk reports and global audits."""
    try:
        for artifact in (translation, source, context, glossary):
            validate_canonical_save(artifact)
    except ValueError as error:
        message = f"review assembly input is invalid: {error}"
        raise ReviewAssemblyError(message) from error
    if source.lifecycle_status is not DocumentLifecycleStatus.FINALIZED:
        message = "review assembly requires a finalized source"
        raise ReviewAssemblyError(message)
    if (
        context.lifecycle_status is not AssetLifecycleStatus.READY
        or glossary.lifecycle_status is not AssetLifecycleStatus.READY
    ):
        message = "review assembly requires ready context and glossary assets"
        raise ReviewAssemblyError(message)
    if not _dependencies_match(translation, source, context, glossary):
        message = "translation dependency binding does not match supplied artifacts"
        raise ReviewAssemblyError(message)

    expected_binding = review_dependency_binding(
        translation,
        manifest.review_profile,
    )
    (
        covered_sources,
        covered_targets,
        covered_boundaries,
        covered_glossary,
        findings,
        execution_ids,
    ) = _validate_report_scopes(
        translation,
        source,
        glossary,
        manifest,
        expected_binding,
    )
    source_order = tuple(cue.cue_id for cue in source.cues)
    target_order = tuple(cue.cue_id for cue in translation.cues)
    boundary_order = tuple(
        (left.cue_id, right.cue_id)
        for left, right in zip(translation.cues, translation.cues[1:], strict=False)
    )
    glossary_order = tuple(entry.entry_id for entry in glossary.entries)
    coverage = ReviewCoverage(
        source_cue_ids=_ordered_subset(source_order, covered_sources),
        target_cue_ids=_ordered_subset(target_order, covered_targets),
        target_boundary_pairs=_ordered_subset(boundary_order, covered_boundaries),
        glossary_entry_ids=_ordered_subset(glossary_order, covered_glossary),
    )
    complete = (
        covered_sources >= set(source_order)
        and covered_targets >= set(target_order)
        and covered_boundaries >= set(boundary_order)
        and covered_glossary >= set(glossary_order)
    )
    has_blocking = any(finding.blocking for finding in findings)
    provenance_payload: dict[str, object] = {
        "review_kind": manifest.review_kind,
        "reviewer_execution_ids": execution_ids,
        "completed_at": manifest.completed_at,
    }
    if manifest.model is not None:
        provenance_payload["model"] = manifest.model
    payload: dict[str, object] = {
        "review_id": manifest.review_id,
        "content_digest": "0" * 64,
        "record_digest": "0" * 64,
        "binding": _artifact_binding(translation, manifest.review_profile),
        "coverage": coverage,
        "result": (
            ReviewArtifactResult.PASSED
            if complete and not has_blocking
            else ReviewArtifactResult.FAILED
        ),
        "findings": findings,
        "provenance": ReviewProvenance.model_validate(provenance_payload),
    }
    if manifest.supersedes_review_id is not None:
        payload["supersedes_review_id"] = manifest.supersedes_review_id
    review = bind_canonical_digests(TranslationReviewArtifact.model_validate(payload))
    validate_canonical_save(review)
    return review


__all__ = [
    "ReviewAssemblyError",
    "ReviewAssemblyInputs",
    "ReviewAssemblyManifest",
    "ReviewChunkReport",
    "ReviewDependencyBinding",
    "ReviewGlobalAudit",
    "assemble_review_artifact",
    "assemble_review_artifact_core",
    "review_chunk_digest",
    "review_dependency_binding",
]
