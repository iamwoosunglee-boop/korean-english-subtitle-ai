# Copyright 2026 Transcription Module contributors

"""Deterministic candidate preflight and Todo 4 selection gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never

from pydantic import ValidationError

from translation_module.canonical._preflight_models import (
    PREFLIGHT_PROFILE_VERSION,
    AdjacentCoverageResult,
    AdjacentDuplicateFinding,
    CandidatePreflightContext,
    CheckerFinding,
    GlossaryCoverageResult,
    GlossaryTargetRequirement,
    MissingGlossaryTargetFinding,
    OverCpsFinding,
    OverLineCountFinding,
    OverLineWidthFinding,
    PreflightCheckerEvidence,
    PreflightGateCode,
    PreflightGateError,
    PreflightReport,
    SemanticQualificationKind,
    SemanticQualificationResult,
)
from translation_module.canonical._preflight_store import write_preflight_report
from translation_module.canonical.digest import (
    CanonicalDigestMismatchError,
    canonical_sha256,
    verify_canonical_digests,
)
from translation_module.canonical.glossary import resolve_glossary_for_cue

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical.attempts import (
        TranslationAttemptStore,
        TranslationSelectionReceipt,
    )
    from translation_module.canonical.translation import TargetCueV2

REPEATED_GLOSSARY_MINIMUM = 2
DISPLAY_LINE_LIMIT = 2
DISPLAY_CHARACTER_LIMIT = 42


@dataclass(frozen=True, slots=True)
class PreflightedSelection:
    """One selection request carrying its current preflight authority."""

    receipt: TranslationSelectionReceipt
    context: CandidatePreflightContext
    report: PreflightReport

    def validate(self) -> None:
        """Require the carried report to remain current and clean."""
        validate_preflight_report(self.context, self.report)


def _fail(code: PreflightGateCode, detail: str) -> Never:
    raise PreflightGateError(code, detail)


def _validate_context(context: CandidatePreflightContext) -> None:
    try:
        for artifact in (context.candidate, context.source, context.glossary):
            verify_canonical_digests(artifact)
    except CanonicalDigestMismatchError as error:
        _fail(PreflightGateCode.INPUT_INVALID, str(error))
    candidate = context.candidate
    source = context.source
    glossary = context.glossary
    if (
        candidate.source_ref.document_id != source.document_id
        or candidate.source_ref.revision_id != source.revision_id
        or candidate.source_ref.content_digest != source.content_digest
        or candidate.glossary_ref.asset_id != glossary.asset_id
        or candidate.glossary_ref.revision_id != glossary.revision_id
        or candidate.glossary_ref.content_digest != glossary.content_digest
        or glossary.prepared_from_source_ref.document_id != source.document_id
        or glossary.prepared_from_source_ref.revision_id != source.revision_id
        or glossary.prepared_from_source_ref.content_digest != source.content_digest
    ):
        _fail(PreflightGateCode.INPUT_INVALID, "preflight dependency binding mismatch")


def _expected_adjacent(
    context: CandidatePreflightContext,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (left.cue_id, right.cue_id)
        for left, right in zip(
            context.candidate.cues,
            context.candidate.cues[1:],
            strict=False,
        )
    )


def _expected_glossary(
    context: CandidatePreflightContext,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    occurrences: dict[str, list[str]] = {}
    try:
        for cue in context.source.cues:
            for entry in resolve_glossary_for_cue(cue, context.glossary):
                occurrences.setdefault(entry.entry_id, []).append(cue.cue_id)
    except ValueError as error:
        _fail(PreflightGateCode.INPUT_INVALID, str(error))
    return tuple(
        (entry.entry_id, tuple(occurrences.get(entry.entry_id, ())))
        for entry in context.glossary.entries
        if len(occurrences.get(entry.entry_id, ())) >= REPEATED_GLOSSARY_MINIMUM
    )


def _coverage_code[T](actual: tuple[T, ...], expected: tuple[T, ...]) -> None:
    if len(actual) != len(set(actual)):
        _fail(PreflightGateCode.COVERAGE_DUPLICATE, "coverage result is duplicated")
    missing = tuple(item for item in expected if item not in actual)
    if missing:
        _fail(
            PreflightGateCode.COVERAGE_MISSING,
            f"required coverage is missing: {missing}",
        )
    if actual != expected:
        _fail(PreflightGateCode.COVERAGE_INVALID, "coverage IDs or order are invalid")


def _validate_semantic_qualifications(
    context: CandidatePreflightContext,
    results: tuple[SemanticQualificationResult, ...],
) -> None:
    kinds = tuple(item.kind for item in results)
    expected_kinds = (
        SemanticQualificationKind.SOURCE_CLAIMS,
        SemanticQualificationKind.TARGET_ONLY,
    )
    _coverage_code(kinds, expected_kinds)
    executions = tuple(
        execution_id for item in results for execution_id in item.reviewer_execution_ids
    )
    if len(executions) != len(set(executions)):
        _fail(
            PreflightGateCode.COVERAGE_DUPLICATE,
            "semantic qualifications require distinct reviewer executions",
        )
    source_ids = tuple(cue.cue_id for cue in context.source.cues)
    target_ids = tuple(cue.cue_id for cue in context.candidate.cues)
    source_result, target_result = results
    _coverage_code(source_result.source_cue_ids, source_ids)
    _coverage_code(source_result.target_cue_ids, target_ids)
    _coverage_code(target_result.source_cue_ids, ())
    _coverage_code(target_result.target_cue_ids, target_ids)


def _validate_finding_references(
    context: CandidatePreflightContext,
    evidence: PreflightCheckerEvidence,
) -> None:
    finding_ids = tuple(item.finding_id for item in evidence.findings)
    if len(finding_ids) != len(set(finding_ids)):
        _fail(PreflightGateCode.COVERAGE_DUPLICATE, "checker finding ID is duplicated")
    references = [
        finding_id
        for result in (*evidence.adjacent_results, *evidence.glossary_results)
        for finding_id in result.finding_ids
    ]
    references.extend(
        finding_id
        for result in evidence.semantic_results
        for finding_id in (
            *result.blocking_finding_ids,
            *result.advisory_finding_ids,
        )
    )
    if len(references) != len(set(references)):
        _fail(
            PreflightGateCode.COVERAGE_DUPLICATE,
            "checker finding reference is duplicated",
        )
    if set(references) != set(finding_ids):
        _fail(
            PreflightGateCode.COVERAGE_INVALID,
            "checker finding references are not exact",
        )
    known_sources = {cue.cue_id for cue in context.source.cues}
    known_targets = {cue.cue_id for cue in context.candidate.cues}
    finding_by_id = {item.finding_id: item for item in evidence.findings}
    if any(
        not set(item.affected_source_cue_ids) <= known_sources
        or not set(item.affected_target_cue_ids) <= known_targets
        for item in evidence.findings
    ):
        _fail(PreflightGateCode.COVERAGE_INVALID, "checker finding scope is foreign")
    for result in evidence.adjacent_results:
        boundary = {result.left_target_cue_id, result.right_target_cue_id}
        if any(
            not finding_by_id[finding_id].affected_target_cue_ids
            or not set(finding_by_id[finding_id].affected_target_cue_ids) <= boundary
            for finding_id in result.finding_ids
        ):
            _fail(
                PreflightGateCode.COVERAGE_INVALID,
                "adjacent finding scope does not match its boundary",
            )
    for result in evidence.glossary_results:
        if any(
            not finding_by_id[finding_id].affected_source_cue_ids
            or not set(finding_by_id[finding_id].affected_source_cue_ids)
            <= set(result.source_cue_ids)
            for finding_id in result.finding_ids
        ):
            _fail(
                PreflightGateCode.COVERAGE_INVALID,
                "glossary finding scope does not match its occurrences",
            )
    _, target_result = evidence.semantic_results
    target_finding_ids = (
        *target_result.blocking_finding_ids,
        *target_result.advisory_finding_ids,
    )
    if any(
        finding_by_id[finding_id].affected_source_cue_ids
        or not finding_by_id[finding_id].affected_target_cue_ids
        for finding_id in target_finding_ids
    ):
        _fail(
            PreflightGateCode.COVERAGE_INVALID,
            "target-only finding scope is not target-only",
        )


def _validate_target_requirements(
    context: CandidatePreflightContext,
    evidence: PreflightCheckerEvidence,
) -> None:
    known_sources = {cue.cue_id for cue in context.source.cues}
    entries = {entry.entry_id for entry in context.glossary.entries}
    requirement_scopes = tuple(
        (item.glossary_entry_id, source_id)
        for item in evidence.target_requirements
        for source_id in item.source_cue_ids
    )
    if len(requirement_scopes) != len(set(requirement_scopes)):
        _fail(
            PreflightGateCode.COVERAGE_DUPLICATE,
            "glossary target requirement scope is duplicated",
        )
    source_by_id = {cue.cue_id: cue for cue in context.source.cues}
    for requirement in evidence.target_requirements:
        if (
            requirement.glossary_entry_id not in entries
            or not set(requirement.source_cue_ids) <= known_sources
        ):
            _fail(
                PreflightGateCode.COVERAGE_INVALID,
                "glossary target requirement scope is foreign",
            )
        if any(
            requirement.glossary_entry_id
            not in {
                entry.entry_id
                for entry in resolve_glossary_for_cue(
                    source_by_id[source_id],
                    context.glossary,
                )
            }
            for source_id in requirement.source_cue_ids
        ):
            _fail(
                PreflightGateCode.COVERAGE_INVALID,
                "glossary target requirement is not effective for its source cue",
            )


def _validate_evidence(
    context: CandidatePreflightContext,
    evidence: PreflightCheckerEvidence,
) -> None:
    adjacent = tuple(
        (item.left_target_cue_id, item.right_target_cue_id)
        for item in evidence.adjacent_results
    )
    glossary = tuple(
        (item.glossary_entry_id, item.source_cue_ids)
        for item in evidence.glossary_results
    )
    _coverage_code(adjacent, _expected_adjacent(context))
    _coverage_code(glossary, _expected_glossary(context))
    _validate_semantic_qualifications(context, evidence.semantic_results)
    _validate_finding_references(context, evidence)
    _validate_target_requirements(context, evidence)


def _finding_id(code: str, target_ids: tuple[str, ...]) -> str:
    return f"preflight:{canonical_sha256((code, target_ids))}"


def _display_findings(
    context: CandidatePreflightContext,
) -> tuple[
    list[OverLineCountFinding],
    list[OverLineWidthFinding],
    list[OverCpsFinding],
]:
    over_line_count: list[OverLineCountFinding] = []
    over_line_width: list[OverLineWidthFinding] = []
    over_cps: list[OverCpsFinding] = []
    for cue in context.candidate.cues:
        lines = cue.text.split("\n")
        if len(lines) > DISPLAY_LINE_LIMIT:
            over_line_count.append(
                OverLineCountFinding(
                    finding_id=_finding_id("over_line_count", (cue.cue_id,)),
                    target_cue_id=cue.cue_id,
                    line_count=len(lines),
                )
            )
        for line_number, line in enumerate(lines, start=1):
            if len(line) > DISPLAY_CHARACTER_LIMIT:
                over_line_width.append(
                    OverLineWidthFinding(
                        finding_id=_finding_id(
                            f"over_line_width:{line_number}",
                            (cue.cue_id,),
                        ),
                        target_cue_id=cue.cue_id,
                        line_number=line_number,
                        visible_characters=len(line),
                    )
                )
        visible = len(cue.text.replace("\n", ""))
        duration = cue.end_ms - cue.start_ms
        if visible * 1000 > 20 * duration:
            over_cps.append(
                OverCpsFinding(
                    finding_id=_finding_id("over_cps", (cue.cue_id,)),
                    target_cue_id=cue.cue_id,
                    visible_characters=visible,
                    duration_ms=duration,
                )
            )
    return over_line_count, over_line_width, over_cps


def _glossary_findings(
    context: CandidatePreflightContext,
    requirements: tuple[GlossaryTargetRequirement, ...],
) -> list[MissingGlossaryTargetFinding]:
    findings: list[MissingGlossaryTargetFinding] = []
    targets_by_source: dict[str, list[TargetCueV2]] = {}
    for target in context.candidate.cues:
        for source_id in target.source_cue_ids:
            targets_by_source.setdefault(source_id, []).append(target)
    for requirement in requirements:
        for source_id in requirement.source_cue_ids:
            linked = tuple(targets_by_source.get(source_id, ()))
            accepted = (
                requirement.accepted_targets
                if requirement.case_sensitive
                else tuple(item.casefold() for item in requirement.accepted_targets)
            )
            if any(
                any(
                    item
                    in (
                        target.text
                        if requirement.case_sensitive
                        else target.text.casefold()
                    )
                    for item in accepted
                )
                for target in linked
            ):
                continue
            target_ids = tuple(target.cue_id for target in linked)
            findings.append(
                MissingGlossaryTargetFinding(
                    finding_id=_finding_id(
                        f"missing_glossary_target:{requirement.requirement_id}",
                        (source_id, *target_ids),
                    ),
                    requirement_id=requirement.requirement_id,
                    glossary_entry_id=requirement.glossary_entry_id,
                    source_cue_id=source_id,
                    linked_target_cue_ids=target_ids,
                    accepted_targets=requirement.accepted_targets,
                )
            )
    return findings


def build_preflight_report(
    context: CandidatePreflightContext,
    evidence: PreflightCheckerEvidence,
) -> PreflightReport:
    """Build one deterministic report without changing any input artifact."""
    _validate_context(context)
    _validate_evidence(context, evidence)
    over_line_count, over_line_width, over_cps = _display_findings(context)
    missing_glossary_target = _glossary_findings(
        context,
        evidence.target_requirements,
    )
    duplicates: list[AdjacentDuplicateFinding] = []
    for left, right in zip(
        context.candidate.cues,
        context.candidate.cues[1:],
        strict=False,
    ):
        left_text = " ".join(left.text.split()).casefold()
        right_text = " ".join(right.text.split()).casefold()
        if left_text == right_text:
            target_ids = (left.cue_id, right.cue_id)
            duplicates.append(
                AdjacentDuplicateFinding(
                    finding_id=_finding_id("adjacent_duplicate", target_ids),
                    target_cue_ids=target_ids,
                    normalized_text=left_text,
                )
            )
    candidate = context.candidate
    source = context.source
    glossary = context.glossary
    return PreflightReport(
        translation_document_id=candidate.document_id,
        translation_revision_id=candidate.revision_id,
        candidate_content_digest=candidate.content_digest,
        source_document_id=source.document_id,
        source_revision_id=source.revision_id,
        source_content_digest=source.content_digest,
        glossary_asset_id=glossary.asset_id,
        glossary_revision_id=glossary.revision_id,
        glossary_content_digest=glossary.content_digest,
        over_line_count_findings=tuple(over_line_count),
        over_line_width_findings=tuple(over_line_width),
        missing_glossary_target_findings=tuple(missing_glossary_target),
        over_cps_findings=tuple(over_cps),
        duplicate_findings=tuple(duplicates),
        adjacent_results=evidence.adjacent_results,
        glossary_results=evidence.glossary_results,
        target_requirements=evidence.target_requirements,
        semantic_results=evidence.semantic_results,
        checker_findings=evidence.findings,
    )


def validate_preflight_report(
    context: CandidatePreflightContext,
    report: PreflightReport,
) -> None:
    """Require one current, complete report without duplicate subtitles."""
    try:
        report = PreflightReport.model_validate_json(report.model_dump_json())
    except ValidationError as error:
        if any(item["loc"] == ("profile_version",) for item in error.errors()):
            _fail(
                PreflightGateCode.PROFILE_STALE,
                "preflight profile version is stale",
            )
        _fail(PreflightGateCode.COVERAGE_INVALID, str(error))
    if (
        report.translation_document_id != context.candidate.document_id
        or report.translation_revision_id != context.candidate.revision_id
        or report.candidate_content_digest != context.candidate.content_digest
    ):
        _fail(PreflightGateCode.CANDIDATE_STALE, "preflight candidate binding is stale")
    try:
        evidence = PreflightCheckerEvidence(
            adjacent_results=report.adjacent_results,
            glossary_results=report.glossary_results,
            target_requirements=report.target_requirements,
            semantic_results=report.semantic_results,
            findings=report.checker_findings,
        )
    except ValidationError as error:
        _fail(PreflightGateCode.COVERAGE_DUPLICATE, str(error))
    expected = build_preflight_report(context, evidence)
    if expected != report:
        _fail(PreflightGateCode.CANDIDATE_STALE, "preflight report content is stale")
    if (
        report.over_line_count_findings
        or report.over_line_width_findings
        or any(item.blocking for item in report.missing_glossary_target_findings)
        or any(item.blocking_finding_ids for item in report.semantic_results)
    ):
        _fail(
            PreflightGateCode.BLOCKING_FINDINGS,
            "candidate contains blocking preflight findings",
        )


def select_preflighted_attempt(
    store: TranslationAttemptStore,
    selection: PreflightedSelection,
) -> Path:
    """Validate preflight, then delegate selection to the verified Todo 4 store."""
    return store.persist_preflighted_selection(selection)


__all__ = [
    "PREFLIGHT_PROFILE_VERSION",
    "AdjacentCoverageResult",
    "CandidatePreflightContext",
    "CheckerFinding",
    "GlossaryCoverageResult",
    "GlossaryTargetRequirement",
    "MissingGlossaryTargetFinding",
    "OverLineCountFinding",
    "OverLineWidthFinding",
    "PreflightCheckerEvidence",
    "PreflightGateCode",
    "PreflightGateError",
    "PreflightReport",
    "PreflightedSelection",
    "SemanticQualificationKind",
    "SemanticQualificationResult",
    "build_preflight_report",
    "select_preflighted_attempt",
    "validate_preflight_report",
    "write_preflight_report",
]
