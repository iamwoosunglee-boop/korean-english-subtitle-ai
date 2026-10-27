# Copyright 2026 Transcription Module contributors

"""Real selected and reviewed finalization fixtures for delivery seams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tests.translation.canonical_finalization_test_support import (
    finalized_dependencies_fixture,
    review_for_fixture,
    selected_authority_fixture,
    translation_finalization_fixture,
    translation_for_dependencies_fixture,
    validation_environment_fixture,
)
from translation_module.canonical.delivery import (
    DeliveryDependencies,
    ReviewTimecodeSpec,
    SelectedTranslationIdentity,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.runtime import (
    CanonicalTranslationWorkflow,
    finalize_selected_translation,
)
from translation_module.canonical.translation import ReviewAcknowledgement

if TYPE_CHECKING:
    from pathlib import Path

    from translation_module.canonical.translation import TranslationDocumentV2

REVIEW_TIMECODE = ReviewTimecodeSpec(
    frame_rate_numerator=30_000,
    frame_rate_denominator=1_001,
    drop_frame=True,
)


@dataclass(frozen=True)
class DeliveryCase:
    """A complete dependency graph and its immutable selected draft/final record."""

    workflow: CanonicalTranslationWorkflow
    identity: SelectedTranslationIdentity
    dependencies: DeliveryDependencies
    candidate: TranslationDocumentV2
    finalized: TranslationDocumentV2


def delivery_case(root: Path, *, text: str = "Welcome back.") -> DeliveryCase:
    """Finalize real persisted selection with a complete, exactly bound review."""
    source, context, glossary = finalized_dependencies_fixture()
    candidate = translation_for_dependencies_fixture(source, context, glossary)
    candidate = bind_canonical_digests(
        candidate.model_copy(
            update={"cues": (candidate.cues[0].model_copy(update={"text": text}),)}
        )
    )
    root, document_id, revision_id = selected_authority_fixture(
        root,
        candidate,
        source,
        glossary,
    )
    review = review_for_fixture(candidate, source, context, glossary)
    environment = validation_environment_fixture(
        source,
        context,
        glossary,
        reviews=(review,),
    )
    dependencies = DeliveryDependencies(
        source,
        glossary,
        REVIEW_TIMECODE,
        context,
        (review,),
        environment.review_profile,
    )
    finalized = finalize_selected_translation(
        root,
        document_id,
        revision_id,
        translation_finalization_fixture(ReviewAcknowledgement.PASSED),
        environment,
    )
    return DeliveryCase(
        CanonicalTranslationWorkflow(root),
        SelectedTranslationIdentity(document_id, revision_id),
        dependencies,
        candidate,
        finalized,
    )


def stored_bytes(root: Path) -> dict[Path, bytes]:
    """Snapshot all persisted files, including selected and attempt history."""
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
