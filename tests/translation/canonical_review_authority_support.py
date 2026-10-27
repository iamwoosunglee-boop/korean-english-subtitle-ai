# Copyright 2026 Transcription Module contributors

"""Persisted authority fixture for canonical review assembly."""

from pathlib import Path

from tests.e2e.canonical_workflow_translation_fixture_support import successful_attempt
from tests.e2e.canonical_workflow_translation_support import (
    checker_evidence,
    selection_receipt,
)
from translation_module.canonical import (
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationGlossaryAsset,
)
from translation_module.canonical.preflight import CandidatePreflightContext
from translation_module.canonical.runtime import CanonicalTranslationWorkflow


def review_authority_root(
    root: Path,
    source: SourceTranscriptV2,
    glossary: TranslationGlossaryAsset,
    translation: TranslationDocumentV2,
) -> Path:
    """Persist one selected, preflight-clean candidate authority."""
    authority_root = root / "translation-authority"
    workflow = CanonicalTranslationWorkflow(authority_root)
    attempt = successful_attempt(translation, "review-attempt", 1)
    _ = workflow.record_attempt(attempt, translation)
    graph = CandidatePreflightContext(translation, source, glossary)
    _ = workflow.preflight_and_authorize(
        graph,
        checker_evidence(translation),
        selection_receipt(translation, attempt.attempt_id),
    )
    return authority_root
