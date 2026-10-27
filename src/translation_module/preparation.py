# Copyright 2026 Transcription Module contributors

"""Prepare digest-bound, context-rich translation requests."""

import hashlib
import json
from collections.abc import Sequence
from typing import Self

from pydantic import Field, model_validator

from translation_module.common import (
    EpisodeContext,
    EvidenceId,
    FrozenModel,
    LocaleCode,
    Sha256Digest,
    SourceCueId,
)
from translation_module.evidence_preparation import EvidenceResolver, preflight_evidence
from translation_module.preparation_errors import PreparationError
from translation_module.source import (
    EvidenceEntry,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)

MAX_OWNED_CUES_PER_CHUNK = 64
MAX_CONTEXT_CUES_PER_SIDE = 64


class ChunkPlan(FrozenModel):
    """Agent-selected owned cue range plus read-only boundary context."""

    chunk_id: str = Field(min_length=1)
    owned_source_cue_ids: tuple[SourceCueId, ...] = Field(
        min_length=1,
        max_length=MAX_OWNED_CUES_PER_CHUNK,
    )
    context_before_source_cue_ids: tuple[SourceCueId, ...] = ()
    context_after_source_cue_ids: tuple[SourceCueId, ...] = ()

    @model_validator(mode="after")
    def cue_roles_are_disjoint(self) -> Self:
        """Prevent context cues from becoming duplicate owned output."""
        owned = set(self.owned_source_cue_ids)
        before = set(self.context_before_source_cue_ids)
        after = set(self.context_after_source_cue_ids)
        if owned & before or owned & after or before & after:
            message = "owned and context cue roles must be disjoint"
            raise ValueError(message)
        return self


class TranslationBatchPlan(FrozenModel):
    """Transient source-bound collection of semantic chunk plans."""

    plan_id: str = Field(min_length=1)
    source_digest: Sha256Digest
    chunks: tuple[ChunkPlan, ...] = Field(min_length=1)


def validate_translation_batch_plan(
    source: SourceSnapshot,
    plan: TranslationBatchPlan,
) -> tuple[ChunkPlan, ...]:
    """Revalidate a source-bound full-run plan at a file boundary."""
    if source_digest(source) != source.content_digest:
        message = "source snapshot digest mismatch"
        raise PreparationError(message)
    if plan.source_digest != source.content_digest:
        message = "batch plan source digest mismatch"
        raise PreparationError(message)
    return validate_batch_plan(source, plan.chunks)


def validate_batch_plan(
    source: SourceSnapshot,
    plans: Sequence[ChunkPlan],
) -> tuple[ChunkPlan, ...]:
    """Validate one transient, ordered partition of the complete source run."""
    batch = tuple(plans)
    chunk_ids = tuple(plan.chunk_id for plan in batch)
    if any(not chunk_id.strip() for chunk_id in chunk_ids):
        message = "chunk IDs must be nonblank"
        raise PreparationError(message)
    if len(chunk_ids) != len(set(chunk_ids)):
        message = "chunk IDs must be unique"
        raise PreparationError(message)

    source_ids = tuple(cue.cue_id for cue in source.cues)
    source_positions = {cue_id: index for index, cue_id in enumerate(source_ids)}
    requested_ids = {
        cue_id
        for plan in batch
        for cue_id in (
            *plan.context_before_source_cue_ids,
            *plan.owned_source_cue_ids,
            *plan.context_after_source_cue_ids,
        )
    }
    unknown_ids = requested_ids - set(source_ids)
    if unknown_ids:
        message = f"unknown source cue: {unknown_ids}"
        raise PreparationError(message)

    for plan in batch:
        before = plan.context_before_source_cue_ids
        owned = plan.owned_source_cue_ids
        after = plan.context_after_source_cue_ids
        if (
            len(owned) > MAX_OWNED_CUES_PER_CHUNK
            or len(before) > MAX_CONTEXT_CUES_PER_SIDE
            or len(after) > MAX_CONTEXT_CUES_PER_SIDE
        ):
            message = "chunk ownership and context are limited to 64 cues per side"
            raise PreparationError(message)
        owned_positions = tuple(source_positions[cue_id] for cue_id in owned)
        expected_positions = tuple(
            range(owned_positions[0], owned_positions[0] + len(owned_positions))
        )
        if owned_positions != expected_positions:
            message = "owned source cue IDs must be contiguous in source order"
            raise PreparationError(message)
        owned_start = owned_positions[0]
        owned_end = owned_positions[-1] + 1
        before_start = owned_start - len(before)
        if before_start < 0 or before != source_ids[before_start:owned_start]:
            message = "context_before must be a contiguous suffix before ownership"
            raise PreparationError(message)
        after_end = owned_end + len(after)
        if after_end > len(source_ids) or after != source_ids[owned_end:after_end]:
            message = "context_after must be a contiguous prefix after ownership"
            raise PreparationError(message)

    owned_ids = tuple(cue_id for plan in batch for cue_id in plan.owned_source_cue_ids)
    if owned_ids != source_ids:
        message = "every source cue must be owned exactly once in source order"
        raise PreparationError(message)
    return batch


class TranslationRequest(FrozenModel):
    """Complete machine-consumed request for one translation agent call."""

    schema_version: str = "translation-request.v1"
    chunk_id: str
    source_document_id: str
    source_digest: Sha256Digest
    source_maturity: SourceMaturity
    source_locale: LocaleCode
    target_locale: LocaleCode = "en-US"
    context: EpisodeContext
    context_evidence_ids: tuple[EvidenceId, ...]
    owned_cues: tuple[SourceCue, ...]
    context_before: tuple[SourceCue, ...] = ()
    context_after: tuple[SourceCue, ...] = ()
    evidence: tuple[EvidenceEntry, ...] = ()


def source_digest(source: SourceSnapshot) -> str:
    """Hash canonical source content without its self-referential digest."""
    payload = source.model_dump(mode="json", exclude={"content_digest"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def bind_source_digest(source: SourceSnapshot) -> SourceSnapshot:
    """Return the source snapshot bound to its current canonical bytes."""
    return source.model_copy(update={"content_digest": source_digest(source)})


def prepare_request(
    source: SourceSnapshot,
    plan: ChunkPlan,
    resolver: EvidenceResolver,
) -> TranslationRequest:
    """Build one ownership-safe request with full episode context."""
    if source_digest(source) != source.content_digest:
        message = "source snapshot digest mismatch"
        raise PreparationError(message)
    cue_by_id = {cue.cue_id: cue for cue in source.cues}
    requested_ids = (
        *plan.context_before_source_cue_ids,
        *plan.owned_source_cue_ids,
        *plan.context_after_source_cue_ids,
    )
    unknown_ids = {cue_id for cue_id in requested_ids if cue_id not in cue_by_id}
    if unknown_ids:
        message = f"unknown source cue: {unknown_ids}"
        raise PreparationError(message)
    source_positions = {
        cue.cue_id: position for position, cue in enumerate(source.cues)
    }
    for cue_ids in (
        plan.context_before_source_cue_ids,
        plan.owned_source_cue_ids,
        plan.context_after_source_cue_ids,
    ):
        positions = tuple(source_positions[cue_id] for cue_id in cue_ids)
        if positions != tuple(sorted(positions)):
            message = "chunk cue IDs must follow source order"
            raise PreparationError(message)
    included_ids = set(requested_ids)
    evidence_ids = {
        evidence_id
        for cue in source.cues
        if cue.cue_id in included_ids
        for evidence_id in cue.evidence_ids
    }
    verified_evidence = preflight_evidence(source, resolver, evidence_ids)
    return TranslationRequest(
        chunk_id=plan.chunk_id,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_maturity=source.maturity,
        source_locale=source.source_locale,
        context=source.context,
        context_evidence_ids=source.context_evidence_ids,
        owned_cues=tuple(cue_by_id[cue_id] for cue_id in plan.owned_source_cue_ids),
        context_before=tuple(
            cue_by_id[cue_id] for cue_id in plan.context_before_source_cue_ids
        ),
        context_after=tuple(
            cue_by_id[cue_id] for cue_id in plan.context_after_source_cue_ids
        ),
        evidence=verified_evidence,
    )
