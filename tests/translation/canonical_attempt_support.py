# Copyright 2026 Transcription Module contributors

"""Reusable strict values for canonical attempt tests."""

from pathlib import Path

from translation_module.canonical.attempts import (
    TranslationAttempt,
    TranslationAttemptBinding,
    TranslationAttemptKind,
    TranslationAttemptOutcome,
    TranslationLineRationale,
    TranslationSelectionReceipt,
    translation_line_text_digest,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.translation import TranslationDocumentV2

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "docs/contracts/examples/translation-document.v2.example.json"


def attempt_document() -> TranslationDocumentV2:
    """Build one digest-valid two-line, two-speaker candidate."""
    document = TranslationDocumentV2.model_validate_json(EXAMPLE.read_bytes())
    cue = document.cues[0].model_copy(
        update={
            "end_ms": 3_000,
            "text": "First displayed line.\nSecond displayed line.",
            "speakers": ("speaker-a", "speaker-b"),
        }
    )
    return bind_canonical_digests(document.model_copy(update={"cues": (cue,)}))


def attempt_binding(document: TranslationDocumentV2) -> TranslationAttemptBinding:
    """Bind an attempt to every immutable candidate dependency."""
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
    """Build exact ordered rationale coverage for every displayed line."""
    cue = document.cues[0]
    return tuple(
        TranslationLineRationale(
            target_cue_id=cue.cue_id,
            line_number=line_number,
            speaker_id=cue.speakers[line_number - 1],
            line_text_digest=translation_line_text_digest(line),
            rationale_ko=f"{line_number}번 줄의 맥락과 말투를 반영했습니다.",
        )
        for line_number, line in enumerate(cue.text.split("\n"), start=1)
    )


def successful_attempt(
    document: TranslationDocumentV2,
    attempt_id: str = "attempt-a",
    ordinal: int = 1,
) -> TranslationAttempt:
    """Build one successful attempt with optional prior lineage."""
    payload = {
        "attempt_id": attempt_id,
        "ordinal": ordinal,
        "kind": TranslationAttemptKind.INITIAL,
        "outcome": TranslationAttemptOutcome.SUCCEEDED,
        "binding": attempt_binding(document),
        "candidate_digest": document.content_digest,
        "rationales": attempt_rationales(document),
    }
    if ordinal > 1:
        payload["parent_attempt_id"] = "attempt-a"
    return TranslationAttempt.model_validate(payload)


def failed_attempt(
    document: TranslationDocumentV2,
    attempt_id: str = "attempt-c",
    ordinal: int = 3,
) -> TranslationAttempt:
    """Build one failed attempt with no candidate or rationale coverage."""
    return TranslationAttempt(
        attempt_id=attempt_id,
        ordinal=ordinal,
        parent_attempt_id="attempt-b",
        kind=TranslationAttemptKind.RETRY,
        outcome=TranslationAttemptOutcome.FAILED,
        binding=attempt_binding(document),
    )


def selection_receipt(
    document: TranslationDocumentV2,
    attempt: TranslationAttempt,
) -> TranslationSelectionReceipt:
    """Build one fixed-time receipt for a successful attempt."""
    assert attempt.candidate_digest is not None
    return TranslationSelectionReceipt(
        selection_id="selection-1",
        selected_at="2026-08-18T00:00:00Z",
        selected_by="editor-1",
        translation_document_id=document.document_id,
        translation_revision_id=document.revision_id,
        translation_document_digest=document.content_digest,
        attempt_id=attempt.attempt_id,
        attempt_candidate_digest=attempt.candidate_digest,
    )
