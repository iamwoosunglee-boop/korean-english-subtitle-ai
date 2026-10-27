# Copyright 2026 Transcription Module contributors

"""Canonical finalization selected-authority state scenarios."""

from pathlib import Path

import pytest

from tests.e2e.canonical_workflow_translation_fixture_support import (
    successful_attempt,
)
from tests.e2e.canonical_workflow_translation_support import (
    checker_evidence,
    selection_receipt,
)
from tests.translation.canonical_finalization_test_support import (
    finalized_dependencies_fixture as _finalized_dependencies,
)
from tests.translation.canonical_finalization_test_support import (
    review_for_fixture as _review_for,
)
from tests.translation.canonical_finalization_test_support import (
    selected_authority_fixture as _selected_authority,
)
from tests.translation.canonical_finalization_test_support import (
    translation_finalization_fixture as _translation_finalization,
)
from tests.translation.canonical_finalization_test_support import (
    translation_for_dependencies_fixture as _translation_for_dependencies,
)
from tests.translation.canonical_finalization_test_support import (
    validation_environment_fixture as _environment,
)
from translation_module.canonical import (
    CanonicalDiagnosticCode,
    CanonicalFinalizationError,
    CanonicalValidationReport,
    DocumentLifecycleStatus,
    EffectiveReviewState,
    LineageStatus,
    ReviewAcknowledgement,
    ReviewEvaluation,
    ReviewProfile,
    bind_canonical_digests,
    create_translation_draft_revision,
    evaluate_review_state,
    verify_canonical_digests,
)
from translation_module.canonical.attempts import SelectionBindingError
from translation_module.canonical.finalization_authority import (
    finalize_translation_core as finalize_translation,
)
from translation_module.canonical.preflight import CandidatePreflightContext
from translation_module.canonical.runtime import CanonicalTranslationWorkflow

PROFILE = ReviewProfile(profile_id="en-us-editorial-review", version="2.0")


def _codes(
    evaluation: CanonicalValidationReport | ReviewEvaluation,
) -> set[CanonicalDiagnosticCode]:
    return {item.code for item in evaluation.diagnostics}


def test_exact_passed_review_requires_passed_acknowledgement_and_finalizes(
    tmp_path: Path,
) -> None:
    """A clean exact review permits human finalization without changing content."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    finalization = _translation_finalization(ReviewAcknowledgement.PASSED)

    evaluation = evaluate_review_state(translation, (review,), PROFILE)
    finalized = finalize_translation(
        *_selected_authority(tmp_path, translation, source, glossary),
        finalization,
        _environment(source, context, glossary, reviews=(review,)),
    )

    assert evaluation.state is EffectiveReviewState.PASSED
    assert finalized.lifecycle_status is DocumentLifecycleStatus.FINALIZED
    assert finalized.content_digest == translation.content_digest
    assert finalized.record_digest != translation.record_digest
    verify_canonical_digests(finalized)


def test_finalization_rejects_review_bound_to_unselected_identical_attempt(
    tmp_path: Path,
) -> None:
    """A candidate-identical review cannot move from attempt A to selected B."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    workflow = CanonicalTranslationWorkflow(tmp_path)
    first = successful_attempt(translation, "attempt-a", 1)
    second = successful_attempt(translation, "attempt-b", 2)
    _ = workflow.record_attempt(first, translation)
    _ = workflow.record_attempt(second, translation)
    graph = CandidatePreflightContext(translation, source, glossary)
    workflow.preflight_and_authorize(
        graph,
        checker_evidence(translation),
        selection_receipt(translation, second.attempt_id),
    )
    review = _review_for(translation, source, context, glossary)
    review = bind_canonical_digests(
        review.model_copy(
            update={
                "binding": review.binding.model_copy(
                    update={
                        "selection_id": "selection:attempt-a",
                        "attempt_id": first.attempt_id,
                    }
                )
            }
        )
    )

    with pytest.raises(SelectionBindingError, match="selected attempt"):
        _ = finalize_translation(
            tmp_path,
            translation.document_id,
            translation.revision_id,
            _translation_finalization(ReviewAcknowledgement.PASSED),
            _environment(source, context, glossary, reviews=(review,)),
        )


def test_finalized_translation_copy_on_edit_creates_new_draft_revision(
    tmp_path: Path,
) -> None:
    """Editing a finalized revision preserves history and invalidates old review."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    review = _review_for(translation, source, context, glossary)
    finalized = finalize_translation(
        *_selected_authority(tmp_path, translation, source, glossary),
        _translation_finalization(ReviewAcknowledgement.PASSED),
        _environment(source, context, glossary, reviews=(review,)),
    )

    draft = create_translation_draft_revision(finalized, revision_id="revision-next")
    evaluation = evaluate_review_state(draft, (review,), PROFILE)

    assert draft.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert draft.finalization is None
    assert draft.revision_id == "revision-next"
    assert draft.supersedes_revision_id == finalized.revision_id
    assert draft.content_digest == finalized.content_digest
    assert draft.record_digest != finalized.record_digest
    assert evaluation.state is EffectiveReviewState.STALE


def test_finalize_translation_raises_with_the_complete_blocking_report(
    tmp_path: Path,
) -> None:
    """Mutation helpers fail atomically and expose every finalization blocker."""
    source, context, glossary = _finalized_dependencies()
    translation = _translation_for_dependencies(source, context, glossary)
    cue = translation.cues[0].model_copy(
        update={"source_cue_ids": (), "lineage_status": LineageStatus.UNASSIGNED}
    )
    translation = bind_canonical_digests(
        translation.model_copy(update={"cues": (cue,)})
    )

    with pytest.raises(CanonicalFinalizationError) as error:
        _ = finalize_translation(
            *_selected_authority(tmp_path, translation, source, glossary),
            _translation_finalization(ReviewAcknowledgement.PASSED),
            _environment(source, context, glossary),
        )

    assert error.value.report.has_blocking
    assert {
        CanonicalDiagnosticCode.LINEAGE_UNASSIGNED,
        CanonicalDiagnosticCode.REVIEW_ACKNOWLEDGEMENT_MISMATCH,
    } <= _codes(error.value.report)
