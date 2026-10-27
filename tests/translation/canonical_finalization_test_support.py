# Copyright 2026 Transcription Module contributors

"""Canonical finalization fixtures shared by authority scenarios."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.e2e.canonical_workflow_translation_fixture_support import successful_attempt
from tests.e2e.canonical_workflow_translation_support import (
    checker_evidence,
    selection_receipt,
)
from tests.translation.test_canonical_validation_state import (
    finalized_dependencies_fixture,
    review_for_fixture,
    translation_finalization_fixture,
    translation_for_dependencies_fixture,
    validation_environment_fixture,
)
from translation_module.canonical.preflight import CandidatePreflightContext
from translation_module.canonical.runtime import CanonicalTranslationWorkflow

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical import (
        SourceTranscriptV2,
        TranslationDocumentV2,
        TranslationGlossaryAsset,
    )


def selected_authority_fixture(
    root: Path,
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2,
    glossary: TranslationGlossaryAsset,
) -> tuple[Path, str, str]:
    """Persist and select the exact candidate used by finalization tests."""
    workflow = CanonicalTranslationWorkflow(root)
    attempt = successful_attempt(translation, "validation-attempt", 1)
    _ = workflow.record_attempt(attempt, translation)
    graph = CandidatePreflightContext(translation, source, glossary)
    workflow.preflight_and_authorize(
        graph,
        checker_evidence(
            translation,
            tuple(cue.cue_id for cue in source.cues),
        ),
        selection_receipt(translation, attempt.attempt_id),
    )
    return root, translation.document_id, translation.revision_id


__all__ = [
    "finalized_dependencies_fixture",
    "review_for_fixture",
    "selected_authority_fixture",
    "translation_finalization_fixture",
    "translation_for_dependencies_fixture",
    "validation_environment_fixture",
]
