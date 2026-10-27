# Copyright 2026 Transcription Module contributors

"""Canonical review assembly rejects caller-forged authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.translation.test_canonical_review_assembly import (
    review_chunk_fixture,
    review_fixture,
    review_manifest_fixture,
)
from translation_module.canonical.review_authority import (
    ReviewAssemblyInputs,
    assemble_review_artifact_core,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from translation_module.canonical.review import TranslationReviewArtifact


def test_forged_capability_cannot_assemble_review() -> None:
    """Review core accepts persistence coordinates, never caller authority."""
    source, context, glossary, translation = review_fixture()

    class ForgedAuthority:
        @staticmethod
        def require_current() -> str:
            return "forged"

    forged_core = cast(
        "Callable[..., TranslationReviewArtifact]", assemble_review_artifact_core
    )
    with pytest.raises((AttributeError, TypeError)):
        _ = forged_core(
            ForgedAuthority(),
            translation.document_id,
            translation.revision_id,
            ReviewAssemblyInputs(
                source,
                context,
                glossary,
                review_manifest_fixture(
                    translation,
                    glossary,
                    (
                        review_chunk_fixture(
                            translation,
                            chunk_id="forged-chunk",
                            source_ids=tuple(cue.cue_id for cue in source.cues),
                            target_ids=tuple(cue.cue_id for cue in translation.cues),
                            execution_id="forged-reviewer",
                        ),
                    ),
                ),
            ),
        )
