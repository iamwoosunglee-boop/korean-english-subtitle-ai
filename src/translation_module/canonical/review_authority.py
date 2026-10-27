# Copyright 2026 Transcription Module contributors

"""Selected-candidate authority for canonical review assembly."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from translation_module.canonical._attempt_store import TranslationAttemptStore
from translation_module.canonical._selected_store import load_selected_candidate
from translation_module.canonical.digest import bind_canonical_digests

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from translation_module.canonical.context import ContentContextAsset
    from translation_module.canonical.glossary import TranslationGlossaryAsset
    from translation_module.canonical.review import (
        CanonicalReviewKind,
        ReviewProfile,
        TranslationReviewArtifact,
    )
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import TranslationDocumentV2


class ReviewAssemblyManifest(Protocol):
    """Structural review manifest accepted by the authority boundary."""

    @property
    def review_kind(self) -> CanonicalReviewKind:
        """Return the declared review kind."""
        ...

    @property
    def review_profile(self) -> ReviewProfile:
        """Return the exact review profile binding."""
        ...


type ReviewAssembler = Callable[
    [
        TranslationDocumentV2,
        SourceTranscriptV2,
        ContentContextAsset,
        TranslationGlossaryAsset,
        ReviewAssemblyManifest,
    ],
    TranslationReviewArtifact,
]


@dataclass(frozen=True, slots=True)
class ReviewAssemblyInputs:
    """Resolved dependencies and manifest for one selected review."""

    source: SourceTranscriptV2
    context: ContentContextAsset
    glossary: TranslationGlossaryAsset
    manifest: ReviewAssemblyManifest


def assemble_review_artifact_core(
    root: Path,
    document_id: str,
    revision_id: str,
    inputs: ReviewAssemblyInputs,
) -> TranslationReviewArtifact:
    """Reload persisted authority and assemble its exact selected revision."""
    candidate = load_selected_candidate(
        root, document_id, revision_id, inputs.source, inputs.glossary
    )
    module = import_module("translation_module.canonical.review_assembly")
    # Lazy lookup preserves historical aliases without an import cycle.
    assemble_review_artifact = cast(
        "ReviewAssembler", vars(module)["assemble_review_artifact"]
    )
    review = assemble_review_artifact(
        candidate,
        inputs.source,
        inputs.context,
        inputs.glossary,
        inputs.manifest,
    )
    receipt = TranslationAttemptStore(root / "attempts").require_selected_receipt(
        candidate
    )
    binding = review.binding.model_copy(
        update={
            "selection_id": receipt.selection_id,
            "attempt_id": receipt.attempt_id,
        }
    )
    return bind_canonical_digests(
        review.model_copy(update={"binding": binding})
    )


__all__ = ["ReviewAssemblyInputs", "assemble_review_artifact_core"]
