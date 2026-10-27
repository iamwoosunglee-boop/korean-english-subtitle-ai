# Copyright 2026 Transcription Module contributors

"""Typed access to the legacy reviewed-publication test seam."""

from typing import Protocol, cast

from translation_module import publication as publication_module
from translation_module.contracts import SourceSnapshot, TranslationDocument
from translation_module.publication import PublicationError
from translation_module.review_state import ReviewAttempts
from translation_module.workflow import ReviewAssignment, ReviewResult


class ReviewedPublisher(Protocol):
    """Legacy reviewed publication callable used by domain-level tests."""

    def __call__(
        self,
        source: SourceSnapshot,
        candidate: TranslationDocument,
        review: ReviewResult,
        *,
        assignment: ReviewAssignment,
        attempts: ReviewAttempts,
    ) -> TranslationDocument: ...


publish_translation = cast(
    "ReviewedPublisher",
    vars(publication_module)["_publish_reviewed_translation"],
)

__all__ = ["PublicationError", "publish_translation"]
