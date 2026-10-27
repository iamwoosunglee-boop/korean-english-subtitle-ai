# Copyright 2026 Transcription Module contributors

"""Canonical translation glossary asset contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator

from translation_module.canonical.common import (
    AssetLifecycleStatus,
    CanonicalEvidenceManifest,
    CanonicalId,
    CanonicalModel,
    DocumentReference,
    validate_evidence_references,
    validate_unique_ids,
)
from translation_module.common import Sha256Digest  # noqa: TC001 - Pydantic runtime

if TYPE_CHECKING:
    from translation_module.canonical.source import SourceCueV2


def _is_none(value: object) -> bool:
    return value is None


class GlossaryDecisionStatus(StrEnum):
    """Authority level for one glossary decision."""

    VERIFIED = "verified"
    HUMAN_SELECTED = "human_selected"
    UNRESOLVED = "unresolved"


class GlossaryEntryType(StrEnum):
    """Closed translation glossary category."""

    PERSON_NAME = "person_name"
    NICKNAME = "nickname"
    WORK_TITLE = "work_title"
    SONG_TITLE = "song_title"
    ORGANIZATION = "organization"
    PLACE = "place"
    FOOD = "food"
    TECHNICAL_TERM = "technical_term"
    PHONETIC_EXPRESSION = "phonetic_expression"
    WORDPLAY = "wordplay"
    CONTEXTUAL_TRANSLATION = "contextual_translation"


class GlossaryEntry(CanonicalModel):
    """One scoped and evidence-aware translation instruction."""

    entry_id: CanonicalId
    source_variants: tuple[CanonicalId, ...] = Field(min_length=1)
    preferred_target: str | None = Field(
        default=None,
        min_length=1,
        exclude_if=_is_none,
    )
    type: GlossaryEntryType
    instruction: str | None = Field(default=None, min_length=1, exclude_if=_is_none)
    scope_source_cue_ids: tuple[CanonicalId, ...]
    decision_status: GlossaryDecisionStatus
    blocking: bool
    case_sensitive: bool
    evidence_ids: tuple[CanonicalId, ...]
    note: str | None = Field(default=None, min_length=1, exclude_if=_is_none)

    @model_validator(mode="after")
    def entry_is_structurally_consistent(self) -> Self:
        """Validate entry variants, scope, and resolved target state."""
        if len(self.source_variants) != len(set(self.source_variants)):
            message = "glossary source variants must be unique"
            raise ValueError(message)
        if len(self.scope_source_cue_ids) != len(set(self.scope_source_cue_ids)):
            message = "glossary source scope IDs must be unique"
            raise ValueError(message)
        if (
            self.decision_status
            in {
                GlossaryDecisionStatus.VERIFIED,
                GlossaryDecisionStatus.HUMAN_SELECTED,
            }
            and self.preferred_target is None
        ):
            message = "resolved glossary entry requires preferred_target"
            raise ValueError(message)
        return self


@dataclass(frozen=True, slots=True)
class GlossaryConflict:
    """Two contradictory glossary entries with the same effective priority."""

    entry_ids: tuple[str, str]
    source_variants: tuple[str, ...]
    scope_source_cue_ids: tuple[str, ...]


def _entry_priority(entry: GlossaryEntry) -> tuple[int, int]:
    specificity = 1 if entry.scope_source_cue_ids else 0
    authority = (
        1 if entry.decision_status is GlossaryDecisionStatus.HUMAN_SELECTED else 0
    )
    return specificity, authority


def _is_resolved(entry: GlossaryEntry) -> bool:
    return entry.decision_status is not GlossaryDecisionStatus.UNRESOLVED


def _variants_overlap(left: GlossaryEntry, right: GlossaryEntry) -> tuple[str, ...]:
    shared: list[str] = []
    for left_variant in left.source_variants:
        for right_variant in right.source_variants:
            equal = (
                left_variant == right_variant
                if left.case_sensitive and right.case_sensitive
                else left_variant.casefold() == right_variant.casefold()
            )
            if equal and left_variant not in shared:
                shared.append(left_variant)
    return tuple(shared)


def _scope_overlap(
    left: GlossaryEntry,
    right: GlossaryEntry,
) -> tuple[bool, tuple[str, ...]]:
    left_scope = left.scope_source_cue_ids
    right_scope = right.scope_source_cue_ids
    if not left_scope and not right_scope:
        return True, ()
    if not left_scope:
        return True, right_scope
    if not right_scope:
        return True, left_scope
    overlap = tuple(source_id for source_id in left_scope if source_id in right_scope)
    return bool(overlap), overlap


def glossary_conflicts(
    glossary: TranslationGlossaryAsset,
) -> tuple[GlossaryConflict, ...]:
    """Return deterministic same-priority directive conflicts."""
    conflicts: list[GlossaryConflict] = []
    resolved = tuple(entry for entry in glossary.entries if _is_resolved(entry))
    for left, right in combinations(resolved, 2):
        if _entry_priority(left) != _entry_priority(right):
            continue
        variants = _variants_overlap(left, right)
        if not variants:
            continue
        overlaps, scope = _scope_overlap(left, right)
        if not overlaps:
            continue
        directives = {
            (left.preferred_target, left.instruction),
            (right.preferred_target, right.instruction),
        }
        if len(directives) == 1:
            continue
        entry_ids = (
            (left.entry_id, right.entry_id)
            if left.entry_id < right.entry_id
            else (right.entry_id, left.entry_id)
        )
        conflicts.append(
            GlossaryConflict(
                entry_ids=entry_ids,
                source_variants=variants,
                scope_source_cue_ids=scope,
            )
        )
    return tuple(conflicts)


def _entry_matches_cue(entry: GlossaryEntry, cue: SourceCueV2) -> bool:
    if entry.scope_source_cue_ids and cue.cue_id not in entry.scope_source_cue_ids:
        return False
    text = cue.text if entry.case_sensitive else cue.text.casefold()
    return any(
        (variant if entry.case_sensitive else variant.casefold()) in text
        for variant in entry.source_variants
    )


def _connected_components(
    entries: tuple[GlossaryEntry, ...],
) -> tuple[tuple[int, ...], ...]:
    remaining = set(range(len(entries)))
    components: list[tuple[int, ...]] = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        component = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            connected = {
                index
                for index in remaining
                if _variants_overlap(entries[current], entries[index])
            }
            remaining.difference_update(connected)
            component.update(connected)
            frontier.extend(connected)
        components.append(tuple(sorted(component)))
    return tuple(components)


def resolve_glossary_for_cue(
    cue: SourceCueV2,
    glossary: TranslationGlossaryAsset,
) -> tuple[GlossaryEntry, ...]:
    """Return the effective resolved glossary entries for one source cue."""
    matched = tuple(
        entry
        for entry in glossary.entries
        if _is_resolved(entry) and _entry_matches_cue(entry, cue)
    )
    selected_ids: set[str] = set()
    for component in _connected_components(matched):
        candidates = tuple(matched[index] for index in component)
        best_priority = max(_entry_priority(entry) for entry in candidates)
        winners = tuple(
            entry for entry in candidates if _entry_priority(entry) == best_priority
        )
        directives = {(entry.preferred_target, entry.instruction) for entry in winners}
        if len(directives) != 1:
            ids = ", ".join(entry.entry_id for entry in winners)
            message = f"glossary conflict for cue {cue.cue_id}: {ids}"
            raise ValueError(message)
        selected_ids.add(min(winners, key=lambda entry: entry.entry_id).entry_id)
    return tuple(entry for entry in glossary.entries if entry.entry_id in selected_ids)


class TranslationGlossaryAsset(CanonicalModel):
    """Versioned glossary prepared from one source revision."""

    schema_version: Literal["translation-glossary.v1"] = "translation-glossary.v1"
    asset_id: CanonicalId
    revision_id: CanonicalId
    supersedes_revision_id: CanonicalId | None = Field(
        default=None,
        exclude_if=_is_none,
    )
    content_digest: Sha256Digest
    record_digest: Sha256Digest
    lifecycle_status: AssetLifecycleStatus
    prepared_from_source_ref: DocumentReference
    evidence: CanonicalEvidenceManifest
    entries: tuple[GlossaryEntry, ...]

    @model_validator(mode="after")
    def glossary_is_structurally_consistent(self) -> Self:
        """Validate stable entry IDs and local evidence references."""
        validate_unique_ids(
            tuple(entry.entry_id for entry in self.entries),
            "glossary entry",
        )
        validate_evidence_references(
            tuple(
                evidence_id
                for entry in self.entries
                for evidence_id in entry.evidence_ids
            ),
            self.evidence,
            "glossary entries",
        )
        return self


__all__ = [
    "GlossaryConflict",
    "GlossaryDecisionStatus",
    "GlossaryEntry",
    "GlossaryEntryType",
    "TranslationGlossaryAsset",
    "glossary_conflicts",
    "resolve_glossary_for_cue",
]
