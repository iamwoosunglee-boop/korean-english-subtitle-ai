# Copyright 2026 Transcription Module contributors

"""Deterministic validation for published translation documents."""

from collections.abc import Set as AbstractSet

from translation_module.common import EvidenceId, SourceCueId
from translation_module.document import (
    RetainedContext,
    TargetCue,
    TranslationDocument,
    UnresolvedItem,
    translation_digest,
)
from translation_module.lineage_validation import (
    lineage_issues,
    repeated_lineage_issues,
)
from translation_module.preparation import source_digest
from translation_module.source import SourceCue, SourceSnapshot

MAX_CHARACTERS_PER_LINE = 42
MAX_CHARACTERS_PER_SECOND = 20
MAX_LINES = 2


class TranslationValidationError(Exception):
    """Translation document violates one or more deterministic contracts."""

    issues: tuple[str, ...]

    def __init__(self, issues: tuple[str, ...]) -> None:
        """Retain every issue in deterministic discovery order."""
        super().__init__("; ".join(issues))
        self.issues = issues


def bind_translation_digest(
    document: TranslationDocument,
) -> TranslationDocument:
    """Return a translation document bound to its current canonical bytes."""
    return document.model_copy(update={"content_digest": translation_digest(document)})


def validate_translation(
    source: SourceSnapshot,
    document: TranslationDocument,
) -> None:
    """Raise once with every deterministic source/result contract violation."""
    issues = _validation_issues(source, document)
    if issues:
        raise TranslationValidationError(issues)


def _validation_issues(
    source: SourceSnapshot,
    document: TranslationDocument,
) -> tuple[str, ...]:
    issues = list(_identity_issues(source, document))
    cue_issues, referenced_source_ids = _cue_collection_issues(
        source,
        document,
    )
    issues.extend(cue_issues)
    missing_source_ids = tuple(
        cue.cue_id for cue in source.cues if cue.cue_id not in referenced_source_ids
    )
    if missing_source_ids:
        issues.append(f"unreferenced source cue: {missing_source_ids}")
    known_evidence = {entry.evidence_id for entry in source.evidence.entries}
    source_by_id = {cue.cue_id: cue for cue in source.cues}
    issues.extend(
        issue
        for context in document.retained_contexts
        for issue in _retained_context_issues(
            context,
            source_by_id,
            known_evidence,
        )
    )
    issues.extend(
        issue
        for item in document.unresolved
        for issue in _unresolved_issues(
            item,
            source_by_id,
            known_evidence,
        )
    )
    unresolved_ids = tuple(item.unresolved_id for item in document.unresolved)
    if len(unresolved_ids) != len(set(unresolved_ids)):
        issues.append("unresolved item IDs must be unique")
    return tuple(issues)


def _identity_issues(
    source: SourceSnapshot,
    document: TranslationDocument,
) -> tuple[str, ...]:
    issues: list[str] = []
    if source_digest(source) != source.content_digest:
        issues.append("source snapshot digest mismatch")
    if document.source_document_id != source.document_id:
        issues.append("source document ID mismatch")
    if document.source_digest != source.content_digest:
        issues.append("source digest does not match translation source")
    if document.context != source.context:
        issues.append("episode context mismatch")
    if translation_digest(document) != document.content_digest:
        issues.append("document digest mismatch")
    return tuple(issues)


def _cue_collection_issues(
    source: SourceSnapshot,
    document: TranslationDocument,
) -> tuple[tuple[str, ...], set[SourceCueId]]:
    issues: list[str] = []
    source_by_id = {cue.cue_id: cue for cue in source.cues}
    source_positions = {
        cue.cue_id: position for position, cue in enumerate(source.cues)
    }
    known_evidence = {entry.evidence_id for entry in source.evidence.entries}
    referenced_source_ids: set[SourceCueId] = set()
    expected_sequences = tuple(range(1, len(document.cues) + 1))
    actual_sequences = tuple(cue.sequence for cue in document.cues)
    if actual_sequences != expected_sequences:
        issues.append("target cue sequences are not contiguous")
    ordering = tuple((cue.start_ms, cue.end_ms, cue.sequence) for cue in document.cues)
    if ordering != tuple(sorted(ordering)):
        issues.append("target cues are not in display order")
    target_speakers: set[str] = set()
    for cue in document.cues:
        cue_result, known_source_ids = _target_cue_issues(
            cue,
            source_by_id,
            source_positions,
            known_evidence,
        )
        issues.extend(cue_result)
        referenced_source_ids.update(known_source_ids)
        target_speakers.update(cue.speakers)
    issues.extend(repeated_lineage_issues(document.cues))
    source_speakers = {speaker for cue in source.cues for speaker in cue.speakers}
    if source_speakers - target_speakers:
        issues.append("target cues do not provide aggregate source speaker coverage")
    return tuple(issues), referenced_source_ids


def _target_cue_issues(
    cue: TargetCue,
    source_by_id: dict[SourceCueId, SourceCue],
    source_positions: dict[SourceCueId, int],
    known_evidence: AbstractSet[EvidenceId],
) -> tuple[tuple[str, ...], tuple[SourceCueId, ...]]:
    issues: list[str] = []
    cue_prefix = f"target cue {cue.cue_id}"
    source_ids = tuple(cue.source_cue_ids)
    if len(source_ids) != len(set(source_ids)):
        issues.append(f"{cue_prefix} repeats source lineage")
    unknown_source_ids = tuple(
        source_id for source_id in source_ids if source_id not in source_by_id
    )
    if unknown_source_ids:
        issues.append(f"{cue_prefix} references unknown source cue")
    known_source_ids = tuple(
        source_id for source_id in source_ids if source_id in source_by_id
    )
    positions = tuple(source_positions[source_id] for source_id in known_source_ids)
    if positions != tuple(sorted(positions)):
        issues.append(f"{cue_prefix} violates source order")
    if known_source_ids and len(known_source_ids) == len(source_ids):
        source_cues = tuple(source_by_id[source_id] for source_id in known_source_ids)
        issues.extend(lineage_issues(cue, source_cues))
    if set(cue.evidence_ids) - known_evidence:
        issues.append(f"{cue_prefix} references unknown evidence")
    if _is_unreadable(cue.text, cue.start_ms, cue.end_ms):
        issues.append(f"{cue_prefix} exceeds readability limits")
    return tuple(issues), known_source_ids


def _retained_context_issues(
    context: RetainedContext,
    source_by_id: dict[SourceCueId, SourceCue],
    known_evidence: AbstractSet[EvidenceId],
) -> tuple[str, ...]:
    issues: list[str] = []
    if any(source_id not in source_by_id for source_id in context.source_cue_ids):
        issues.append(f"context {context.context_id} references unknown source cue")
    known_source_ids = tuple(
        source_id for source_id in context.source_cue_ids if source_id in source_by_id
    )
    positions = tuple(
        tuple(source_by_id).index(source_id) for source_id in known_source_ids
    )
    if positions != tuple(sorted(positions)):
        issues.append(f"context {context.context_id} violates source order")
    if not context.evidence_ids:
        issues.append(f"context {context.context_id} requires evidence")
    elif set(context.evidence_ids) - known_evidence:
        issues.append(f"context {context.context_id} references unknown evidence")
    return tuple(issues)


def _unresolved_issues(
    item: UnresolvedItem,
    source_by_id: dict[SourceCueId, SourceCue],
    known_evidence: AbstractSet[EvidenceId],
) -> tuple[str, ...]:
    issues: list[str] = []
    if set(item.source_cue_ids) - set(source_by_id):
        issues.append(f"unresolved item {item.unresolved_id} references unknown source")
    if set(item.evidence_ids) - known_evidence:
        issues.append(
            f"unresolved item {item.unresolved_id} references unknown evidence"
        )
    return tuple(issues)


def _is_unreadable(text: str, start_ms: int, end_ms: int) -> bool:
    lines = text.splitlines() or [text]
    if len(lines) > MAX_LINES:
        return True
    if any(len(line) > MAX_CHARACTERS_PER_LINE for line in lines):
        return True
    duration_seconds = (int(end_ms) - int(start_ms)) / 1_000
    character_count = len(text.replace("\n", ""))
    return character_count / duration_seconds > MAX_CHARACTERS_PER_SECOND
