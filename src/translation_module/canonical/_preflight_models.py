# Copyright 2026 Transcription Module contributors

"""Strict immutable models for deterministic candidate preflight."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal, Self, override

from pydantic import Field, model_validator

from translation_module.canonical.common import CanonicalId, CanonicalModel
from translation_module.common import Sha256Digest  # noqa: TC001 - Pydantic runtime

if TYPE_CHECKING:
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import TranslationDocumentV2

PREFLIGHT_PROFILE_VERSION: Final = "en-us-semantic-qualification.v5"


class PreflightGateCode(StrEnum):
    """Stable reason selection or report construction failed closed."""

    INPUT_INVALID = "input_invalid"
    CANDIDATE_STALE = "candidate_stale"
    PROFILE_STALE = "profile_stale"
    COVERAGE_MISSING = "coverage_missing"
    COVERAGE_DUPLICATE = "coverage_duplicate"
    COVERAGE_INVALID = "coverage_invalid"
    BLOCKING_FINDINGS = "blocking_findings"
    REPORT_CONFLICT = "report_conflict"


@dataclass(frozen=True, slots=True)
class PreflightGateError(ValueError):
    """Typed failure at the preflight construction or selection boundary."""

    code: PreflightGateCode
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"


@dataclass(frozen=True, slots=True)
class CandidatePreflightContext:
    """Exact candidate, source, and glossary graph inspected by preflight."""

    candidate: TranslationDocumentV2
    source: SourceTranscriptV2
    glossary: TranslationGlossaryAsset


class AdjacentCoverageResult(CanonicalModel):
    """One checker result for one ordered adjacent target boundary."""

    left_target_cue_id: CanonicalId
    right_target_cue_id: CanonicalId
    finding_ids: tuple[CanonicalId, ...]

    @model_validator(mode="after")
    def boundary_is_unambiguous(self) -> Self:
        if self.left_target_cue_id == self.right_target_cue_id:
            message = "adjacent coverage requires distinct target cues"
            raise ValueError(message)
        if len(self.finding_ids) != len(set(self.finding_ids)):
            message = "adjacent coverage finding IDs must be unique"
            raise ValueError(message)
        return self


class GlossaryCoverageResult(CanonicalModel):
    """One checker result for one repeated effective glossary entry."""

    glossary_entry_id: CanonicalId
    source_cue_ids: tuple[CanonicalId, ...] = Field(min_length=2)
    finding_ids: tuple[CanonicalId, ...]

    @model_validator(mode="after")
    def coverage_ids_are_unique(self) -> Self:
        if len(self.source_cue_ids) != len(set(self.source_cue_ids)):
            message = "glossary coverage source cue IDs must be unique"
            raise ValueError(message)
        if len(self.finding_ids) != len(set(self.finding_ids)):
            message = "glossary coverage finding IDs must be unique"
            raise ValueError(message)
        return self


class GlossaryTargetRequirement(CanonicalModel):
    """Explicit literal target forms required for selected source occurrences."""

    requirement_id: CanonicalId
    glossary_entry_id: CanonicalId
    source_cue_ids: tuple[CanonicalId, ...] = Field(min_length=1)
    accepted_targets: tuple[str, ...] = Field(min_length=1)
    case_sensitive: bool

    @model_validator(mode="after")
    def identities_and_forms_are_unique(self) -> Self:
        if len(self.source_cue_ids) != len(set(self.source_cue_ids)):
            message = "glossary target requirement source IDs must be unique"
            raise ValueError(message)
        if len(self.accepted_targets) != len(set(self.accepted_targets)):
            message = "glossary target requirement forms must be unique"
            raise ValueError(message)
        return self


class SemanticQualificationKind(StrEnum):
    """Independent semantic passes required before candidate selection."""

    SOURCE_CLAIMS = "source_claims"
    TARGET_ONLY = "target_only"


class SemanticQualificationResult(CanonicalModel):
    """Exact scope and findings from one independent semantic pass."""

    kind: SemanticQualificationKind
    source_cue_ids: tuple[CanonicalId, ...]
    target_cue_ids: tuple[CanonicalId, ...]
    reviewer_execution_ids: tuple[CanonicalId, ...] = Field(min_length=1)
    blocking_finding_ids: tuple[CanonicalId, ...]
    advisory_finding_ids: tuple[CanonicalId, ...]

    @model_validator(mode="after")
    def coverage_ids_are_unique(self) -> Self:
        if len(self.source_cue_ids) != len(set(self.source_cue_ids)):
            message = "semantic qualification source IDs must be unique"
            raise ValueError(message)
        if len(self.target_cue_ids) != len(set(self.target_cue_ids)):
            message = "semantic qualification target IDs must be unique"
            raise ValueError(message)
        if len(self.reviewer_execution_ids) != len(set(self.reviewer_execution_ids)):
            message = "semantic qualification execution IDs must be unique"
            raise ValueError(message)
        finding_ids = (*self.blocking_finding_ids, *self.advisory_finding_ids)
        if len(finding_ids) != len(set(finding_ids)):
            message = "semantic qualification finding ownership must be unique"
            raise ValueError(message)
        return self


class CheckerFinding(CanonicalModel):
    """Semantic checker observation without selection-control fields."""

    finding_id: CanonicalId
    affected_source_cue_ids: tuple[CanonicalId, ...]
    affected_target_cue_ids: tuple[CanonicalId, ...]
    problem: str = Field(min_length=1)
    required_change: str = Field(min_length=1)

    @model_validator(mode="after")
    def finding_has_scope(self) -> Self:
        if not self.affected_source_cue_ids and not self.affected_target_cue_ids:
            message = "checker finding requires source or target scope"
            raise ValueError(message)
        return self


class PreflightCheckerEvidence(CanonicalModel):
    """Complete typed semantic checker output supplied to preflight."""

    adjacent_results: tuple[AdjacentCoverageResult, ...]
    glossary_results: tuple[GlossaryCoverageResult, ...]
    target_requirements: tuple[GlossaryTargetRequirement, ...] = ()
    semantic_results: tuple[SemanticQualificationResult, ...]
    findings: tuple[CheckerFinding, ...]

    @model_validator(mode="after")
    def identities_are_unique(self) -> Self:
        adjacent = tuple(
            (item.left_target_cue_id, item.right_target_cue_id)
            for item in self.adjacent_results
        )
        glossary = tuple(item.glossary_entry_id for item in self.glossary_results)
        requirements = tuple(item.requirement_id for item in self.target_requirements)
        semantic_kinds = tuple(item.kind for item in self.semantic_results)
        findings = tuple(item.finding_id for item in self.findings)
        if len(adjacent) != len(set(adjacent)):
            message = "adjacent coverage results must be unique"
            raise ValueError(message)
        if len(glossary) != len(set(glossary)):
            message = "glossary coverage results must be unique"
            raise ValueError(message)
        if len(requirements) != len(set(requirements)):
            message = "glossary target requirement IDs must be unique"
            raise ValueError(message)
        if len(semantic_kinds) != len(set(semantic_kinds)):
            message = "semantic qualification kinds must be unique"
            raise ValueError(message)
        if len(findings) != len(set(findings)):
            message = "checker finding IDs must be unique"
            raise ValueError(message)
        return self


class OverCpsFinding(CanonicalModel):
    """Warning for one cue above the fixed CPS target."""

    code: Literal["over_cps"] = "over_cps"
    finding_id: CanonicalId
    blocking: Literal[False] = False
    target_cue_id: CanonicalId
    visible_characters: int = Field(ge=1, strict=True)
    duration_ms: int = Field(gt=0, strict=True)


class OverLineCountFinding(CanonicalModel):
    """Blocking finding for a cue that cannot fit the display line limit."""

    code: Literal["over_line_count"] = "over_line_count"
    finding_id: CanonicalId
    blocking: Literal[True] = True
    target_cue_id: CanonicalId
    line_count: int = Field(gt=2, strict=True)
    line_limit: Literal[2] = 2


class OverLineWidthFinding(CanonicalModel):
    """Blocking finding for one displayed line wider than the fixed limit."""

    code: Literal["over_line_width"] = "over_line_width"
    finding_id: CanonicalId
    blocking: Literal[True] = True
    target_cue_id: CanonicalId
    line_number: int = Field(ge=1, strict=True)
    visible_characters: int = Field(gt=42, strict=True)
    character_limit: Literal[42] = 42


class MissingGlossaryTargetFinding(CanonicalModel):
    """One explicit accepted target absent from its linked target cues."""

    code: Literal["missing_glossary_target"] = "missing_glossary_target"
    finding_id: CanonicalId
    blocking: Literal[True] = True
    requirement_id: CanonicalId
    glossary_entry_id: CanonicalId
    source_cue_id: CanonicalId
    linked_target_cue_ids: tuple[CanonicalId, ...]
    accepted_targets: tuple[str, ...] = Field(min_length=1)


class AdjacentDuplicateFinding(CanonicalModel):
    """Warning for exact normalized adjacent target text."""

    code: Literal["adjacent_exact_normalized_duplicate"] = (
        "adjacent_exact_normalized_duplicate"
    )
    finding_id: CanonicalId
    blocking: Literal[False] = False
    target_cue_ids: tuple[CanonicalId, CanonicalId]
    normalized_text: str = Field(min_length=1)


class PreflightReport(CanonicalModel):
    """Immutable candidate-bound translation-preflight.v1 sidecar."""

    schema_version: Literal["translation-preflight.v1"] = "translation-preflight.v1"
    profile_version: Literal["en-us-semantic-qualification.v5"] = (
        PREFLIGHT_PROFILE_VERSION
    )
    translation_document_id: CanonicalId
    translation_revision_id: CanonicalId
    candidate_content_digest: Sha256Digest
    source_document_id: CanonicalId
    source_revision_id: CanonicalId
    source_content_digest: Sha256Digest
    glossary_asset_id: CanonicalId
    glossary_revision_id: CanonicalId
    glossary_content_digest: Sha256Digest
    over_line_count_findings: tuple[OverLineCountFinding, ...]
    over_line_width_findings: tuple[OverLineWidthFinding, ...]
    missing_glossary_target_findings: tuple[MissingGlossaryTargetFinding, ...]
    over_cps_findings: tuple[OverCpsFinding, ...]
    duplicate_findings: tuple[AdjacentDuplicateFinding, ...]
    adjacent_results: tuple[AdjacentCoverageResult, ...]
    glossary_results: tuple[GlossaryCoverageResult, ...]
    target_requirements: tuple[GlossaryTargetRequirement, ...]
    semantic_results: tuple[SemanticQualificationResult, ...]
    checker_findings: tuple[CheckerFinding, ...]
