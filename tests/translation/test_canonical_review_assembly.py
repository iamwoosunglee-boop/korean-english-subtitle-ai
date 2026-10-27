# Copyright 2026 Transcription Module contributors

"""Chunk review assembly and immutable coverage gate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from translation_module.canonical import (
    CanonicalArtifact,
    CanonicalDiagnosticCode,
    CanonicalReviewKind,
    ContentContextAsset,
    EffectiveReviewState,
    ReviewArtifactResult,
    ReviewProfile,
    SourceFinalization,
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationGlossaryAsset,
    bind_canonical_digests,
    evaluate_review_state,
    finalize_source,
    ready_context_asset,
    ready_glossary_asset,
)
from translation_module.canonical.review_assembly import (
    ReviewAssemblyError,
    ReviewAssemblyManifest,
    ReviewChunkReport,
    ReviewGlobalAudit,
    assemble_review_artifact,
    review_chunk_digest,
    review_dependency_binding,
)

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "docs" / "contracts" / "examples"
PROFILE = ReviewProfile(profile_id="en-us-editorial-review", version="2.0")
type ReviewFixture = tuple[
    SourceTranscriptV2,
    ContentContextAsset,
    TranslationGlossaryAsset,
    TranslationDocumentV2,
]


def _load[ArtifactT: CanonicalArtifact](
    name: str,
    model: type[ArtifactT],
) -> ArtifactT:
    return model.model_validate_json(
        (EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8")
    )


def _fixture() -> ReviewFixture:
    input_context = _load(
        "content-context.input.v1",
        ContentContextAsset,
    )
    source_draft = _load("source-transcript.v2", SourceTranscriptV2)
    first_source = source_draft.cues[0]
    second_source = first_source.model_copy(
        update={
            "sequence": 2,
            "cue_id": "source:ass-dialogue:000002",
            "start_ms": 1000,
            "end_ms": 2000,
            "text": "노바 노바!",
            "evidence_ids": (),
        }
    )
    source_draft = bind_canonical_digests(
        source_draft.model_copy(update={"cues": (first_source, second_source)})
    )
    source = finalize_source(
        source_draft,
        SourceFinalization(
            finalized_at="2026-08-08T00:00:00Z",
            finalized_by="human-editor",
        ),
        input_context=input_context,
    )

    context_draft = _load("content-context.v1", ContentContextAsset)
    context_ref = context_draft.prepared_from_source_ref
    assert context_ref is not None
    context_draft = bind_canonical_digests(
        context_draft.model_copy(
            update={
                "prepared_from_source_ref": context_ref.model_copy(
                    update={
                        "document_id": source.document_id,
                        "revision_id": source.revision_id,
                        "content_digest": source.content_digest,
                    }
                )
            }
        )
    )
    context = ready_context_asset(context_draft, source=source)

    glossary_draft = _load(
        "translation-glossary.v1",
        TranslationGlossaryAsset,
    )
    prepared_source_ref = glossary_draft.prepared_from_source_ref.model_copy(
        update={
            "document_id": source.document_id,
            "revision_id": source.revision_id,
            "content_digest": source.content_digest,
        }
    )
    glossary_draft = bind_canonical_digests(
        glossary_draft.model_copy(
            update={"prepared_from_source_ref": prepared_source_ref}
        )
    )
    glossary = ready_glossary_asset(glossary_draft, source=source)

    translation = _load(
        "translation-document.v2",
        TranslationDocumentV2,
    )
    first_target = translation.cues[0]
    second_target = first_target.model_copy(
        update={
            "sequence": 2,
            "cue_id": "target:000002",
            "source_cue_ids": (second_source.cue_id,),
            "start_ms": 1000,
            "end_ms": 2000,
            "text": "NOVA NOVA!",
        }
    )
    translation = bind_canonical_digests(
        translation.model_copy(
            update={
                "source_ref": translation.source_ref.model_copy(
                    update={
                        "document_id": source.document_id,
                        "revision_id": source.revision_id,
                        "content_digest": source.content_digest,
                    }
                ),
                "prepared_context_ref": translation.prepared_context_ref.model_copy(
                    update={
                        "asset_id": context.asset_id,
                        "revision_id": context.revision_id,
                        "content_digest": context.content_digest,
                    }
                ),
                "glossary_ref": translation.glossary_ref.model_copy(
                    update={
                        "asset_id": glossary.asset_id,
                        "revision_id": glossary.revision_id,
                        "content_digest": glossary.content_digest,
                    }
                ),
                "cues": (first_target, second_target),
            }
        )
    )
    return source, context, glossary, translation


def _chunk(
    translation: TranslationDocumentV2,
    *,
    chunk_id: str,
    source_ids: tuple[str, ...],
    target_ids: tuple[str, ...],
    execution_id: str,
) -> ReviewChunkReport:
    return ReviewChunkReport(
        chunk_id=chunk_id,
        binding=review_dependency_binding(translation, PROFILE),
        target_scope_digest=review_chunk_digest(translation, target_ids),
        source_cue_ids=source_ids,
        target_cue_ids=target_ids,
        findings=(),
        reviewer_execution_id=execution_id,
    )


def _audit(
    translation: TranslationDocumentV2,
    glossary: TranslationGlossaryAsset,
    *,
    boundary_pairs: tuple[tuple[str, str], ...] | None = None,
    glossary_ids: tuple[str, ...] | None = None,
) -> ReviewGlobalAudit:
    return ReviewGlobalAudit(
        audit_id="global-audit",
        binding=review_dependency_binding(translation, PROFILE),
        candidate_content_digest=translation.content_digest,
        continuity_audit_completed=True,
        terminology_audit_completed=True,
        target_boundary_pairs=(
            ((translation.cues[0].cue_id, translation.cues[1].cue_id),)
            if boundary_pairs is None
            else boundary_pairs
        ),
        glossary_entry_ids=(
            tuple(entry.entry_id for entry in glossary.entries)
            if glossary_ids is None
            else glossary_ids
        ),
        findings=(),
        reviewer_execution_id="reviewer-global",
    )


def _manifest(
    translation: TranslationDocumentV2,
    glossary: TranslationGlossaryAsset,
    chunks: tuple[ReviewChunkReport, ...],
    *,
    audit: ReviewGlobalAudit | None = None,
) -> ReviewAssemblyManifest:
    return ReviewAssemblyManifest(
        review_id="review-current",
        review_kind=CanonicalReviewKind.INDEPENDENT_AI,
        review_profile=PROFILE,
        completed_at="2026-08-08T01:00:00Z",
        model="review-model",
        chunk_reports=chunks,
        global_audit=audit or _audit(translation, glossary),
    )


def test_complete_chunk_and_global_audits_assemble_passed_review() -> None:
    """Passed coverage derives from scoped reports rather than caller totals."""
    source, context, glossary, translation = _fixture()
    chunks = (
        _chunk(
            translation,
            chunk_id="chunk-1",
            source_ids=(source.cues[0].cue_id,),
            target_ids=(translation.cues[0].cue_id,),
            execution_id="reviewer-1",
        ),
        _chunk(
            translation,
            chunk_id="chunk-2",
            source_ids=(source.cues[1].cue_id,),
            target_ids=(translation.cues[1].cue_id,),
            execution_id="reviewer-2",
        ),
    )

    review = assemble_review_artifact(
        translation,
        source,
        context,
        glossary,
        _manifest(translation, glossary, chunks),
    )

    assert review.result is ReviewArtifactResult.PASSED
    assert review.coverage.source_cue_ids == tuple(cue.cue_id for cue in source.cues)
    assert review.coverage.target_cue_ids == tuple(
        cue.cue_id for cue in translation.cues
    )
    assert review.coverage.target_boundary_pairs == (
        (translation.cues[0].cue_id, translation.cues[1].cue_id),
    )
    assert review.coverage.glossary_entry_ids == tuple(
        entry.entry_id for entry in glossary.entries
    )
    assert review.provenance.reviewer_execution_ids == (
        "reviewer-1",
        "reviewer-2",
        "reviewer-global",
    )


def test_review_assembly_allows_omitted_model_metadata() -> None:
    """Optional execution metadata stays omitted instead of becoming JSON null."""
    source, context, glossary, translation = _fixture()
    chunk = _chunk(
        translation,
        chunk_id="all-targets",
        source_ids=tuple(cue.cue_id for cue in source.cues),
        target_ids=tuple(cue.cue_id for cue in translation.cues),
        execution_id="reviewer-all",
    )
    payload = _manifest(translation, glossary, (chunk,)).model_dump(
        mode="json",
        exclude_none=True,
    )
    payload.pop("model")
    manifest = ReviewAssemblyManifest.model_validate(payload)

    review = assemble_review_artifact(
        translation,
        source,
        context,
        glossary,
        manifest,
    )

    assert review.provenance.model is None


def test_missing_target_or_source_coverage_assembles_failed_review() -> None:
    """A partial chunk declaration cannot become a passed review artifact."""
    source, context, glossary, translation = _fixture()
    partial = (
        _chunk(
            translation,
            chunk_id="chunk-1",
            source_ids=(source.cues[0].cue_id,),
            target_ids=(translation.cues[0].cue_id,),
            execution_id="reviewer-1",
        ),
    )

    review = assemble_review_artifact(
        translation,
        source,
        context,
        glossary,
        _manifest(translation, glossary, partial),
    )

    assert review.result is ReviewArtifactResult.FAILED
    assert review.coverage.source_cue_ids == (source.cues[0].cue_id,)
    assert review.coverage.target_cue_ids == (translation.cues[0].cue_id,)


def test_missing_boundary_or_glossary_audit_assembles_failed_review() -> None:
    """Target IDs alone do not prove cross-cue continuity or terminology review."""
    source, context, glossary, translation = _fixture()
    chunk = _chunk(
        translation,
        chunk_id="all-targets",
        source_ids=tuple(cue.cue_id for cue in source.cues),
        target_ids=tuple(cue.cue_id for cue in translation.cues),
        execution_id="reviewer-all",
    )
    audit = _audit(translation, glossary, boundary_pairs=(), glossary_ids=())

    review = assemble_review_artifact(
        translation,
        source,
        context,
        glossary,
        _manifest(translation, glossary, (chunk,), audit=audit),
    )

    assert review.result is ReviewArtifactResult.FAILED
    assert review.coverage.target_boundary_pairs == ()
    assert review.coverage.glossary_entry_ids == ()


def test_wrong_chunk_scope_digest_is_rejected() -> None:
    """A reviewer cannot attest IDs for content other than the current chunk bytes."""
    source, context, glossary, translation = _fixture()
    chunk = _chunk(
        translation,
        chunk_id="chunk-1",
        source_ids=(source.cues[0].cue_id,),
        target_ids=(translation.cues[0].cue_id,),
        execution_id="reviewer-1",
    ).model_copy(update={"target_scope_digest": "f" * 64})

    with pytest.raises(ReviewAssemblyError, match="scope digest"):
        _ = assemble_review_artifact(
            translation,
            source,
            context,
            glossary,
            _manifest(translation, glossary, (chunk,)),
        )


def test_unchanged_chunk_report_can_be_reused_after_other_chunk_edit() -> None:
    """Only the changed target chunk needs a new scoped report."""
    source, context, glossary, translation = _fixture()
    old_chunk_2 = _chunk(
        translation,
        chunk_id="chunk-2",
        source_ids=(source.cues[1].cue_id,),
        target_ids=(translation.cues[1].cue_id,),
        execution_id="reviewer-2-old",
    )
    edited_first = translation.cues[0].model_copy(update={"text": "Welcome back."})
    edited = bind_canonical_digests(
        translation.model_copy(update={"cues": (edited_first, translation.cues[1])})
    )
    new_chunk_1 = _chunk(
        edited,
        chunk_id="chunk-1",
        source_ids=(source.cues[0].cue_id,),
        target_ids=(edited.cues[0].cue_id,),
        execution_id="reviewer-1-new",
    )

    review = assemble_review_artifact(
        edited,
        source,
        context,
        glossary,
        _manifest(
            edited,
            glossary,
            (new_chunk_1, old_chunk_2),
            audit=_audit(edited, glossary),
        ),
    )

    assert review.result is ReviewArtifactResult.PASSED
    assert "reviewer-2-old" in review.provenance.reviewer_execution_ids


def test_changed_chunk_report_and_stale_dependency_binding_are_rejected() -> None:
    """Changed owned content or dependency assets require new review evidence."""
    source, context, glossary, translation = _fixture()
    old_chunk = _chunk(
        translation,
        chunk_id="chunk-1",
        source_ids=(source.cues[0].cue_id,),
        target_ids=(translation.cues[0].cue_id,),
        execution_id="reviewer-old",
    )
    edited_first = translation.cues[0].model_copy(update={"text": "Welcome back."})
    edited = bind_canonical_digests(
        translation.model_copy(update={"cues": (edited_first, translation.cues[1])})
    )

    with pytest.raises(ReviewAssemblyError, match="scope digest"):
        _ = assemble_review_artifact(
            edited,
            source,
            context,
            glossary,
            _manifest(
                edited,
                glossary,
                (old_chunk,),
                audit=_audit(edited, glossary),
            ),
        )

    changed_glossary = bind_canonical_digests(
        glossary.model_copy(update={"entries": ()})
    )
    with pytest.raises(ReviewAssemblyError, match="binding"):
        _ = assemble_review_artifact(
            translation,
            source,
            context,
            changed_glossary,
            _manifest(translation, changed_glossary, (old_chunk,)),
        )


def test_incomplete_forged_pass_is_effective_failed() -> None:
    """Effective state does not trust a stored passed enum with incomplete coverage."""
    source, context, glossary, translation = _fixture()
    chunks = (
        _chunk(
            translation,
            chunk_id="chunk-1",
            source_ids=(source.cues[0].cue_id,),
            target_ids=(translation.cues[0].cue_id,),
            execution_id="reviewer-1",
        ),
    )
    review = assemble_review_artifact(
        translation,
        source,
        context,
        glossary,
        _manifest(translation, glossary, chunks),
    ).model_copy(update={"result": ReviewArtifactResult.PASSED})
    review = bind_canonical_digests(review)

    evaluation = evaluate_review_state(
        translation,
        (review,),
        PROFILE,
        source=source,
        glossary=glossary,
    )

    assert evaluation.state is EffectiveReviewState.FAILED
    assert CanonicalDiagnosticCode.REVIEW_COVERAGE_INCOMPLETE in {
        item.code for item in evaluation.diagnostics
    }


review_fixture = _fixture
review_manifest_fixture = _manifest
review_chunk_fixture = _chunk
