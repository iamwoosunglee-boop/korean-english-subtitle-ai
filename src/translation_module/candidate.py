# Copyright 2026 Transcription Module contributors

"""Transient translation chunk results and deterministic document assembly."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Never

from pydantic import Field, ValidationError

from translation_module.common import FrozenModel, Sha256Digest, SourceCueId
from translation_module.document import (
    DocumentStatus,
    RetainedContext,
    TargetCue,
    TranslationDocument,
)
from translation_module.preparation import (
    ChunkPlan,
    source_digest,
    validate_batch_plan,
)
from translation_module.preparation_errors import PreparationError
from translation_module.source import SourceSnapshot
from translation_module.validation import (
    TranslationValidationError,
    bind_translation_digest,
    validate_translation,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class CandidateAssemblyError(ValueError):
    """Chunk results cannot form one valid translation document."""


def _fail(message: str) -> Never:
    raise CandidateAssemblyError(message)


class ChunkCandidate(FrozenModel):
    """Transient agent result bound to one source and owned chunk."""

    source_digest: Sha256Digest
    chunk_id: str = Field(min_length=1)
    owned_source_cue_ids: tuple[SourceCueId, ...] = Field(min_length=1)
    cues: tuple[TargetCue, ...] = Field(min_length=1)
    retained_contexts: tuple[RetainedContext, ...] = ()


def assemble_candidate(
    source: SourceSnapshot,
    plans: Sequence[ChunkPlan],
    candidates: Sequence[ChunkCandidate],
    *,
    document_id: str,
) -> TranslationDocument:
    """Validate complete chunk output and return one canonical document."""
    source, batch, result_by_chunk = _validated_inputs(source, plans, candidates)
    ordered_cues, contexts = _collect_outputs(source, batch, result_by_chunk)
    resequenced = _resequence_output(source, ordered_cues)
    return _build_document(source, document_id, contexts, resequenced)


def _validated_inputs(
    source: SourceSnapshot,
    plans: Sequence[ChunkPlan],
    candidates: Sequence[ChunkCandidate],
) -> tuple[
    SourceSnapshot,
    tuple[ChunkPlan, ...],
    dict[str, ChunkCandidate],
]:
    try:
        source = SourceSnapshot.model_validate(source.model_dump())
    except ValidationError as error:
        raise CandidateAssemblyError(str(error)) from error
    if source_digest(source) != source.content_digest:
        _fail("source snapshot digest mismatch")
    try:
        batch = validate_batch_plan(source, plans)
    except PreparationError as error:
        raise CandidateAssemblyError(str(error)) from error

    results = tuple(candidates)
    expected_chunks = tuple(plan.chunk_id for plan in batch)
    actual_chunks = tuple(result.chunk_id for result in results)
    foreign_chunks = tuple(
        chunk for chunk in actual_chunks if chunk not in expected_chunks
    )
    missing_chunks = tuple(
        chunk for chunk in expected_chunks if chunk not in actual_chunks
    )
    if foreign_chunks:
        _fail(f"foreign chunk: {foreign_chunks}")
    if missing_chunks:
        _fail(f"missing chunk: {missing_chunks}")
    if len(actual_chunks) != len(set(actual_chunks)):
        _fail("duplicate chunk output")
    return source, batch, {result.chunk_id: result for result in results}


def _collect_outputs(
    source: SourceSnapshot,
    batch: tuple[ChunkPlan, ...],
    result_by_chunk: dict[str, ChunkCandidate],
) -> tuple[list[TargetCue], dict[str, RetainedContext]]:
    source_ids = {cue.cue_id for cue in source.cues}
    ordered_cues: list[TargetCue] = []
    contexts: dict[str, RetainedContext] = {}
    for plan in batch:
        result = result_by_chunk[plan.chunk_id]
        if result.source_digest != source.content_digest:
            _fail(f"source digest mismatch for chunk {plan.chunk_id}")
        if result.owned_source_cue_ids != plan.owned_source_cue_ids:
            _fail(f"ownership mismatch for chunk {plan.chunk_id}")
        allowed = {
            *plan.context_before_source_cue_ids,
            *plan.owned_source_cue_ids,
            *plan.context_after_source_cue_ids,
        }
        owned = set(plan.owned_source_cue_ids)
        for cue in result.cues:
            lineage = set(cue.source_cue_ids)
            if lineage - source_ids or lineage - allowed:
                _fail(f"foreign source output in chunk {plan.chunk_id}")
            if not lineage & owned:
                _fail(f"context-only output in chunk {plan.chunk_id}")
            ordered_cues.append(cue)
        for context in result.retained_contexts:
            stable_id = str(context.context_id)
            previous = contexts.get(stable_id)
            if previous is not None and previous != context:
                _fail(f"retained context {stable_id} has conflicting values")
            _ = contexts.setdefault(stable_id, context)
    return ordered_cues, contexts


def _resequence_output(
    source: SourceSnapshot,
    ordered_cues: list[TargetCue],
) -> tuple[TargetCue, ...]:
    output_counts = Counter(
        source_id for cue in ordered_cues for source_id in cue.source_cue_ids
    )
    missing_output = tuple(
        cue.cue_id for cue in source.cues if output_counts[cue.cue_id] == 0
    )
    if missing_output:
        _fail(f"missing owned output: {missing_output}")

    target_ids = tuple(cue.cue_id for cue in ordered_cues)
    if len(target_ids) != len(set(target_ids)):
        _fail("target IDs must be unique")
    source_positions = {
        cue.cue_id: position for position, cue in enumerate(source.cues)
    }
    ordered_cues.sort(
        key=lambda cue: (
            min(source_positions[source_id] for source_id in cue.source_cue_ids),
            cue.start_ms,
            cue.end_ms,
            str(cue.cue_id),
        )
    )
    return tuple(
        cue.model_copy(update={"sequence": sequence})
        for sequence, cue in enumerate(ordered_cues, start=1)
    )


def _build_document(
    source: SourceSnapshot,
    document_id: str,
    contexts: dict[str, RetainedContext],
    resequenced: tuple[TargetCue, ...],
) -> TranslationDocument:
    try:
        document = bind_translation_digest(
            TranslationDocument(
                document_id=document_id,
                content_digest="0" * 64,
                source_document_id=source.document_id,
                source_digest=source.content_digest,
                source_locale=source.source_locale,
                target_locale="en-US",
                context=source.context,
                retained_contexts=tuple(contexts.values()),
                cues=resequenced,
                status=DocumentStatus.FINALIZED,
            )
        )
        validate_translation(source, document)
    except (ValueError, TranslationValidationError) as error:
        raise CandidateAssemblyError(str(error)) from error
    return document


__all__ = ["CandidateAssemblyError", "ChunkCandidate", "assemble_candidate"]
