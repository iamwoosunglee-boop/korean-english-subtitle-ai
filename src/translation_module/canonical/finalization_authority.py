# Copyright 2026 Transcription Module contributors

"""Selected-candidate authority for canonical translation finalization."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from translation_module.canonical._attempt_models import SelectionBindingError
from translation_module.canonical._selected_store import (
    load_selected_candidate_with_receipt,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from translation_module.canonical.context import ContentContextAsset
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.review import (
        ReviewProfile,
        TranslationReviewArtifact,
    )
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import (
        TranslationDocumentV2,
        TranslationFinalization,
    )


class TranslationValidationContext(Protocol):
    """Structural validation context accepted by selected finalization."""

    @property
    def source(self) -> SourceTranscriptV2 | None:
        """Return the selected source dependency."""
        ...

    @property
    def context(self) -> ContentContextAsset | None:
        """Return the selected context dependency."""
        ...

    @property
    def glossary(self) -> TranslationGlossaryAsset | None:
        """Return the selected glossary dependency."""
        ...

    @property
    def reviews(self) -> tuple[TranslationReviewArtifact, ...]:
        """Return immutable review evidence."""
        ...

    @property
    def review_profile(self) -> ReviewProfile:
        """Return the exact review profile binding."""
        ...


class FinalizationReport(Protocol):
    """Blocking state passed from validation to the pure finalizer."""

    @property
    def has_blocking(self) -> bool:
        """Return whether finalization must fail."""
        ...


type FinalizationValidator = Callable[
    [TranslationDocumentV2, TranslationFinalization, TranslationValidationContext],
    FinalizationReport,
]
type TranslationFinalizer = Callable[
    [TranslationDocumentV2, TranslationFinalization, FinalizationReport],
    TranslationDocumentV2,
]


def finalize_translation_core(
    root: Path,
    document_id: str,
    revision_id: str,
    finalization: TranslationFinalization,
    environment: TranslationValidationContext,
) -> TranslationDocumentV2:
    """Reload persisted authority and finalize its exact selected revision."""
    if environment.source is None or environment.glossary is None:
        message = "selected finalization requires source and glossary dependencies"
        raise ValueError(message)
    translation, receipt = load_selected_candidate_with_receipt(
        root, document_id, revision_id, environment.source, environment.glossary
    )
    for review in environment.reviews:
        binding = review.binding
        if (
            binding.translation_document_id == translation.document_id
            and binding.translation_revision_id == translation.revision_id
            and binding.candidate_content_digest == translation.content_digest
            and (
                binding.selection_id != receipt.selection_id
                or binding.attempt_id != receipt.attempt_id
            )
        ):
            message = "review does not bind the selected attempt"
            raise SelectionBindingError(message)
    module = import_module("translation_module.canonical.validation")
    # Lazy lookup preserves historical aliases without an import cycle.
    validate_translation_finalization = cast(
        "FinalizationValidator", vars(module)["validate_translation_finalization"]
    )
    finalize_translation = cast(
        "TranslationFinalizer", vars(module)["finalize_translation"]
    )
    report = validate_translation_finalization(
        translation,
        finalization,
        environment,
    )
    return finalize_translation(translation, finalization, report)


__all__ = ["finalize_translation_core"]
