# Copyright 2026 Transcription Module contributors

"""Validation tiers and lifecycle transitions for canonical subtitle artifacts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath, PureWindowsPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from translation_module.canonical.common import (
    AssetLifecycleStatus,
    AssetReference,
    DocumentLifecycleStatus,
    DocumentReference,
)
from translation_module.canonical.context import (
    ContentContextAsset,
    ContextDecisionStatus,
    ContextStage,
)
from translation_module.canonical.digest import (
    CanonicalArtifact,
    CanonicalDigestMismatchError,
    bind_canonical_digests,
    canonical_content_digest,
    canonical_json_bytes,
    canonical_record_digest,
    verify_canonical_digests,
)
from translation_module.canonical.finalization_authority import (
    finalize_translation_core,
)
from translation_module.canonical.glossary import (
    GlossaryDecisionStatus,
    TranslationGlossaryAsset,
    glossary_conflicts,
)
from translation_module.canonical.review import (
    ReviewArtifactResult,
    ReviewBinding,
    ReviewProfile,
    TranslationReviewArtifact,
)
from translation_module.canonical.source import unresolved_speaker_cue_ids
from translation_module.canonical.translation import (
    LineageStatus,
    ReviewAcknowledgement,
    TranslationDocumentV2,
    TranslationFinalization,
)
from translation_module.common import ContentKind

if TYPE_CHECKING:
    from translation_module.canonical.source import (
        SourceCueV2,
        SourceFinalization,
        SourceTranscriptV2,
    )
    from translation_module.canonical.translation import TargetCueV2


class DiagnosticSeverity(StrEnum):
    """Whether a diagnostic is informational or blocks the requested transition."""

    WARNING = "warning"
    BLOCKING = "blocking"


class CanonicalDiagnosticCode(StrEnum):
    """Stable canonical workflow diagnostic identifiers."""

    CONTENT_DIGEST_MISMATCH = "content_digest_mismatch"
    RECORD_DIGEST_MISMATCH = "record_digest_mismatch"
    INVALID_LIFECYCLE = "invalid_lifecycle"
    TARGET_DISPLAY_ORDER = "target_display_order"
    REPEATED_SOURCE_LINEAGE = "repeated_source_lineage"
    LINEAGE_UNASSIGNED = "lineage_unassigned"
    LINEAGE_NEEDS_CONFIRMATION = "lineage_needs_confirmation"
    UNKNOWN_SOURCE_CUE = "unknown_source_cue"
    SOURCE_ORDER_VIOLATION = "source_order_violation"
    TIMING_ENVELOPE_VIOLATION = "timing_envelope_violation"
    TARGET_SPEAKER_NOT_IN_SOURCE = "target_speaker_not_in_source"
    TARGET_SPEAKER_ORDER_MISMATCH = "target_speaker_order_mismatch"
    SOURCE_COVERAGE_MISSING = "source_coverage_missing"
    UNKNOWN_CONTEXT_ID = "unknown_context_id"
    UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
    UNRESOLVED_TRANSLATION_DECISION = "unresolved_translation_decision"
    MISSING_SOURCE_REF = "missing_source_ref"
    SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
    SOURCE_NOT_FINALIZED = "source_not_finalized"
    SOURCE_SPEAKER_UNRESOLVED = "source_speaker_unresolved"
    MISSING_CONTEXT_REF = "missing_context_ref"
    CONTEXT_IDENTITY_MISMATCH = "context_identity_mismatch"
    CONTEXT_NOT_PREPARED = "context_not_prepared"
    CONTEXT_NOT_READY = "context_not_ready"
    CONTEXT_SOURCE_IDENTITY_MISMATCH = "context_source_identity_mismatch"
    MISSING_GLOSSARY_REF = "missing_glossary_ref"
    GLOSSARY_IDENTITY_MISMATCH = "glossary_identity_mismatch"
    GLOSSARY_NOT_READY = "glossary_not_ready"
    GLOSSARY_SOURCE_IDENTITY_MISMATCH = "glossary_source_identity_mismatch"
    BLOCKING_CONTEXT_FACT = "blocking_context_fact"
    BLOCKING_GLOSSARY_ENTRY = "blocking_glossary_entry"
    UNRESOLVED_CONTEXT_FACT = "unresolved_context_fact"
    UNRESOLVED_GLOSSARY_ENTRY = "unresolved_glossary_entry"
    CONTEXT_SCOPE_UNKNOWN_SOURCE = "context_scope_unknown_source"
    GLOSSARY_SCOPE_UNKNOWN_SOURCE = "glossary_scope_unknown_source"
    GLOSSARY_CONFLICT = "glossary_conflict"
    REVIEW_NOT_RUN = "review_not_run"
    REVIEW_FAILED = "review_failed"
    REVIEW_STALE = "review_stale"
    REVIEW_CONFLICT = "review_conflict"
    REVIEW_INVALID = "review_invalid"
    REVIEW_COVERAGE_INCOMPLETE = "review_coverage_incomplete"
    REVIEW_ACKNOWLEDGEMENT_MISMATCH = "review_acknowledgement_mismatch"
    UNDISPOSED_BLOCKING_FINDING = "undisposed_blocking_finding"
    NONPORTABLE_EVIDENCE_LOCATOR = "nonportable_evidence_locator"


class EffectiveReviewState(StrEnum):
    """Review state calculated from immutable artifacts and current binding."""

    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class CanonicalDiagnostic:
    """One deterministic workflow diagnostic."""

    code: CanonicalDiagnosticCode
    severity: DiagnosticSeverity
    message: str
    source_cue_ids: tuple[str, ...] = ()
    target_cue_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalValidationReport:
    """Deterministic diagnostics for one requested workflow operation."""

    diagnostics: tuple[CanonicalDiagnostic, ...]

    @property
    def has_blocking(self) -> bool:
        """Return whether the requested transition must be rejected."""
        return any(
            diagnostic.severity is DiagnosticSeverity.BLOCKING
            for diagnostic in self.diagnostics
        )


@dataclass(frozen=True, slots=True)
class ReviewEvaluation:
    """Effective review state and authoritative exact review, when unique."""

    state: EffectiveReviewState
    authoritative_review: TranslationReviewArtifact | None
    diagnostics: tuple[CanonicalDiagnostic, ...]

    @property
    def has_blocking(self) -> bool:
        """Return whether review metadata itself prevents finalization."""
        return any(
            diagnostic.severity is DiagnosticSeverity.BLOCKING
            for diagnostic in self.diagnostics
        )


@dataclass(frozen=True, slots=True)
class TranslationValidationContext:
    """Resolved dependencies and review policy used to evaluate one translation."""

    source: SourceTranscriptV2 | None
    context: ContentContextAsset | None
    glossary: TranslationGlossaryAsset | None
    reviews: tuple[TranslationReviewArtifact, ...]
    review_profile: ReviewProfile


class CanonicalFinalizationError(ValueError):
    """Raised when a canonical lifecycle transition has blocking diagnostics."""

    report: CanonicalValidationReport

    def __init__(self, report: CanonicalValidationReport) -> None:
        """Retain the complete deterministic report for the caller."""
        self.report = report
        message = "; ".join(
            diagnostic.message
            for diagnostic in report.diagnostics
            if diagnostic.severity is DiagnosticSeverity.BLOCKING
        )
        super().__init__(message or "canonical finalization failed")


def _diagnostic(
    code: CanonicalDiagnosticCode,
    severity: DiagnosticSeverity,
    message: str,
    *,
    source_cue_ids: tuple[str, ...] = (),
    target_cue_ids: tuple[str, ...] = (),
) -> CanonicalDiagnostic:
    return CanonicalDiagnostic(
        code=code,
        severity=severity,
        message=message,
        source_cue_ids=source_cue_ids,
        target_cue_ids=target_cue_ids,
    )


def _report(diagnostics: list[CanonicalDiagnostic]) -> CanonicalValidationReport:
    return CanonicalValidationReport(tuple(diagnostics))


def _document_reference_matches(
    reference: DocumentReference,
    artifact: SourceTranscriptV2,
) -> bool:
    return (
        reference.document_id == artifact.document_id
        and reference.revision_id == artifact.revision_id
        and reference.content_digest == artifact.content_digest
    )


def _asset_reference_matches(
    reference: AssetReference,
    artifact: ContentContextAsset | TranslationGlossaryAsset,
) -> bool:
    return (
        reference.asset_id == artifact.asset_id
        and reference.revision_id == artifact.revision_id
        and reference.content_digest == artifact.content_digest
    )


def _source_reference_matches(
    reference: DocumentReference,
    source: SourceTranscriptV2,
) -> bool:
    return _document_reference_matches(reference, source)


def _is_absolute_locator(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme == "file":
        return True
    if parsed.scheme:
        return False
    return PurePath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _evidence_locator_diagnostics(
    artifact: SourceTranscriptV2 | ContentContextAsset | TranslationGlossaryAsset,
) -> list[CanonicalDiagnostic]:
    values = [artifact.evidence.base_uri]
    values.extend(entry.locator for entry in artifact.evidence.entries)
    if not any(_is_absolute_locator(value) for value in values):
        return []
    return [
        _diagnostic(
            CanonicalDiagnosticCode.NONPORTABLE_EVIDENCE_LOCATOR,
            DiagnosticSeverity.WARNING,
            "evidence contains an absolute local locator",
        )
    ]


def _digest_diagnostics(
    artifact: CanonicalArtifact,
    severity: DiagnosticSeverity = DiagnosticSeverity.BLOCKING,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    expected_content = canonical_content_digest(artifact)
    if artifact.content_digest != expected_content:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTENT_DIGEST_MISMATCH,
                severity,
                "content digest mismatch",
            )
        )
    expected_record = canonical_record_digest(artifact)
    if artifact.record_digest != expected_record:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.RECORD_DIGEST_MISMATCH,
                severity,
                "record digest mismatch",
            )
        )
    return diagnostics


def _translation_save_diagnostics(
    translation: TranslationDocumentV2,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    ordering = tuple(
        (cue.start_ms, cue.end_ms, cue.sequence) for cue in translation.cues
    )
    if ordering != tuple(sorted(ordering)):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.TARGET_DISPLAY_ORDER,
                DiagnosticSeverity.BLOCKING,
                "target cues are not in display order",
            )
        )
    diagnostics.extend(
        _diagnostic(
            CanonicalDiagnosticCode.REPEATED_SOURCE_LINEAGE,
            DiagnosticSeverity.BLOCKING,
            f"target cue {cue.cue_id} repeats source lineage",
            source_cue_ids=cue.source_cue_ids,
            target_cue_ids=(cue.cue_id,),
        )
        for cue in translation.cues
        if len(cue.source_cue_ids) != len(set(cue.source_cue_ids))
    )
    return diagnostics


def validate_canonical_save(artifact: CanonicalArtifact) -> None:
    """Raise when a typed artifact cannot be safely persisted as canonical JSON."""
    payload = artifact.model_dump(mode="json", exclude_none=True)
    _ = type(artifact).model_validate(payload)
    _ = canonical_json_bytes(payload)
    verify_canonical_digests(artifact)
    diagnostics: list[CanonicalDiagnostic] = []
    if isinstance(artifact, TranslationDocumentV2):
        diagnostics.extend(_translation_save_diagnostics(artifact))
    if diagnostics:
        raise ValueError("; ".join(item.message for item in diagnostics))


def _expected_review_binding(
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


def _candidate_binding_matches(
    binding: ReviewBinding,
    expected: ReviewBinding,
) -> bool:
    """Compare canonical review scope independently of selected-attempt authority."""
    return (
        binding.model_copy(update={"selection_id": None, "attempt_id": None})
        == expected
    )


def evaluate_review_state(
    translation: TranslationDocumentV2,
    reviews: tuple[TranslationReviewArtifact, ...],
    review_profile: ReviewProfile,
    *,
    source: SourceTranscriptV2 | None = None,
    glossary: TranslationGlossaryAsset | None = None,
) -> ReviewEvaluation:
    """Calculate review state without mutating translation lifecycle state."""
    expected = _expected_review_binding(translation, review_profile)
    diagnostics: list[CanonicalDiagnostic] = []
    valid_reviews: list[TranslationReviewArtifact] = []
    related_candidates = tuple(
        review
        for review in reviews
        if review.binding.translation_document_id == translation.document_id
    )
    for review in related_candidates:
        try:
            validate_canonical_save(review)
        except (CanonicalDigestMismatchError, ValueError) as error:
            diagnostics.append(
                _diagnostic(
                    CanonicalDiagnosticCode.REVIEW_INVALID,
                    DiagnosticSeverity.BLOCKING,
                    f"review {review.review_id} is invalid: {error}",
                )
            )
        else:
            valid_reviews.append(review)

    related = valid_reviews
    superseded_ids = {
        review.supersedes_review_id
        for review in related
        if review.supersedes_review_id is not None
    }
    authoritative = [
        review for review in related if review.review_id not in superseded_ids
    ]
    exact = [
        review
        for review in authoritative
        if _candidate_binding_matches(review.binding, expected)
    ]
    if len(exact) > 1 or len({review.review_id for review in exact}) != len(exact):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.REVIEW_CONFLICT,
                DiagnosticSeverity.BLOCKING,
                "multiple authoritative reviews match the current binding",
            )
        )
        return ReviewEvaluation(
            state=EffectiveReviewState.STALE,
            authoritative_review=None,
            diagnostics=tuple(diagnostics),
        )
    if len(exact) == 1:
        review = exact[0]
        if not _review_coverage_complete(
            review,
            translation,
            source=source,
            glossary=glossary,
        ):
            diagnostics.append(
                _diagnostic(
                    CanonicalDiagnosticCode.REVIEW_COVERAGE_INCOMPLETE,
                    DiagnosticSeverity.WARNING,
                    "the authoritative review coverage is incomplete",
                )
            )
            return ReviewEvaluation(
                state=EffectiveReviewState.FAILED,
                authoritative_review=review,
                diagnostics=tuple(diagnostics),
            )
        if review.result is ReviewArtifactResult.PASSED:
            return ReviewEvaluation(
                state=EffectiveReviewState.PASSED,
                authoritative_review=review,
                diagnostics=tuple(diagnostics),
            )
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.REVIEW_FAILED,
                DiagnosticSeverity.WARNING,
                "the authoritative review failed",
            )
        )
        return ReviewEvaluation(
            state=EffectiveReviewState.FAILED,
            authoritative_review=review,
            diagnostics=tuple(diagnostics),
        )
    if related:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.REVIEW_STALE,
                DiagnosticSeverity.WARNING,
                "review artifacts exist but none match the current binding",
            )
        )
        return ReviewEvaluation(
            state=EffectiveReviewState.STALE,
            authoritative_review=None,
            diagnostics=tuple(diagnostics),
        )
    diagnostics.append(
        _diagnostic(
            CanonicalDiagnosticCode.REVIEW_NOT_RUN,
            DiagnosticSeverity.WARNING,
            "no review artifact exists for this translation",
        )
    )
    return ReviewEvaluation(
        state=EffectiveReviewState.NOT_RUN,
        authoritative_review=None,
        diagnostics=tuple(diagnostics),
    )


def _source_dependency_diagnostics(
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2 | None,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    if source is None:
        return [
            _diagnostic(
                CanonicalDiagnosticCode.MISSING_SOURCE_REF,
                severity,
                "the referenced source revision is unavailable",
            )
        ]
    if not _document_reference_matches(translation.source_ref, source):
        return [
            _diagnostic(
                CanonicalDiagnosticCode.SOURCE_IDENTITY_MISMATCH,
                severity,
                "the resolved source identity does not match source_ref",
            )
        ]
    if source.lifecycle_status is not DocumentLifecycleStatus.FINALIZED:
        return [
            _diagnostic(
                CanonicalDiagnosticCode.SOURCE_NOT_FINALIZED,
                severity,
                "translation requires a finalized source revision",
            )
        ]
    return _source_speaker_diagnostics(source, severity)


def _source_speaker_diagnostics(
    source: SourceTranscriptV2,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    return [
        _diagnostic(
            CanonicalDiagnosticCode.SOURCE_SPEAKER_UNRESOLVED,
            severity,
            f"source cue {cue_id} has an unresolved speaker",
            source_cue_ids=(cue_id,),
        )
        for cue_id in unresolved_speaker_cue_ids(source)
    ]


def _context_dependency_diagnostics(
    translation: TranslationDocumentV2,
    context: ContentContextAsset | None,
    source: SourceTranscriptV2 | None,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    if context is None:
        return [
            _diagnostic(
                CanonicalDiagnosticCode.MISSING_CONTEXT_REF,
                severity,
                "the referenced prepared context revision is unavailable",
            )
        ]
    if not _asset_reference_matches(translation.prepared_context_ref, context):
        return [
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_IDENTITY_MISMATCH,
                severity,
                "the resolved context identity does not match prepared_context_ref",
            )
        ]
    diagnostics: list[CanonicalDiagnostic] = []
    if context.stage is not ContextStage.PREPARED:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_NOT_PREPARED,
                severity,
                "translation requires a prepared context revision",
            )
        )
    if context.lifecycle_status is not AssetLifecycleStatus.READY:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_NOT_READY,
                severity,
                "prepared context is not ready",
            )
        )
    if (
        source is not None
        and context.prepared_from_source_ref is not None
        and not _source_reference_matches(context.prepared_from_source_ref, source)
    ):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_SOURCE_IDENTITY_MISMATCH,
                severity,
                "prepared context was built from a different source revision",
            )
        )
    return diagnostics


def _glossary_dependency_diagnostics(
    translation: TranslationDocumentV2,
    glossary: TranslationGlossaryAsset | None,
    source: SourceTranscriptV2 | None,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    if glossary is None:
        return [
            _diagnostic(
                CanonicalDiagnosticCode.MISSING_GLOSSARY_REF,
                severity,
                "the referenced glossary revision is unavailable",
            )
        ]
    if not _asset_reference_matches(translation.glossary_ref, glossary):
        return [
            _diagnostic(
                CanonicalDiagnosticCode.GLOSSARY_IDENTITY_MISMATCH,
                severity,
                "the resolved glossary identity does not match glossary_ref",
            )
        ]
    diagnostics: list[CanonicalDiagnostic] = []
    if glossary.lifecycle_status is not AssetLifecycleStatus.READY:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.GLOSSARY_NOT_READY,
                severity,
                "glossary is not ready",
            )
        )
    if source is not None and not _source_reference_matches(
        glossary.prepared_from_source_ref,
        source,
    ):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.GLOSSARY_SOURCE_IDENTITY_MISMATCH,
                severity,
                "glossary was built from a different source revision",
            )
        )
    return diagnostics


def _dependency_diagnostics(
    translation: TranslationDocumentV2,
    environment: TranslationValidationContext,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    for artifact in (
        environment.source,
        environment.context,
        environment.glossary,
    ):
        if artifact is not None:
            diagnostics.extend(_digest_diagnostics(artifact, severity))
    diagnostics.extend(
        _source_dependency_diagnostics(
            translation,
            environment.source,
            severity,
        )
    )
    diagnostics.extend(
        _context_dependency_diagnostics(
            translation,
            environment.context,
            environment.source,
            severity,
        )
    )
    diagnostics.extend(
        _glossary_dependency_diagnostics(
            translation,
            environment.glossary,
            environment.source,
            severity,
        )
    )
    return diagnostics


def _asset_decision_diagnostics(
    context: ContentContextAsset | None,
    glossary: TranslationGlossaryAsset | None,
    *,
    blocking: bool,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    severity = DiagnosticSeverity.BLOCKING if blocking else DiagnosticSeverity.WARNING
    if context is not None:
        for fact in context.facts:
            if fact.decision_status is not ContextDecisionStatus.UNRESOLVED:
                continue
            code = (
                CanonicalDiagnosticCode.BLOCKING_CONTEXT_FACT
                if fact.blocking
                else CanonicalDiagnosticCode.UNRESOLVED_CONTEXT_FACT
            )
            fact_severity = severity if fact.blocking else DiagnosticSeverity.WARNING
            diagnostics.append(
                _diagnostic(
                    code,
                    fact_severity,
                    f"context fact {fact.fact_id} is unresolved",
                    source_cue_ids=fact.scope_source_cue_ids,
                )
            )
    if glossary is not None:
        for entry in glossary.entries:
            if entry.decision_status is not GlossaryDecisionStatus.UNRESOLVED:
                continue
            code = (
                CanonicalDiagnosticCode.BLOCKING_GLOSSARY_ENTRY
                if entry.blocking
                else CanonicalDiagnosticCode.UNRESOLVED_GLOSSARY_ENTRY
            )
            entry_severity = severity if entry.blocking else DiagnosticSeverity.WARNING
            diagnostics.append(
                _diagnostic(
                    code,
                    entry_severity,
                    f"glossary entry {entry.entry_id} is unresolved",
                    source_cue_ids=entry.scope_source_cue_ids,
                )
            )
    return diagnostics


def _lineage_status_diagnostics(
    cue: TargetCueV2,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    if cue.lineage_status is LineageStatus.UNASSIGNED:
        return [
            _diagnostic(
                CanonicalDiagnosticCode.LINEAGE_UNASSIGNED,
                severity,
                f"target cue {cue.cue_id} has no source lineage",
                target_cue_ids=(cue.cue_id,),
            )
        ]
    if cue.lineage_status is LineageStatus.NEEDS_CONFIRMATION:
        return [
            _diagnostic(
                CanonicalDiagnosticCode.LINEAGE_NEEDS_CONFIRMATION,
                severity,
                f"target cue {cue.cue_id} has provisional source lineage",
                source_cue_ids=cue.source_cue_ids,
                target_cue_ids=(cue.cue_id,),
            )
        ]
    return []


def _linked_source_diagnostics(
    cue: TargetCueV2,
    source_by_id: dict[str, SourceCueV2],
    source_positions: dict[str, int],
    source_reference_counts: Counter[str],
    severity: DiagnosticSeverity,
) -> tuple[list[CanonicalDiagnostic], tuple[str, ...]]:
    diagnostics: list[CanonicalDiagnostic] = []
    unknown = tuple(
        source_id for source_id in cue.source_cue_ids if source_id not in source_by_id
    )
    if unknown:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.UNKNOWN_SOURCE_CUE,
                severity,
                f"target cue {cue.cue_id} references unknown source cues",
                source_cue_ids=unknown,
                target_cue_ids=(cue.cue_id,),
            )
        )
    known = tuple(
        source_id for source_id in cue.source_cue_ids if source_id in source_by_id
    )
    positions = tuple(source_positions[source_id] for source_id in known)
    if positions != tuple(sorted(positions)):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.SOURCE_ORDER_VIOLATION,
                severity,
                f"target cue {cue.cue_id} violates source order",
                source_cue_ids=known,
                target_cue_ids=(cue.cue_id,),
            )
        )
    if known and len(known) == len(cue.source_cue_ids):
        linked = tuple(source_by_id[source_id] for source_id in known)
        envelope_start = min(item.start_ms for item in linked)
        envelope_end = max(item.end_ms for item in linked)
        if cue.start_ms < envelope_start or cue.end_ms > envelope_end:
            diagnostics.append(
                _diagnostic(
                    CanonicalDiagnosticCode.TIMING_ENVELOPE_VIOLATION,
                    severity,
                    f"target cue {cue.cue_id} exceeds source timing envelope",
                    source_cue_ids=known,
                    target_cue_ids=(cue.cue_id,),
                )
            )
        diagnostics.extend(
            _speaker_lineage_diagnostics(
                cue,
                linked,
                severity,
                exact_alignment=all(
                    source_reference_counts[source_id] == 1 for source_id in known
                ),
            )
        )
    return diagnostics, known


def _collapse_ownership(
    groups: tuple[tuple[str, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    collapsed: list[tuple[str, ...]] = []
    for group in groups:
        if not collapsed or collapsed[-1] != group:
            collapsed.append(group)
    return tuple(collapsed)


def _source_ownership_shape(
    linked: tuple[SourceCueV2, ...],
) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    for source_cue in linked:
        source_lines = source_cue.text.replace("\\N", "\n").splitlines()
        if len(source_lines) == 1:
            groups.append(source_cue.speakers)
        else:
            groups.extend((speaker,) for speaker in source_cue.speakers)
    return _collapse_ownership(tuple(groups))


def _target_ownership_shape(
    cue: TargetCueV2,
) -> tuple[tuple[str, ...], ...] | None:
    target_lines = tuple(line.strip() for line in cue.text.split("\n"))
    if len(target_lines) == 1:
        return (cue.speakers,)
    explicit_turns = all(line.startswith("- ") for line in target_lines)
    if not explicit_turns:
        return ((cue.speakers[0],),) if len(cue.speakers) == 1 else None
    if len(cue.speakers) == 1:
        return ((cue.speakers[0],),)
    if len(cue.speakers) != len(target_lines):
        return None
    return _collapse_ownership(tuple((speaker,) for speaker in cue.speakers))


def _speaker_lineage_diagnostics(
    cue: TargetCueV2,
    linked: tuple[SourceCueV2, ...],
    severity: DiagnosticSeverity,
    *,
    exact_alignment: bool,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    source_speakers = {
        speaker for source_cue in linked for speaker in source_cue.speakers
    }
    foreign_speakers = tuple(
        speaker for speaker in cue.speakers if speaker not in source_speakers
    )
    if foreign_speakers:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.TARGET_SPEAKER_NOT_IN_SOURCE,
                severity,
                f"target cue {cue.cue_id} contains speakers absent from source lineage",
                source_cue_ids=cue.source_cue_ids,
                target_cue_ids=(cue.cue_id,),
            )
        )

    target_shape = _target_ownership_shape(cue)
    ambiguous_turns = target_shape is None or (
        exact_alignment and target_shape != _source_ownership_shape(linked)
    )
    if ambiguous_turns:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.TARGET_SPEAKER_ORDER_MISMATCH,
                severity,
                f"target cue {cue.cue_id} does not preserve source speaker turns",
                source_cue_ids=cue.source_cue_ids,
                target_cue_ids=(cue.cue_id,),
            )
        )
    return diagnostics


def _lineage_diagnostics(
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2 | None,
    *,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    referenced: set[str] = set()
    source_by_id = {} if source is None else {cue.cue_id: cue for cue in source.cues}
    source_positions = (
        {}
        if source is None
        else {cue.cue_id: index for index, cue in enumerate(source.cues)}
    )
    source_reference_counts = Counter(
        source_id for cue in translation.cues for source_id in cue.source_cue_ids
    )
    for cue in translation.cues:
        diagnostics.extend(_lineage_status_diagnostics(cue, severity))
        if source is None or not cue.source_cue_ids:
            continue
        linked_diagnostics, known = _linked_source_diagnostics(
            cue,
            source_by_id,
            source_positions,
            source_reference_counts,
            severity,
        )
        diagnostics.extend(linked_diagnostics)
        referenced.update(known)
    if source is None:
        return diagnostics
    missing = tuple(
        cue.cue_id
        for cue in source.cues
        if cue.content_kind is not ContentKind.UNKNOWN and cue.cue_id not in referenced
    )
    if missing:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.SOURCE_COVERAGE_MISSING,
                severity,
                "translatable source cues are not covered by target lineage",
                source_cue_ids=missing,
            )
        )
    return diagnostics


def _target_reference_diagnostics(
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2 | None,
    context: ContentContextAsset | None,
    *,
    severity: DiagnosticSeverity,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    known_context: set[str] = (
        set() if context is None else {fact.fact_id for fact in context.facts}
    )
    known_evidence: set[str] = (
        set()
        if source is None
        else {entry.evidence_id for entry in source.evidence.entries}
    )
    for cue in translation.cues:
        unknown_context = tuple(
            context_id
            for context_id in cue.context_ids
            if context_id not in known_context
        )
        if unknown_context:
            diagnostics.append(
                _diagnostic(
                    CanonicalDiagnosticCode.UNKNOWN_CONTEXT_ID,
                    severity,
                    f"target cue {cue.cue_id} references unknown context IDs",
                    target_cue_ids=(cue.cue_id,),
                )
            )
        unknown_evidence = tuple(
            evidence_id
            for evidence_id in cue.evidence_ids
            if evidence_id not in known_evidence
        )
        if unknown_evidence:
            diagnostics.append(
                _diagnostic(
                    CanonicalDiagnosticCode.UNKNOWN_EVIDENCE_ID,
                    severity,
                    f"target cue {cue.cue_id} references unknown evidence IDs",
                    target_cue_ids=(cue.cue_id,),
                )
            )
    return diagnostics


def _review_coverage_complete(
    review: TranslationReviewArtifact,
    translation: TranslationDocumentV2,
    *,
    source: SourceTranscriptV2 | None = None,
    glossary: TranslationGlossaryAsset | None = None,
) -> bool:
    required_sources: set[str] = (
        set() if source is None else {cue.cue_id for cue in source.cues}
    )
    required_targets = {cue.cue_id for cue in translation.cues}
    required_boundaries = {
        (left.cue_id, right.cue_id)
        for left, right in zip(translation.cues, translation.cues[1:], strict=False)
    }
    required_glossary: set[str] = (
        set() if glossary is None else {entry.entry_id for entry in glossary.entries}
    )
    return (
        required_sources <= set(review.coverage.source_cue_ids)
        and required_targets <= set(review.coverage.target_cue_ids)
        and required_boundaries <= set(review.coverage.target_boundary_pairs)
        and required_glossary <= set(review.coverage.glossary_entry_ids)
    )


def _review_finalization_diagnostics(
    evaluation: ReviewEvaluation,
    finalization: TranslationFinalization,
    *,
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2 | None,
    glossary: TranslationGlossaryAsset | None,
) -> list[CanonicalDiagnostic]:
    diagnostics = list(evaluation.diagnostics)
    expected_acknowledgement = {
        EffectiveReviewState.PASSED: ReviewAcknowledgement.PASSED,
        EffectiveReviewState.NOT_RUN: ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED,
        EffectiveReviewState.STALE: ReviewAcknowledgement.STALE_ACKNOWLEDGED,
        EffectiveReviewState.FAILED: ReviewAcknowledgement.FAILED_FINDINGS_DISPOSED,
    }[evaluation.state]
    if finalization.review_acknowledgement is not expected_acknowledgement:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.REVIEW_ACKNOWLEDGEMENT_MISMATCH,
                DiagnosticSeverity.BLOCKING,
                "finalization acknowledgement does not match effective review state",
            )
        )
    review = evaluation.authoritative_review
    if review is None or source is None or glossary is None:
        return diagnostics
    if not _review_coverage_complete(
        review,
        translation,
        source=source,
        glossary=glossary,
    ):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.REVIEW_COVERAGE_INCOMPLETE,
                DiagnosticSeverity.BLOCKING,
                "authoritative review coverage is incomplete",
            )
        )
    if evaluation.state is not EffectiveReviewState.FAILED:
        return diagnostics
    disposed = {
        (item.review_id, item.finding_id) for item in finalization.finding_dispositions
    }
    for finding in review.findings:
        if not finding.blocking:
            continue
        key = (review.review_id, finding.finding_id)
        if key not in disposed:
            diagnostics.append(
                _diagnostic(
                    CanonicalDiagnosticCode.UNDISPOSED_BLOCKING_FINDING,
                    DiagnosticSeverity.BLOCKING,
                    f"blocking finding {finding.finding_id} has no disposition",
                    source_cue_ids=finding.affected_source_cue_ids,
                    target_cue_ids=finding.affected_target_cue_ids,
                )
            )
    return diagnostics


def draft_translation_diagnostics(
    translation: TranslationDocumentV2,
    environment: TranslationValidationContext,
) -> CanonicalValidationReport:
    """Return non-destructive diagnostics while preserving draft saveability."""
    diagnostics = _digest_diagnostics(translation)
    diagnostics.extend(_translation_save_diagnostics(translation))
    diagnostics.extend(
        _dependency_diagnostics(
            translation,
            environment,
            DiagnosticSeverity.WARNING,
        )
    )
    diagnostics.extend(
        _lineage_diagnostics(
            translation,
            environment.source,
            severity=DiagnosticSeverity.WARNING,
        )
    )
    diagnostics.extend(
        _target_reference_diagnostics(
            translation,
            environment.source,
            environment.context,
            severity=DiagnosticSeverity.WARNING,
        )
    )
    diagnostics.extend(
        _asset_decision_diagnostics(
            environment.context,
            environment.glossary,
            blocking=False,
        )
    )
    diagnostics.extend(
        evaluate_review_state(
            translation,
            environment.reviews,
            environment.review_profile,
            source=environment.source,
            glossary=environment.glossary,
        ).diagnostics
    )
    for artifact in (
        environment.source,
        environment.context,
        environment.glossary,
    ):
        if artifact is not None:
            diagnostics.extend(_evidence_locator_diagnostics(artifact))
    return _report(diagnostics)


def validate_translation_finalization(
    translation: TranslationDocumentV2,
    finalization: TranslationFinalization,
    environment: TranslationValidationContext,
) -> CanonicalValidationReport:
    """Return every deterministic blocker for human translation finalization."""
    diagnostics = _digest_diagnostics(translation)
    diagnostics.extend(_translation_save_diagnostics(translation))
    if translation.lifecycle_status is not DocumentLifecycleStatus.DRAFT:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.INVALID_LIFECYCLE,
                DiagnosticSeverity.BLOCKING,
                "only a draft translation can be finalized",
            )
        )
    diagnostics.extend(
        _dependency_diagnostics(
            translation,
            environment,
            DiagnosticSeverity.BLOCKING,
        )
    )
    diagnostics.extend(
        _lineage_diagnostics(
            translation,
            environment.source,
            severity=DiagnosticSeverity.BLOCKING,
        )
    )
    diagnostics.extend(
        _target_reference_diagnostics(
            translation,
            environment.source,
            environment.context,
            severity=DiagnosticSeverity.BLOCKING,
        )
    )
    diagnostics.extend(
        _asset_decision_diagnostics(
            environment.context,
            environment.glossary,
            blocking=True,
        )
    )
    if translation.unresolved_decisions:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.UNRESOLVED_TRANSLATION_DECISION,
                DiagnosticSeverity.BLOCKING,
                "translation has unresolved human decisions",
                source_cue_ids=tuple(
                    source_id
                    for decision in translation.unresolved_decisions
                    for source_id in decision.source_cue_ids
                ),
                target_cue_ids=tuple(
                    target_id
                    for decision in translation.unresolved_decisions
                    for target_id in decision.target_cue_ids
                ),
            )
        )
    evaluation = evaluate_review_state(
        translation,
        environment.reviews,
        environment.review_profile,
        source=environment.source,
        glossary=environment.glossary,
    )
    diagnostics.extend(
        _review_finalization_diagnostics(
            evaluation,
            finalization,
            translation=translation,
            source=environment.source,
            glossary=environment.glossary,
        )
    )
    for artifact in (
        environment.source,
        environment.context,
        environment.glossary,
    ):
        if artifact is not None:
            diagnostics.extend(_evidence_locator_diagnostics(artifact))
    return _report(diagnostics)


def _raise_if_blocking(report: CanonicalValidationReport) -> None:
    if report.has_blocking:
        raise CanonicalFinalizationError(report)


def _source_context_diagnostics(
    source: SourceTranscriptV2,
    input_context: ContentContextAsset | None,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    if input_context is None:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.MISSING_CONTEXT_REF,
                DiagnosticSeverity.BLOCKING,
                "the input context revision is unavailable",
            )
        )
        return diagnostics
    if not (
        source.input_context_ref.asset_id == input_context.asset_id
        and source.input_context_ref.revision_id == input_context.revision_id
        and source.input_context_ref.content_digest == input_context.content_digest
    ):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_IDENTITY_MISMATCH,
                DiagnosticSeverity.BLOCKING,
                "the resolved input context identity does not match source ref",
            )
        )
    if input_context.stage is not ContextStage.INPUT:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_NOT_PREPARED,
                DiagnosticSeverity.BLOCKING,
                "source finalization requires an input-stage context",
            )
        )
    if input_context.lifecycle_status is not AssetLifecycleStatus.READY:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_NOT_READY,
                DiagnosticSeverity.BLOCKING,
                "input context is not ready",
            )
        )
    diagnostics.extend(_asset_decision_diagnostics(input_context, None, blocking=True))
    return diagnostics


def finalize_source(
    source: SourceTranscriptV2,
    finalization: SourceFinalization,
    *,
    input_context: ContentContextAsset | None,
) -> SourceTranscriptV2:
    """Return a human-finalized source revision or raise with all blockers."""
    diagnostics = _digest_diagnostics(source)
    if input_context is not None:
        diagnostics.extend(_digest_diagnostics(input_context))
    if source.lifecycle_status is not DocumentLifecycleStatus.DRAFT:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.INVALID_LIFECYCLE,
                DiagnosticSeverity.BLOCKING,
                "only a draft source can be finalized",
            )
        )
    diagnostics.extend(_source_context_diagnostics(source, input_context))
    diagnostics.extend(
        _source_speaker_diagnostics(
            source,
            DiagnosticSeverity.BLOCKING,
        )
    )
    _raise_if_blocking(_report(diagnostics))
    finalized = source.model_copy(
        update={
            "lifecycle_status": DocumentLifecycleStatus.FINALIZED,
            "finalization": finalization,
        }
    )
    return bind_canonical_digests(finalized)


def _prepared_asset_source_diagnostics(
    reference: DocumentReference | None,
    source: SourceTranscriptV2 | None,
) -> list[CanonicalDiagnostic]:
    diagnostics: list[CanonicalDiagnostic] = []
    if source is None:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.MISSING_SOURCE_REF,
                DiagnosticSeverity.BLOCKING,
                "the prepared asset source revision is unavailable",
            )
        )
        return diagnostics
    if reference is None or not _source_reference_matches(reference, source):
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.SOURCE_IDENTITY_MISMATCH,
                DiagnosticSeverity.BLOCKING,
                "prepared asset source identity does not match",
            )
        )
    if source.lifecycle_status is not DocumentLifecycleStatus.FINALIZED:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.SOURCE_NOT_FINALIZED,
                DiagnosticSeverity.BLOCKING,
                "prepared assets require a finalized source revision",
            )
        )
    return diagnostics


def _scope_diagnostics(
    source: SourceTranscriptV2 | None,
    context: ContentContextAsset | None,
    glossary: TranslationGlossaryAsset | None,
) -> list[CanonicalDiagnostic]:
    if source is None:
        return []
    known = {cue.cue_id for cue in source.cues}
    diagnostics: list[CanonicalDiagnostic] = []
    if context is not None:
        for fact in context.facts:
            unknown = tuple(
                source_id
                for source_id in fact.scope_source_cue_ids
                if source_id not in known
            )
            if unknown:
                diagnostics.append(
                    _diagnostic(
                        CanonicalDiagnosticCode.CONTEXT_SCOPE_UNKNOWN_SOURCE,
                        DiagnosticSeverity.BLOCKING,
                        f"context fact {fact.fact_id} has unknown source scope",
                        source_cue_ids=unknown,
                    )
                )
    if glossary is not None:
        for entry in glossary.entries:
            unknown = tuple(
                source_id
                for source_id in entry.scope_source_cue_ids
                if source_id not in known
            )
            if unknown:
                diagnostics.append(
                    _diagnostic(
                        CanonicalDiagnosticCode.GLOSSARY_SCOPE_UNKNOWN_SOURCE,
                        DiagnosticSeverity.BLOCKING,
                        f"glossary entry {entry.entry_id} has unknown source scope",
                        source_cue_ids=unknown,
                    )
                )
    return diagnostics


def _glossary_conflict_diagnostics(
    glossary: TranslationGlossaryAsset,
) -> list[CanonicalDiagnostic]:
    return [
        _diagnostic(
            CanonicalDiagnosticCode.GLOSSARY_CONFLICT,
            DiagnosticSeverity.BLOCKING,
            (
                "glossary entries "
                f"{conflict.entry_ids[0]} and {conflict.entry_ids[1]} "
                "define different directives at the same priority"
            ),
            source_cue_ids=conflict.scope_source_cue_ids,
        )
        for conflict in glossary_conflicts(glossary)
    ]


def ready_context_asset(
    context: ContentContextAsset,
    *,
    source: SourceTranscriptV2 | None,
) -> ContentContextAsset:
    """Return a ready prepared-context revision or raise with all blockers."""
    diagnostics = _digest_diagnostics(context)
    if source is not None:
        diagnostics.extend(_digest_diagnostics(source))
    if context.stage is not ContextStage.PREPARED:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.CONTEXT_NOT_PREPARED,
                DiagnosticSeverity.BLOCKING,
                "only a prepared context can transition to ready",
            )
        )
    if context.lifecycle_status is not AssetLifecycleStatus.DRAFT:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.INVALID_LIFECYCLE,
                DiagnosticSeverity.BLOCKING,
                "only a draft context can transition to ready",
            )
        )
    diagnostics.extend(
        _prepared_asset_source_diagnostics(context.prepared_from_source_ref, source)
    )
    diagnostics.extend(_asset_decision_diagnostics(context, None, blocking=True))
    diagnostics.extend(_scope_diagnostics(source, context, None))
    _raise_if_blocking(_report(diagnostics))
    return bind_canonical_digests(
        context.model_copy(update={"lifecycle_status": AssetLifecycleStatus.READY})
    )


def ready_glossary_asset(
    glossary: TranslationGlossaryAsset,
    *,
    source: SourceTranscriptV2 | None,
) -> TranslationGlossaryAsset:
    """Return a ready glossary revision or raise with all blockers."""
    diagnostics = _digest_diagnostics(glossary)
    if source is not None:
        diagnostics.extend(_digest_diagnostics(source))
    if glossary.lifecycle_status is not AssetLifecycleStatus.DRAFT:
        diagnostics.append(
            _diagnostic(
                CanonicalDiagnosticCode.INVALID_LIFECYCLE,
                DiagnosticSeverity.BLOCKING,
                "only a draft glossary can transition to ready",
            )
        )
    diagnostics.extend(
        _prepared_asset_source_diagnostics(glossary.prepared_from_source_ref, source)
    )
    diagnostics.extend(_asset_decision_diagnostics(None, glossary, blocking=True))
    diagnostics.extend(_scope_diagnostics(source, None, glossary))
    diagnostics.extend(_glossary_conflict_diagnostics(glossary))
    _raise_if_blocking(_report(diagnostics))
    return bind_canonical_digests(
        glossary.model_copy(update={"lifecycle_status": AssetLifecycleStatus.READY})
    )


def finalize_translation(
    translation: TranslationDocumentV2,
    finalization: TranslationFinalization,
    report: CanonicalValidationReport,
) -> TranslationDocumentV2:
    """Return a human-finalized translation revision or raise atomically."""
    _raise_if_blocking(report)
    finalized = translation.model_copy(
        update={
            "lifecycle_status": DocumentLifecycleStatus.FINALIZED,
            "finalization": finalization,
        }
    )
    return bind_canonical_digests(finalized)


def create_translation_draft_revision(
    translation: TranslationDocumentV2,
    *,
    revision_id: str,
) -> TranslationDocumentV2:
    """Copy one finalized translation into a new editable draft revision."""
    if translation.lifecycle_status is not DocumentLifecycleStatus.FINALIZED:
        message = "copy-on-edit requires a finalized translation revision"
        raise ValueError(message)
    if revision_id == translation.revision_id:
        message = "new translation revision_id must differ from the finalized revision"
        raise ValueError(message)
    draft = translation.model_copy(
        update={
            "revision_id": revision_id,
            "supersedes_revision_id": translation.revision_id,
            "lifecycle_status": DocumentLifecycleStatus.DRAFT,
            "finalization": None,
        }
    )
    return bind_canonical_digests(draft)


def create_source_draft_revision(
    source: SourceTranscriptV2,
    *,
    revision_id: str,
) -> SourceTranscriptV2:
    """Copy one finalized source into a new editable draft revision."""
    if source.lifecycle_status is not DocumentLifecycleStatus.FINALIZED:
        message = "copy-on-edit requires a finalized source revision"
        raise ValueError(message)
    if revision_id == source.revision_id:
        message = "new source revision_id must differ from the finalized revision"
        raise ValueError(message)
    draft = source.model_copy(
        update={
            "revision_id": revision_id,
            "supersedes_revision_id": source.revision_id,
            "lifecycle_status": DocumentLifecycleStatus.DRAFT,
            "finalization": None,
        }
    )
    return bind_canonical_digests(draft)


__all__ = [
    "CanonicalDiagnostic",
    "CanonicalDiagnosticCode",
    "CanonicalFinalizationError",
    "CanonicalValidationReport",
    "DiagnosticSeverity",
    "EffectiveReviewState",
    "ReviewEvaluation",
    "TranslationValidationContext",
    "create_source_draft_revision",
    "create_translation_draft_revision",
    "draft_translation_diagnostics",
    "evaluate_review_state",
    "finalize_source",
    "finalize_translation",
    "finalize_translation_core",
    "ready_context_asset",
    "ready_glossary_asset",
    "validate_canonical_save",
    "validate_translation_finalization",
]
