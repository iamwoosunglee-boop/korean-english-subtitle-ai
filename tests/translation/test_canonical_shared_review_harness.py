# Copyright 2026 Transcription Module contributors

"""Shared translation/review chunk-range regressions."""

import pytest
from pydantic import ValidationError

from tests.translation.test_canonical_review_assembly import review_fixture
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.runtime import (
    SharedReviewChunkScope,
    TranslationReviewHarnessError,
    build_translation_review_harness,
)
from translation_module.common import SourceCueId
from translation_module.preparation import ChunkPlan, TranslationBatchPlan


def test_shared_harness_uses_translation_chunk_ranges_for_review() -> None:
    """Review ownership is derived from the exact translation chunk plan."""
    source, _context, _glossary, translation = review_fixture()
    plan = TranslationBatchPlan(
        plan_id="semantic-plan",
        source_digest=source.content_digest,
        chunks=(
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId(source.cues[0].cue_id),),
                context_after_source_cue_ids=(SourceCueId(source.cues[1].cue_id),),
            ),
            ChunkPlan(
                chunk_id="chunk-2",
                owned_source_cue_ids=(SourceCueId(source.cues[1].cue_id),),
                context_before_source_cue_ids=(SourceCueId(source.cues[0].cue_id),),
            ),
        ),
    )

    harness = build_translation_review_harness(source, translation, plan)

    assert tuple(scope.chunk_id for scope in harness.chunks) == (
        "chunk-1",
        "chunk-2",
    )
    assert tuple(scope.source_cue_ids for scope in harness.chunks) == tuple(
        chunk.owned_source_cue_ids for chunk in plan.chunks
    )
    assert tuple(scope.target_cue_ids for scope in harness.chunks) == (
        (translation.cues[0].cue_id,),
        (translation.cues[1].cue_id,),
    )
    assert harness.chunks[0].context_after_source_cue_ids == (source.cues[1].cue_id,)
    assert harness.chunks[1].context_before_source_cue_ids == (source.cues[0].cue_id,)


def test_shared_harness_rejects_more_than_64_context_cues() -> None:
    """Reviewer context preserves the translation plan's 64-cue ceiling."""
    context = tuple(f"source-{index}" for index in range(65))

    with pytest.raises(ValidationError):
        _ = SharedReviewChunkScope(
            chunk_id="chunk-1",
            source_cue_ids=("source-owned",),
            target_cue_ids=("target-owned",),
            context_before_source_cue_ids=context,
        )


def test_shared_harness_rejects_target_spanning_translation_chunks() -> None:
    """One target cannot make a reviewer silently merge translation chunks."""
    source, _context, _glossary, translation = review_fixture()
    crossing = translation.cues[0].model_copy(
        update={
            "source_cue_ids": (
                source.cues[0].cue_id,
                source.cues[1].cue_id,
            )
        }
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (crossing, translation.cues[1])})
    )
    plan = TranslationBatchPlan(
        plan_id="semantic-plan",
        source_digest=source.content_digest,
        chunks=(
            ChunkPlan(
                chunk_id="chunk-1",
                owned_source_cue_ids=(SourceCueId(source.cues[0].cue_id),),
            ),
            ChunkPlan(
                chunk_id="chunk-2",
                owned_source_cue_ids=(SourceCueId(source.cues[1].cue_id),),
            ),
        ),
    )

    with pytest.raises(
        TranslationReviewHarnessError,
        match="spans translation chunks",
    ):
        _ = build_translation_review_harness(source, translation, plan)
