# Copyright 2026 Transcription Module contributors

"""Fixed candidate and attempt builders for the canonical workflow proof."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcription_module.models import Milliseconds
from translation_module.canonical.attempts import (
    TranslationAttempt,
    TranslationAttemptBinding,
    TranslationAttemptKind,
    TranslationAttemptOutcome,
    TranslationLineRationale,
    translation_line_text_digest,
)
from translation_module.canonical.common import (
    AssetReference,
    DocumentLifecycleStatus,
    DocumentReference,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.translation import (
    DecisionStatus,
    LineageStatus,
    TargetCueV2,
    TranslationDocumentV2,
)
from translation_module.common import ContentKind

if TYPE_CHECKING:
    from translation_module.canonical.source import SourceTranscriptV2


def attempt_binding(document: TranslationDocumentV2) -> TranslationAttemptBinding:
    """Bind one attempt to every current candidate dependency."""
    return TranslationAttemptBinding(
        translation_document_id=document.document_id,
        translation_revision_id=document.revision_id,
        source_document_id=document.source_ref.document_id,
        source_revision_id=document.source_ref.revision_id,
        source_content_digest=document.source_ref.content_digest,
        prepared_context_asset_id=document.prepared_context_ref.asset_id,
        prepared_context_revision_id=document.prepared_context_ref.revision_id,
        prepared_context_content_digest=document.prepared_context_ref.content_digest,
        glossary_asset_id=document.glossary_ref.asset_id,
        glossary_revision_id=document.glossary_ref.revision_id,
        glossary_content_digest=document.glossary_ref.content_digest,
    )


def attempt_rationales(
    document: TranslationDocumentV2,
) -> tuple[TranslationLineRationale, ...]:
    """Cover every displayed target line with one concrete Korean rationale."""
    return tuple(
        TranslationLineRationale(
            target_cue_id=cue.cue_id,
            line_number=line_number,
            speaker_id=cue.speakers[min(line_number - 1, len(cue.speakers) - 1)],
            line_text_digest=translation_line_text_digest(line),
            rationale_ko="원문의 맥락과 화자 말투를 구체적으로 반영했습니다.",
        )
        for cue in document.cues
        for line_number, line in enumerate(cue.text.split("\n"), start=1)
    )


def successful_attempt(
    document: TranslationDocumentV2,
    identifier: str,
    ordinal: int,
) -> TranslationAttempt:
    """Build one ordered successful immutable attempt."""
    if ordinal == 1:
        return TranslationAttempt(
            attempt_id=identifier,
            ordinal=ordinal,
            kind=TranslationAttemptKind.INITIAL,
            outcome=TranslationAttemptOutcome.SUCCEEDED,
            binding=attempt_binding(document),
            candidate_digest=document.content_digest,
            rationales=attempt_rationales(document),
        )
    return TranslationAttempt(
        attempt_id=identifier,
        ordinal=ordinal,
        parent_attempt_id=f"attempt-{chr(95 + ordinal)}",
        kind=TranslationAttemptKind.ALTERNATIVE,
        outcome=TranslationAttemptOutcome.SUCCEEDED,
        binding=attempt_binding(document),
        candidate_digest=document.content_digest,
        rationales=attempt_rationales(document),
    )


def failed_attempt(
    document: TranslationDocumentV2,
    identifier: str,
    ordinal: int,
) -> TranslationAttempt:
    """Build one ordered failed immutable attempt."""
    return TranslationAttempt(
        attempt_id=identifier,
        ordinal=ordinal,
        parent_attempt_id=f"attempt-{chr(95 + ordinal)}",
        kind=TranslationAttemptKind.RETRY,
        outcome=TranslationAttemptOutcome.FAILED,
        binding=attempt_binding(document),
    )


def candidate(
    source: SourceTranscriptV2,
    context_id: tuple[str, str, str],
    glossary_id: tuple[str, str, str],
) -> TranslationDocumentV2:
    """Build one digest-bound candidate preserving source order and lineage."""
    cues = tuple(
        TargetCueV2(
            sequence=cue.sequence,
            cue_id=f"target-{cue.sequence}",
            source_cue_ids=(cue.cue_id,),
            lineage_status=LineageStatus.CONFIRMED,
            start_ms=Milliseconds(cue.start_ms),
            end_ms=Milliseconds(cue.end_ms),
            text=f"Line {cue.sequence}.",
            speakers=cue.speakers,
            content_kind=ContentKind.DIALOGUE,
            context_ids=(),
            evidence_ids=("frame-a",) if cue.sequence == 1 else (),
        )
        for cue in source.cues
    )
    return bind_canonical_digests(
        TranslationDocumentV2(
            document_id="translation-document",
            revision_id="translation-revision-b",
            content_digest="0" * 64,
            record_digest="0" * 64,
            source_ref=DocumentReference(
                document_id=source.document_id,
                revision_id=source.revision_id,
                content_digest=source.content_digest,
                path="source.json",
            ),
            prepared_context_ref=AssetReference(
                asset_id=context_id[0],
                revision_id=context_id[1],
                content_digest=context_id[2],
            ),
            glossary_ref=AssetReference(
                asset_id=glossary_id[0],
                revision_id=glossary_id[1],
                content_digest=glossary_id[2],
            ),
            lifecycle_status=DocumentLifecycleStatus.DRAFT,
            decision_status=DecisionStatus.CLEAR,
            cues=cues,
            unresolved_decisions=(),
        )
    )
