# Copyright 2026 Transcription Module contributors

"""Prepare context and glossary drafts from one finalized source revision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from translation_module.canonical.common import (
    AssetLifecycleStatus,
    DocumentLifecycleStatus,
    DocumentReference,
)
from translation_module.canonical.context import (
    ContentContextAsset,
    ContextDecisionStatus,
    ContextFact,
    ContextStage,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.glossary import (
    GlossaryDecisionStatus,
    GlossaryEntry,
    GlossaryEntryType,
    TranslationGlossaryAsset,
)
from translation_module.canonical.source import unresolved_speaker_cue_ids
from translation_module.canonical.validation import validate_canonical_save

if TYPE_CHECKING:
    from translation_module.canonical.source import SourceTranscriptV2


class AssetPreparationError(ValueError):
    """Raised when prepared assets cannot be bound safely."""


@dataclass(frozen=True, slots=True)
class PreparedAssetBundle:
    """Prepared context and glossary drafts for one source revision."""

    context: ContentContextAsset
    glossary: TranslationGlossaryAsset


def _source_reference(
    source: SourceTranscriptV2,
    source_path: str | None,
) -> DocumentReference:
    payload: dict[str, object] = {
        "document_id": source.document_id,
        "revision_id": source.revision_id,
        "content_digest": source.content_digest,
    }
    if source_path is not None:
        payload["path"] = source_path
    return DocumentReference.model_validate(payload)


def _validate_inputs(
    source: SourceTranscriptV2,
    input_context: ContentContextAsset,
) -> None:
    try:
        validate_canonical_save(source)
        validate_canonical_save(input_context)
    except ValueError as error:
        message = f"canonical preparation input is invalid: {error}"
        raise AssetPreparationError(message) from error
    if source.lifecycle_status is not DocumentLifecycleStatus.FINALIZED:
        message = "asset preparation requires a finalized source revision"
        raise AssetPreparationError(message)
    unresolved = unresolved_speaker_cue_ids(source)
    if unresolved:
        message = f"source has unresolved speaker cues: {unresolved}"
        raise AssetPreparationError(message)
    if input_context.stage is not ContextStage.INPUT:
        message = "asset preparation requires an input context revision"
        raise AssetPreparationError(message)
    if input_context.lifecycle_status is not AssetLifecycleStatus.READY:
        message = "asset preparation requires a ready input context revision"
        raise AssetPreparationError(message)
    ref = source.input_context_ref
    if (
        ref.asset_id != input_context.asset_id
        or ref.revision_id != input_context.revision_id
        or ref.content_digest != input_context.content_digest
    ):
        message = "source input context does not match the supplied input context"
        raise AssetPreparationError(message)


def _source_note_facts(
    source: SourceTranscriptV2,
    existing_ids: set[str],
) -> tuple[ContextFact, ...]:
    facts: list[ContextFact] = []
    for cue in source.cues:
        if cue.context_note is None:
            continue
        base_id = f"source-note:{cue.cue_id}"
        fact_id = base_id
        suffix = 2
        while fact_id in existing_ids:
            fact_id = f"{base_id}:{suffix}"
            suffix += 1
        existing_ids.add(fact_id)
        facts.append(
            ContextFact(
                fact_id=fact_id,
                statement=cue.context_note,
                participant_ids=(),
                scope_source_cue_ids=(cue.cue_id,),
                evidence_ids=(),
                decision_status=ContextDecisionStatus.HUMAN_CONFIRMED,
                blocking=False,
            )
        )
    return tuple(facts)


def _participant_name_entries(
    input_context: ContentContextAsset,
) -> tuple[GlossaryEntry, ...]:
    return tuple(
        GlossaryEntry(
            entry_id=f"participant:{participant.participant_id}:name",
            source_variants=(participant.name_ko,),
            preferred_target=participant.name_en,
            type=GlossaryEntryType.PERSON_NAME,
            instruction="Use the prepared English participant name.",
            scope_source_cue_ids=(),
            decision_status=GlossaryDecisionStatus.VERIFIED,
            blocking=False,
            case_sensitive=True,
            evidence_ids=participant.evidence_ids,
            note="Seeded from the ready input context.",
        )
        for participant in input_context.participants
        if participant.name_en is not None
    )


def prepare_translation_assets(
    source: SourceTranscriptV2,
    input_context: ContentContextAsset,
    *,
    source_path: str | None = None,
) -> PreparedAssetBundle:
    """Create deterministic draft assets from a finalized source and input context."""
    _validate_inputs(source, input_context)
    source_ref = _source_reference(source, source_path)
    fact_ids = {fact.fact_id for fact in input_context.facts}
    context = bind_canonical_digests(
        ContentContextAsset(
            asset_id=input_context.asset_id,
            revision_id=f"{source.revision_id}:prepared-context",
            supersedes_revision_id=input_context.revision_id,
            content_digest="0" * 64,
            record_digest="0" * 64,
            stage=ContextStage.PREPARED,
            lifecycle_status=AssetLifecycleStatus.DRAFT,
            prepared_from_source_ref=source_ref,
            program=input_context.program,
            episode_summary=input_context.episode_summary,
            evidence=input_context.evidence,
            participants=input_context.participants,
            facts=(
                *input_context.facts,
                *_source_note_facts(source, fact_ids),
            ),
        )
    )
    glossary = bind_canonical_digests(
        TranslationGlossaryAsset(
            asset_id=f"{source.document_id}:glossary",
            revision_id=f"{source.revision_id}:glossary",
            content_digest="0" * 64,
            record_digest="0" * 64,
            lifecycle_status=AssetLifecycleStatus.DRAFT,
            prepared_from_source_ref=source_ref,
            evidence=input_context.evidence,
            entries=_participant_name_entries(input_context),
        )
    )
    try:
        validate_canonical_save(context)
        validate_canonical_save(glossary)
    except ValueError as error:
        message = f"prepared asset draft failed save validation: {error}"
        raise AssetPreparationError(message) from error
    return PreparedAssetBundle(context=context, glossary=glossary)


__all__ = [
    "AssetPreparationError",
    "PreparedAssetBundle",
    "prepare_translation_assets",
]
