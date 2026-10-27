# Copyright 2026 Transcription Module contributors

"""Canonical lifecycle, validation-tier, and review-state tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from translation_module.canonical import (
    CanonicalArtifact,
    CanonicalDiagnosticCode,
    CanonicalFinalizationError,
    CanonicalValidationReport,
    ContentContextAsset,
    DocumentLifecycleStatus,
    EffectiveReviewState,
    FindingDisposition,
    LineageStatus,
    ReviewAcknowledgement,
    ReviewArtifactResult,
    ReviewEvaluation,
    ReviewProfile,
    SourceFinalization,
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationFinalization,
    TranslationGlossaryAsset,
    TranslationReviewArtifact,
    TranslationValidationContext,
    bind_canonical_digests,
    create_source_draft_revision,
    draft_translation_diagnostics,
    evaluate_review_state,
    finalize_source,
    ready_context_asset,
    ready_glossary_asset,
    validate_canonical_save,
    validate_translation_finalization,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "docs" / "contracts" / "examples"
PROFILE = ReviewProfile(profile_id="en-us-editorial-review", version="2.0")


def _load[ArtifactT: CanonicalArtifact](
    name: str,
    model: type[ArtifactT],
) -> ArtifactT:
    return model.model_validate_json(
        (EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8")
    )


def _model_payload(model: BaseModel) -> dict[str, object]:
    return cast(
        "dict[str, object]",
        model.model_dump(mode="json", exclude_none=True),
    )


def _object(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _object_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    for item in cast("list[object]", value):
        assert isinstance(item, dict)
    return cast("list[dict[str, object]]", value)


def _source() -> SourceTranscriptV2:
    return _load("source-transcript.v2", SourceTranscriptV2)


def _input_context() -> ContentContextAsset:
    return _load("content-context.input.v1", ContentContextAsset)


def _prepared_context() -> ContentContextAsset:
    return _load("content-context.v1", ContentContextAsset)


def _glossary() -> TranslationGlossaryAsset:
    return _load("translation-glossary.v1", TranslationGlossaryAsset)


def _translation() -> TranslationDocumentV2:
    return _load("translation-document.v2", TranslationDocumentV2)


def _review() -> TranslationReviewArtifact:
    return _load("translation-review.v1", TranslationReviewArtifact)


def _source_finalization() -> SourceFinalization:
    return SourceFinalization(
        finalized_at="2026-08-07T12:00:00Z",
        finalized_by="human-editor",
    )


def _environment(
    source: SourceTranscriptV2 | None,
    context: ContentContextAsset | None,
    glossary: TranslationGlossaryAsset | None,
    *,
    reviews: tuple[TranslationReviewArtifact, ...] = (),
) -> TranslationValidationContext:
    """Bundle resolved dependencies for translation validation."""
    return TranslationValidationContext(
        source=source,
        context=context,
        glossary=glossary,
        reviews=reviews,
        review_profile=PROFILE,
    )


def _translation_finalization(
    acknowledgement: ReviewAcknowledgement,
    *,
    dispositions: tuple[FindingDisposition, ...] = (),
) -> TranslationFinalization:
    return TranslationFinalization(
        finalized_at="2026-08-07T12:00:00Z",
        finalized_by="human-editor",
        review_acknowledgement=acknowledgement,
        finding_dispositions=dispositions,
    )


def _finalized_dependencies() -> tuple[
    SourceTranscriptV2,
    ContentContextAsset,
    TranslationGlossaryAsset,
]:
    source = finalize_source(
        _source(),
        _source_finalization(),
        input_context=_input_context(),
    )
    context = ready_context_asset(_prepared_context(), source=source)
    glossary = ready_glossary_asset(_glossary(), source=source)
    return source, context, glossary


def _translation_for_dependencies(
    source: SourceTranscriptV2,
    context: ContentContextAsset,
    glossary: TranslationGlossaryAsset,
) -> TranslationDocumentV2:
    payload = _model_payload(_translation())
    payload["source_ref"] = {
        **_object(payload, "source_ref"),
        "document_id": source.document_id,
        "revision_id": source.revision_id,
        "content_digest": source.content_digest,
    }
    payload["prepared_context_ref"] = {
        **_object(payload, "prepared_context_ref"),
        "asset_id": context.asset_id,
        "revision_id": context.revision_id,
        "content_digest": context.content_digest,
    }
    payload["glossary_ref"] = {
        **_object(payload, "glossary_ref"),
        "asset_id": glossary.asset_id,
        "revision_id": glossary.revision_id,
        "content_digest": glossary.content_digest,
    }
    _object_list(payload, "cues")[0]["text"] = "Welcome back."
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    return bind_canonical_digests(TranslationDocumentV2.model_validate(payload))


def _review_for(  # noqa: PLR0913
    translation: TranslationDocumentV2,
    source: SourceTranscriptV2,
    context: ContentContextAsset,
    glossary: TranslationGlossaryAsset,
    *,
    review_id: str = "review-current",
    result: ReviewArtifactResult = ReviewArtifactResult.PASSED,
    blocking_finding: bool = False,
    supersedes_review_id: str | None = None,
) -> TranslationReviewArtifact:
    payload = _model_payload(_review())
    payload["review_id"] = review_id
    if supersedes_review_id is None:
        _ = payload.pop("supersedes_review_id", None)
    else:
        payload["supersedes_review_id"] = supersedes_review_id
    payload["binding"] = {
        "selection_id": "selection:validation-attempt",
        "attempt_id": "validation-attempt",
        "translation_document_id": translation.document_id,
        "translation_revision_id": translation.revision_id,
        "candidate_content_digest": translation.content_digest,
        "source_document_id": source.document_id,
        "source_revision_id": source.revision_id,
        "source_content_digest": source.content_digest,
        "prepared_context_asset_id": context.asset_id,
        "prepared_context_revision_id": context.revision_id,
        "prepared_context_content_digest": context.content_digest,
        "glossary_asset_id": glossary.asset_id,
        "glossary_revision_id": glossary.revision_id,
        "glossary_content_digest": glossary.content_digest,
        "review_profile": PROFILE.model_dump(mode="json"),
    }
    payload["coverage"] = {
        "source_cue_ids": [cue.cue_id for cue in source.cues],
        "target_cue_ids": [cue.cue_id for cue in translation.cues],
        "target_boundary_pairs": [
            [left.cue_id, right.cue_id]
            for left, right in zip(translation.cues, translation.cues[1:], strict=False)
        ],
        "glossary_entry_ids": [entry.entry_id for entry in glossary.entries],
    }
    payload["result"] = result.value
    if blocking_finding:
        payload["findings"] = [
            {
                "finding_id": "finding-blocking",
                "affected_source_cue_ids": [source.cues[0].cue_id],
                "affected_target_cue_ids": [translation.cues[0].cue_id],
                "domain": "naturalness",
                "severity": "major",
                "blocking": True,
                "problem": "The line is not natural English.",
                "required_change": "Rewrite the line.",
                "resolution_status": "open",
            }
        ]
    else:
        payload["findings"] = []
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    return bind_canonical_digests(TranslationReviewArtifact.model_validate(payload))


def _codes(
    report: CanonicalValidationReport | ReviewEvaluation,
) -> set[CanonicalDiagnosticCode]:
    return {item.code for item in report.diagnostics}


def test_save_validation_accepts_bound_draft_and_rejects_stale_digest() -> None:
    """Schema-valid drafts save only when both digests match current bytes."""
    translation = _translation()

    validate_canonical_save(translation)

    stale = translation.model_copy(update={"record_digest": "f" * 64})
    with pytest.raises(ValueError, match="record digest mismatch"):
        validate_canonical_save(stale)


def test_unassigned_lineage_is_saveable_but_blocks_finalization() -> None:
    """Incomplete lineage is a draft diagnostic rather than a save error."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    cue = translation.cues[0].model_copy(
        update={"source_cue_ids": (), "lineage_status": LineageStatus.UNASSIGNED}
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (cue,)})
    )

    validate_canonical_save(translation)
    draft = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )
    final = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert CanonicalDiagnosticCode.LINEAGE_UNASSIGNED in _codes(draft)
    assert not draft.has_blocking
    assert CanonicalDiagnosticCode.LINEAGE_UNASSIGNED in _codes(final)
    assert final.has_blocking


def _translation_with_two_source_turns(
    target_speakers: tuple[str, str],
) -> tuple[
    SourceTranscriptV2,
    ContentContextAsset,
    TranslationGlossaryAsset,
    TranslationDocumentV2,
]:
    return _translation_with_turn_shape(
        source_text="- 어서 오세요.\n- 감사합니다.",
        source_speakers=("한지아", "세라"),
        target_text="- Welcome.\n- Thank you.",
        target_speakers=target_speakers,
    )


def _translation_with_turn_shape(
    *,
    source_text: str,
    source_speakers: tuple[str, ...],
    target_text: str,
    target_speakers: tuple[str, ...],
) -> tuple[
    SourceTranscriptV2,
    ContentContextAsset,
    TranslationGlossaryAsset,
    TranslationDocumentV2,
]:
    source, context, glossary = _finalized_dependencies()
    source_cue = type(source.cues[0]).model_validate(
        {
            **source.cues[0].model_dump(mode="json", exclude_none=True),
            "text": source_text,
            "speakers": list(source_speakers),
        }
    )
    source = bind_canonical_digests(
        source.model_copy(
            update={
                "content_digest": "0" * 64,
                "record_digest": "0" * 64,
                "cues": (source_cue,),
            }
        )
    )
    translation = _translation_for_dependencies(source, context, glossary)
    target = translation.cues[0].model_copy(
        update={
            "text": target_text,
            "speakers": target_speakers,
        }
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (target,)})
    )
    return source, context, glossary, translation


def test_linked_source_rejects_foreign_target_speaker() -> None:
    """A target cannot assign a turn to someone absent from its source lineage."""
    source, context, glossary, translation = _translation_with_two_source_turns(
        ("한지아", "문하린")
    )

    draft = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )
    final = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert "target_speaker_not_in_source" in {
        item.code.value for item in draft.diagnostics
    }
    assert "target_speaker_not_in_source" in {
        item.code.value for item in final.diagnostics
    }
    assert final.has_blocking


def test_linked_source_rejects_reversed_displayed_speaker_order() -> None:
    """Displayed sequential turns retain the linked source speaker order."""
    source, context, glossary, translation = _translation_with_two_source_turns(
        ("세라", "한지아")
    )

    draft = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )
    final = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert "target_speaker_order_mismatch" in {
        item.code.value for item in draft.diagnostics
    }
    assert "target_speaker_order_mismatch" in {
        item.code.value for item in final.diagnostics
    }
    assert final.has_blocking


@pytest.mark.parametrize(
    (
        "source_text",
        "source_speakers",
        "target_text",
        "target_speakers",
    ),
    [
        (
            "함께 인사합니다.",
            ("한지아", "세라"),
            "- Welcome.\n- Thank you.",
            ("한지아", "세라"),
        ),
        (
            "- 어서 오세요.\n- 감사합니다.",
            ("한지아", "세라"),
            "Welcome together.",
            ("한지아", "세라"),
        ),
        (
            "- 어서 오세요.\\N- 감사합니다.",
            ("한지아", "세라"),
            "Welcome together.",
            ("한지아", "세라"),
        ),
        (
            "- 어서 오세요.\n- 감사합니다.",
            ("한지아", "세라"),
            "Welcome.",
            ("한지아",),
        ),
    ],
)
def test_linked_source_rejects_joint_sequential_turn_shape_mismatch(
    source_text: str,
    source_speakers: tuple[str, ...],
    target_text: str,
    target_speakers: tuple[str, ...],
) -> None:
    """Joint and sequential ownership shapes cannot be interchanged or omitted."""
    source, context, glossary, translation = _translation_with_turn_shape(
        source_text=source_text,
        source_speakers=source_speakers,
        target_text=target_text,
        target_speakers=target_speakers,
    )

    draft = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )
    final = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert "target_speaker_order_mismatch" in {
        item.code.value for item in draft.diagnostics
    }
    assert "target_speaker_order_mismatch" in {
        item.code.value for item in final.diagnostics
    }
    assert final.has_blocking


def test_linked_source_allows_single_speaker_visual_wrap() -> None:
    """A visual line break does not invent another speaker turn."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    target = translation.cues[0].model_copy(
        update={"text": "Welcome back.\nPlease have a seat."}
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (target,)})
    )

    draft = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )

    assert "target_speaker_order_mismatch" not in {
        item.code.value for item in draft.diagnostics
    }


def test_source_with_unresolved_speaker_cannot_finalize() -> None:
    """A placeholder speaker remains editable but cannot become source authority."""
    source = _source()
    cue = source.cues[0].model_copy(update={"speakers": ("unknown",)})
    source = bind_canonical_digests(source.model_copy(update={"cues": (cue,)}))

    with pytest.raises(CanonicalFinalizationError) as captured:
        _ = finalize_source(
            source,
            _source_finalization(),
            input_context=_input_context(),
        )

    assert CanonicalDiagnosticCode.SOURCE_SPEAKER_UNRESOLVED in _codes(
        captured.value.report
    )
    assert captured.value.report.has_blocking


def test_translation_diagnostics_report_unresolved_source_speaker() -> None:
    """Migrated finalized source placeholders remain visible to translation gates."""
    source, context, glossary = _finalized_dependencies()
    cue = source.cues[0].model_copy(update={"speakers": ("unknown",)})
    source = bind_canonical_digests(source.model_copy(update={"cues": (cue,)}))
    translation = _translation_for_dependencies(source, context, glossary)

    draft = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )
    final = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert CanonicalDiagnosticCode.SOURCE_SPEAKER_UNRESOLVED in _codes(draft)
    speaker_diagnostic = next(
        item
        for item in draft.diagnostics
        if item.code is CanonicalDiagnosticCode.SOURCE_SPEAKER_UNRESOLVED
    )
    assert speaker_diagnostic.source_cue_ids == (cue.cue_id,)
    assert CanonicalDiagnosticCode.SOURCE_SPEAKER_UNRESOLVED in _codes(final)
    assert final.has_blocking


finalized_dependencies_fixture = _finalized_dependencies
translation_for_dependencies_fixture = _translation_for_dependencies
review_for_fixture = _review_for
translation_finalization_fixture = _translation_finalization
validation_environment_fixture = _environment


def test_passed_review_rejects_wrong_acknowledgement() -> None:
    """The finalization record must describe the effective review state."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)

    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary, reviews=(review,)),
    )

    assert CanonicalDiagnosticCode.REVIEW_ACKNOWLEDGEMENT_MISMATCH in _codes(report)
    assert report.has_blocking


def test_no_review_requires_not_run_acknowledgement() -> None:
    """Human finalization without AI review remains explicit and auditable."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)

    evaluation = evaluate_review_state(translation, (), PROFILE)
    allowed = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )
    blocked = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.PASSED),
        _environment(source, context, glossary),
    )

    assert evaluation.state is EffectiveReviewState.NOT_RUN
    assert CanonicalDiagnosticCode.REVIEW_NOT_RUN in _codes(allowed)
    assert not allowed.has_blocking
    assert CanonicalDiagnosticCode.REVIEW_ACKNOWLEDGEMENT_MISMATCH in _codes(blocked)


def test_candidate_edit_makes_old_review_stale_and_requires_stale_ack() -> None:
    """Semantic candidate changes invalidate the old exact review binding."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    cue = translation.cues[0].model_copy(update={"text": "Welcome again."})
    edited = bind_canonical_digests(translation.model_copy(update={"cues": (cue,)}))

    evaluation = evaluate_review_state(edited, (review,), PROFILE)
    allowed = validate_translation_finalization(
        edited,
        _translation_finalization(ReviewAcknowledgement.STALE_ACKNOWLEDGED),
        _environment(source, context, glossary, reviews=(review,)),
    )

    assert evaluation.state is EffectiveReviewState.STALE
    assert CanonicalDiagnosticCode.REVIEW_STALE in _codes(allowed)
    assert not allowed.has_blocking


def test_failed_review_requires_every_blocking_finding_disposition() -> None:
    """A failed review has no blanket bypass for open blocking findings."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(
        translation,
        source,
        context,
        glossary,
        result=ReviewArtifactResult.FAILED,
        blocking_finding=True,
    )
    no_disposition = _translation_finalization(
        ReviewAcknowledgement.FAILED_FINDINGS_DISPOSED
    )
    disposition = FindingDisposition(
        review_id=review.review_id,
        finding_id="finding-blocking",
        reason="The human editor intentionally accepts this wording.",
    )
    accepted = _translation_finalization(
        ReviewAcknowledgement.FAILED_FINDINGS_DISPOSED,
        dispositions=(disposition,),
    )

    blocked = validate_translation_finalization(
        translation,
        no_disposition,
        _environment(source, context, glossary, reviews=(review,)),
    )
    allowed = validate_translation_finalization(
        translation,
        accepted,
        _environment(source, context, glossary, reviews=(review,)),
    )

    assert CanonicalDiagnosticCode.UNDISPOSED_BLOCKING_FINDING in _codes(blocked)
    assert blocked.has_blocking
    assert not allowed.has_blocking


def test_incomplete_failed_review_cannot_be_disposed_as_a_finding() -> None:
    """Coverage failure remains blocking even when no finding needs disposition."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(
        translation,
        source,
        context,
        glossary,
        result=ReviewArtifactResult.FAILED,
    )
    payload = _model_payload(review)
    _object(payload, "coverage")["target_cue_ids"] = []
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    review = bind_canonical_digests(TranslationReviewArtifact.model_validate(payload))

    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.FAILED_FINDINGS_DISPOSED),
        _environment(source, context, glossary, reviews=(review,)),
    )

    assert CanonicalDiagnosticCode.REVIEW_COVERAGE_INCOMPLETE in _codes(report)
    assert report.has_blocking


def test_review_conflict_blocks_even_with_stale_acknowledgement() -> None:
    """Two unsuperseded exact reviews are not silently ordered."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    first = _review_for(
        translation,
        source,
        context,
        glossary,
        review_id="review-first",
    )
    second = _review_for(
        translation,
        source,
        context,
        glossary,
        review_id="review-second",
    )

    evaluation = evaluate_review_state(translation, (first, second), PROFILE)
    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.STALE_ACKNOWLEDGED),
        _environment(source, context, glossary, reviews=(first, second)),
    )

    assert evaluation.state is EffectiveReviewState.STALE
    assert CanonicalDiagnosticCode.REVIEW_CONFLICT in _codes(evaluation)
    assert CanonicalDiagnosticCode.REVIEW_CONFLICT in _codes(report)
    assert report.has_blocking


def test_superseded_review_leaves_one_authoritative_exact_result() -> None:
    """An explicit immutable supersession chain removes false conflict."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    first = _review_for(
        translation,
        source,
        context,
        glossary,
        review_id="review-first",
        result=ReviewArtifactResult.FAILED,
        blocking_finding=True,
    )
    second = _review_for(
        translation,
        source,
        context,
        glossary,
        review_id="review-second",
        supersedes_review_id=first.review_id,
    )

    evaluation = evaluate_review_state(translation, (first, second), PROFILE)

    assert evaluation.state is EffectiveReviewState.PASSED
    assert evaluation.authoritative_review == second
    assert CanonicalDiagnosticCode.REVIEW_CONFLICT not in _codes(evaluation)


def test_missing_and_mismatched_dependencies_have_distinct_diagnostics() -> None:
    """Missing files and wrong identities remain separate recovery states."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    missing = draft_translation_diagnostics(
        translation,
        _environment(None, None, None),
    )
    changed_source = bind_canonical_digests(
        source.model_copy(update={"document_id": "other-source"})
    )
    changed_context = bind_canonical_digests(
        context.model_copy(update={"asset_id": "other-context"})
    )
    changed_glossary = bind_canonical_digests(
        glossary.model_copy(update={"asset_id": "other-glossary"})
    )
    mismatched = draft_translation_diagnostics(
        translation,
        _environment(changed_source, changed_context, changed_glossary),
    )

    assert {
        CanonicalDiagnosticCode.MISSING_SOURCE_REF,
        CanonicalDiagnosticCode.MISSING_CONTEXT_REF,
        CanonicalDiagnosticCode.MISSING_GLOSSARY_REF,
    } <= _codes(missing)
    assert {
        CanonicalDiagnosticCode.SOURCE_IDENTITY_MISMATCH,
        CanonicalDiagnosticCode.CONTEXT_IDENTITY_MISMATCH,
        CanonicalDiagnosticCode.GLOSSARY_IDENTITY_MISMATCH,
    } <= _codes(mismatched)


def test_path_only_relocation_does_not_stale_review() -> None:
    """Locator hints are excluded from review and candidate identity."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    payload = _model_payload(translation)
    _object(payload, "source_ref")["path"] = "../../relocated/source.json"
    _object(payload, "prepared_context_ref")["path"] = "../../relocated/context.json"
    _object(payload, "glossary_ref")["path"] = "../../relocated/glossary.json"
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    relocated = bind_canonical_digests(TranslationDocumentV2.model_validate(payload))

    evaluation = evaluate_review_state(relocated, (review,), PROFILE)

    assert relocated.content_digest == translation.content_digest
    assert evaluation.state is EffectiveReviewState.PASSED


def test_updating_dependency_ref_makes_old_review_stale() -> None:
    """A new source/context/glossary binding requires a new review artifact."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    payload = _model_payload(translation)
    _object(payload, "glossary_ref")["content_digest"] = "f" * 64
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    updated = bind_canonical_digests(TranslationDocumentV2.model_validate(payload))

    evaluation = evaluate_review_state(updated, (review,), PROFILE)

    assert evaluation.state is EffectiveReviewState.STALE


def test_source_coverage_order_and_timing_are_finalization_gates() -> None:
    """Confirmed lineage must resolve, preserve source order, and stay in envelope."""
    source, context, glossary = _finalized_dependencies()
    source_payload = _model_payload(source)
    source_payload["duration_ms"] = 3_000
    source_cues = _object_list(source_payload, "cues")
    source_cues.append(
        {
            **source_cues[0],
            "sequence": 2,
            "cue_id": "source:ass-dialogue:000002",
            "start_ms": 1_000,
            "end_ms": 2_000,
            "text": "노바 노바",
        }
    )
    source_payload["content_digest"] = "0" * 64
    source_payload["record_digest"] = "0" * 64
    source = bind_canonical_digests(SourceTranscriptV2.model_validate(source_payload))
    context_payload = _model_payload(context)
    _object(context_payload, "prepared_from_source_ref").update(
        {
            "document_id": source.document_id,
            "revision_id": source.revision_id,
            "content_digest": source.content_digest,
        }
    )
    context_payload["content_digest"] = "0" * 64
    context_payload["record_digest"] = "0" * 64
    context = bind_canonical_digests(
        ContentContextAsset.model_validate(context_payload)
    )
    glossary_payload = _model_payload(glossary)
    _object(glossary_payload, "prepared_from_source_ref").update(
        {
            "document_id": source.document_id,
            "revision_id": source.revision_id,
            "content_digest": source.content_digest,
        }
    )
    glossary_payload["content_digest"] = "0" * 64
    glossary_payload["record_digest"] = "0" * 64
    glossary = bind_canonical_digests(
        TranslationGlossaryAsset.model_validate(glossary_payload)
    )
    translation = _translation_for_dependencies(source, context, glossary)
    cue = translation.cues[0].model_copy(
        update={
            "source_cue_ids": (
                "source:ass-dialogue:000002",
                "source:ass-dialogue:000001",
            ),
            "start_ms": 0,
            "end_ms": 2_100,
        }
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (cue,)})
    )

    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert {
        CanonicalDiagnosticCode.SOURCE_ORDER_VIOLATION,
        CanonicalDiagnosticCode.TIMING_ENVELOPE_VIOLATION,
    } <= _codes(report)
    assert report.has_blocking

    missing_coverage_cue = cue.model_copy(
        update={
            "source_cue_ids": ("source:ass-dialogue:000001",),
            "start_ms": 0,
            "end_ms": 1_000,
        }
    )
    missing_coverage = bind_canonical_digests(
        translation.model_copy(update={"cues": (missing_coverage_cue,)})
    )
    missing_report = validate_translation_finalization(
        missing_coverage,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )
    assert CanonicalDiagnosticCode.SOURCE_COVERAGE_MISSING in _codes(missing_report)


def test_blocking_context_and_glossary_items_prevent_ready_transition() -> None:
    """Ready assets cannot retain unresolved blocking preparation decisions."""
    source = finalize_source(
        _source(),
        _source_finalization(),
        input_context=_input_context(),
    )
    context_payload = _model_payload(_prepared_context())
    _object_list(context_payload, "facts")[0].update(
        {"decision_status": "unresolved", "blocking": True}
    )
    context_payload["content_digest"] = "0" * 64
    context_payload["record_digest"] = "0" * 64
    context = bind_canonical_digests(
        ContentContextAsset.model_validate(context_payload)
    )
    glossary_payload = _model_payload(_glossary())
    glossary_entry = _object_list(glossary_payload, "entries")[0]
    glossary_entry.update({"decision_status": "unresolved", "blocking": True})
    _ = glossary_entry.pop("preferred_target")
    glossary_payload["content_digest"] = "0" * 64
    glossary_payload["record_digest"] = "0" * 64
    glossary = bind_canonical_digests(
        TranslationGlossaryAsset.model_validate(glossary_payload)
    )

    with pytest.raises(CanonicalFinalizationError) as context_error:
        _ = ready_context_asset(context, source=source)
    with pytest.raises(CanonicalFinalizationError) as glossary_error:
        _ = ready_glossary_asset(glossary, source=source)

    assert CanonicalDiagnosticCode.BLOCKING_CONTEXT_FACT in _codes(
        context_error.value.report
    )
    assert CanonicalDiagnosticCode.BLOCKING_GLOSSARY_ENTRY in _codes(
        glossary_error.value.report
    )


def test_finalized_source_copy_on_edit_creates_new_draft_revision() -> None:
    """Source edits use copy-on-edit rather than overwriting finalized history."""
    source = finalize_source(
        _source(),
        _source_finalization(),
        input_context=_input_context(),
    )

    draft = create_source_draft_revision(source, revision_id="source-next")

    assert draft.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert draft.finalization is None
    assert draft.supersedes_revision_id == source.revision_id
    assert draft.content_digest == source.content_digest
    assert draft.record_digest != source.record_digest


def test_review_evaluation_never_mutates_translation_lifecycle() -> None:
    """AI review state is computed outside the canonical translation document."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)

    _ = evaluate_review_state(translation, (review,), PROFILE)

    assert translation.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert translation.finalization is None


def test_open_translation_decision_blocks_finalization() -> None:
    """Lifecycle draft and decision needs-confirmation remain independent states."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    payload = _model_payload(translation)
    payload["decision_status"] = "needs_confirmation"
    payload["unresolved_decisions"] = [
        {
            "decision_id": "decision-rina",
            "source_cue_ids": [source.cues[0].cue_id],
            "target_cue_ids": [translation.cues[0].cue_id],
            "domain": "culture",
            "question": "Keep the nickname Rina?",
            "options": [
                {
                    "option_id": "rina",
                    "text": "Rina",
                    "meaning": "Use the nickname heard in the source.",
                    "risk": "It differs from the official stage name.",
                }
            ],
            "recommendation_id": "rina",
        }
    ]
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    translation = bind_canonical_digests(TranslationDocumentV2.model_validate(payload))

    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert CanonicalDiagnosticCode.UNRESOLVED_TRANSLATION_DECISION in _codes(report)
    assert report.has_blocking


def test_review_profile_version_change_makes_prior_review_stale() -> None:
    """Review policy identity is part of the exact review binding."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    newer_profile = ReviewProfile(
        profile_id=PROFILE.profile_id,
        version="3.0",
    )

    evaluation = evaluate_review_state(translation, (review,), newer_profile)

    assert evaluation.state is EffectiveReviewState.STALE
    assert CanonicalDiagnosticCode.REVIEW_STALE in _codes(evaluation)


def test_invalid_review_digest_is_reported_and_cannot_authorize_finalization() -> None:
    """A tampered review artifact never becomes the authoritative exact review."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    invalid_review = review.model_copy(update={"record_digest": "f" * 64})

    evaluation = evaluate_review_state(translation, (invalid_review,), PROFILE)
    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary, reviews=(invalid_review,)),
    )

    assert CanonicalDiagnosticCode.REVIEW_INVALID in _codes(evaluation)
    assert evaluation.has_blocking
    assert CanonicalDiagnosticCode.REVIEW_INVALID in _codes(report)
    assert report.has_blocking


def test_nonportable_evidence_locator_is_a_draft_warning() -> None:
    """Absolute local evidence locations are visible without invalidating content."""
    source, context, glossary = _finalized_dependencies()
    payload = _model_payload(source)
    _object(payload, "evidence")["base_uri"] = "file:///Users/editor/project/evidence/"
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    source = bind_canonical_digests(SourceTranscriptV2.model_validate(payload))
    translation = _translation_for_dependencies(source, context, glossary)

    report = draft_translation_diagnostics(
        translation,
        _environment(source, context, glossary),
    )

    assert CanonicalDiagnosticCode.NONPORTABLE_EVIDENCE_LOCATOR in _codes(report)
    assert not report.has_blocking


def test_unknown_target_context_and_evidence_references_block_finalization() -> None:
    """Target metadata must resolve in the selected context and source evidence."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    cue = translation.cues[0].model_copy(
        update={
            "context_ids": ("missing-context",),
            "evidence_ids": ("missing-evidence",),
        }
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (cue,)})
    )

    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(source, context, glossary),
    )

    assert {
        CanonicalDiagnosticCode.UNKNOWN_CONTEXT_ID,
        CanonicalDiagnosticCode.UNKNOWN_EVIDENCE_ID,
    } <= _codes(report)
    assert report.has_blocking


def test_prepared_asset_scope_must_resolve_in_bound_source() -> None:
    """Ready assets cannot retain cue scopes from another source revision."""
    source = finalize_source(
        _source(),
        _source_finalization(),
        input_context=_input_context(),
    )
    context_payload = _model_payload(_prepared_context())
    _object_list(context_payload, "facts")[0]["scope_source_cue_ids"] = [
        "missing-source"
    ]
    context_payload["content_digest"] = "0" * 64
    context_payload["record_digest"] = "0" * 64
    context = bind_canonical_digests(
        ContentContextAsset.model_validate(context_payload)
    )
    glossary_payload = _model_payload(_glossary())
    _object_list(glossary_payload, "entries")[0]["scope_source_cue_ids"] = [
        "missing-source"
    ]
    glossary_payload["content_digest"] = "0" * 64
    glossary_payload["record_digest"] = "0" * 64
    glossary = bind_canonical_digests(
        TranslationGlossaryAsset.model_validate(glossary_payload)
    )

    with pytest.raises(CanonicalFinalizationError) as context_error:
        _ = ready_context_asset(context, source=source)
    with pytest.raises(CanonicalFinalizationError) as glossary_error:
        _ = ready_glossary_asset(glossary, source=source)

    assert CanonicalDiagnosticCode.CONTEXT_SCOPE_UNKNOWN_SOURCE in _codes(
        context_error.value.report
    )
    assert CanonicalDiagnosticCode.GLOSSARY_SCOPE_UNKNOWN_SOURCE in _codes(
        glossary_error.value.report
    )


def test_source_finalization_requires_the_exact_input_context_revision() -> None:
    """Source finalization never silently accepts a different input context."""
    input_context = bind_canonical_digests(
        _input_context().model_copy(update={"asset_id": "other-input-context"})
    )

    with pytest.raises(CanonicalFinalizationError) as error:
        _ = finalize_source(
            _source(),
            _source_finalization(),
            input_context=input_context,
        )

    assert CanonicalDiagnosticCode.CONTEXT_IDENTITY_MISMATCH in _codes(
        error.value.report
    )


def test_target_display_order_is_a_save_validation_error() -> None:
    """A canonical file cannot persist target rows outside display order."""
    translation = _translation()
    first = translation.cues[0].model_copy(
        update={"sequence": 1, "start_ms": 500, "end_ms": 900}
    )
    second = translation.cues[0].model_copy(
        update={
            "sequence": 2,
            "cue_id": "target:000002",
            "start_ms": 0,
            "end_ms": 400,
        }
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (first, second)})
    )

    with pytest.raises(ValueError, match="display order"):
        validate_canonical_save(translation)


def test_invalid_dependency_digest_blocks_translation_finalization() -> None:
    """Matching ref strings do not excuse a tampered dependency artifact."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    invalid_source = source.model_copy(update={"record_digest": "f" * 64})

    report = validate_translation_finalization(
        translation,
        _translation_finalization(ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED),
        _environment(invalid_source, context, glossary),
    )

    assert CanonicalDiagnosticCode.RECORD_DIGEST_MISMATCH in _codes(report)
    assert report.has_blocking


def test_save_validation_rechecks_model_invariants_after_model_copy() -> None:
    """A model_copy shortcut cannot persist an invalid lifecycle shape."""
    translation = _translation().model_copy(
        update={"lifecycle_status": DocumentLifecycleStatus.FINALIZED}
    )
    translation = bind_canonical_digests(translation)

    with pytest.raises(ValueError, match="finalized translation requires finalization"):
        validate_canonical_save(translation)


def test_invalid_review_for_another_document_does_not_block_this_translation() -> None:
    """Review discovery ignores invalid artifacts unrelated to the current document."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    unrelated_payload = _model_payload(review)
    unrelated_payload["review_id"] = "review-unrelated"
    _object(unrelated_payload, "binding")["translation_document_id"] = "other-document"
    unrelated_payload["content_digest"] = "0" * 64
    unrelated_payload["record_digest"] = "0" * 64
    unrelated = bind_canonical_digests(
        TranslationReviewArtifact.model_validate(unrelated_payload)
    ).model_copy(update={"record_digest": "f" * 64})

    evaluation = evaluate_review_state(translation, (review, unrelated), PROFILE)

    assert evaluation.state is EffectiveReviewState.PASSED
    assert CanonicalDiagnosticCode.REVIEW_INVALID not in _codes(evaluation)
    assert not evaluation.has_blocking
