# Copyright 2026 Transcription Module contributors

"""Canonical translation preflight and selection workflow support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tests.e2e.canonical_workflow_translation_fixture_support import (
    candidate,
    failed_attempt,
    successful_attempt,
)
from translation_module.canonical.asset_preparation import prepare_translation_assets
from translation_module.canonical.attempts import (
    TranslationAttemptStore,
    TranslationSelectionReceipt,
)
from translation_module.canonical.digest import (
    bind_canonical_digests,
    verify_canonical_digests,
)
from translation_module.canonical.preflight import (
    AdjacentCoverageResult,
    CandidatePreflightContext,
    PreflightCheckerEvidence,
    PreflightedSelection,
    PreflightGateCode,
    PreflightGateError,
    PreflightReport,
    SemanticQualificationKind,
    SemanticQualificationResult,
    build_preflight_report,
    validate_preflight_report,
    write_preflight_report,
)
from translation_module.canonical.runtime import CanonicalTranslationWorkflow
from translation_module.canonical.source import SourceFinalization
from translation_module.canonical.validation import (
    finalize_source,
    ready_context_asset,
    ready_glossary_asset,
    validate_canonical_save,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e.canonical_workflow_finalization_support import (
        FinalizedWorkflowResult,
    )
    from translation_module.canonical.context import ContentContextAsset
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import TranslationDocumentV2


@dataclass(frozen=True, slots=True)
class TranslationWorkflowResult:
    """Selected candidate and all ready canonical dependencies."""

    source: SourceTranscriptV2
    context: ContentContextAsset
    glossary: TranslationGlossaryAsset
    candidate: TranslationDocumentV2
    evidence_root: Path
    attempt_store: TranslationAttemptStore
    preflight: PreflightReport
    selection_path: Path
    authority_root: Path
    observations: dict[str, str | int | bool]


def checker_evidence(
    document: TranslationDocumentV2,
    source_ids: tuple[str, ...] | None = None,
) -> PreflightCheckerEvidence:
    """Cover every required deterministic checker scope without findings."""
    target_ids = tuple(cue.cue_id for cue in document.cues)
    if source_ids is None:
        source_ids = tuple(
            dict.fromkeys(
                source_id for cue in document.cues for source_id in cue.source_cue_ids
            )
        )
    return PreflightCheckerEvidence(
        adjacent_results=tuple(
            AdjacentCoverageResult(
                left_target_cue_id=left.cue_id,
                right_target_cue_id=right.cue_id,
                finding_ids=(),
            )
            for left, right in zip(document.cues, document.cues[1:], strict=False)
        ),
        glossary_results=(),
        semantic_results=(
            SemanticQualificationResult(
                kind=SemanticQualificationKind.SOURCE_CLAIMS,
                source_cue_ids=source_ids,
                target_cue_ids=target_ids,
                reviewer_execution_ids=("e2e-source-claims",),
                blocking_finding_ids=(),
                advisory_finding_ids=(),
            ),
            SemanticQualificationResult(
                kind=SemanticQualificationKind.TARGET_ONLY,
                source_cue_ids=(),
                target_cue_ids=target_ids,
                reviewer_execution_ids=("e2e-target-only",),
                blocking_finding_ids=(),
                advisory_finding_ids=(),
            ),
        ),
        findings=(),
    )


def selection_receipt(
    document: TranslationDocumentV2,
    attempt_id: str = "attempt-b",
) -> TranslationSelectionReceipt:
    """Build one immutable receipt for a recorded workflow attempt."""
    return TranslationSelectionReceipt(
        selection_id=f"selection:{attempt_id}",
        selected_at="2026-08-18T11:00:00Z",
        selected_by="human-editor",
        translation_document_id=document.document_id,
        translation_revision_id=document.revision_id,
        translation_document_digest=document.content_digest,
        attempt_id=attempt_id,
        attempt_candidate_digest=document.content_digest,
    )


def run_translation_workflow(
    finalized: FinalizedWorkflowResult,
    root: Path,
) -> TranslationWorkflowResult:
    """Append A/B/C, block dirty preflight, and select clean B once."""
    source = finalize_source(
        finalized.source,
        SourceFinalization(
            finalized_at="2026-08-18T10:30:00Z",
            finalized_by="human-editor",
        ),
        input_context=finalized.context,
    )
    bundle = prepare_translation_assets(
        source,
        finalized.context,
        source_path="source.json",
    )
    context = ready_context_asset(bundle.context, source=source)
    glossary = ready_glossary_asset(bundle.glossary, source=source)
    current = candidate(
        source,
        (context.asset_id, context.revision_id, context.content_digest),
        (glossary.asset_id, glossary.revision_id, glossary.content_digest),
    )
    workflow = CanonicalTranslationWorkflow(root)
    store = workflow.attempt_store
    attempts = (
        successful_attempt(current, "attempt-a", 1),
        successful_attempt(current, "attempt-b", 2),
        failed_attempt(current, "attempt-c", 3),
    )
    for item in attempts:
        _ = workflow.record_attempt(item, current)

    long_text = "x" * 21
    dirty_cues = (
        current.cues[0].model_copy(update={"text": long_text}),
        current.cues[1].model_copy(update={"text": long_text}),
        *current.cues[2:],
    )
    dirty = bind_canonical_digests(current.model_copy(update={"cues": dirty_cues}))
    dirty_context = CandidatePreflightContext(dirty, source, glossary)
    dirty_before = dirty.model_dump_json()
    incomplete = checker_evidence(dirty).model_copy(update={"adjacent_results": ()})
    with pytest.raises(PreflightGateError) as missing:
        _ = build_preflight_report(dirty_context, incomplete)
    dirty_report = build_preflight_report(dirty_context, checker_evidence(dirty))
    assert missing.value.code is PreflightGateCode.COVERAGE_MISSING
    assert dirty_report.over_cps_findings
    assert dirty_report.duplicate_findings
    validate_preflight_report(dirty_context, dirty_report)
    assert dirty.model_dump_json() == dirty_before

    context_graph = CandidatePreflightContext(current, source, glossary)
    first_report = build_preflight_report(context_graph, checker_evidence(current))
    second_report = build_preflight_report(context_graph, checker_evidence(current))
    assert first_report == second_report
    report_path = write_preflight_report(root / "preflight.json", first_report)
    parsed_report = PreflightReport.model_validate_json(report_path.read_bytes())
    validate_preflight_report(context_graph, parsed_report)
    workflow.authorize(
        PreflightedSelection(selection_receipt(current), context_graph, parsed_report)
    )
    selection = store.selection_path(current.document_id, current.revision_id)
    for artifact in (source, context, glossary, current):
        validate_canonical_save(artifact)
        verify_canonical_digests(artifact)
    loaded = store.load()
    assert tuple(item.ordinal for item in loaded) == (1, 2, 3)
    assert tuple(item.parent_attempt_id for item in loaded) == (
        None,
        "attempt-a",
        "attempt-b",
    )
    return TranslationWorkflowResult(
        source,
        context,
        glossary,
        current,
        finalized.store.runs_dir / finalized.source.document_id,
        store,
        parsed_report,
        selection,
        root,
        {
            "attempt_count": len(loaded),
            "selected_attempt": "attempt-b",
            "blocking_over_cps": len(dirty_report.over_cps_findings),
            "blocking_duplicates": len(dirty_report.duplicate_findings),
            "missing_coverage": missing.value.code.value,
            "candidate_unchanged": True,
        },
    )
