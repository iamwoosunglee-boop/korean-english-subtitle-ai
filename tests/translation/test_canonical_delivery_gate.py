# Copyright 2026 Transcription Module contributors

"""Finalized-only delivery rejects stale authority without filesystem mutation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest

from tests.translation.canonical_delivery_support import (
    DeliveryCase,
    delivery_case,
    stored_bytes,
)
from tests.translation.canonical_finalization_test_support import (
    translation_finalization_fixture,
    validation_environment_fixture,
)
from translation_module.canonical.delivery import assemble_selected_delivery
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.runtime import finalize_selected_translation
from translation_module.canonical.translation import ReviewAcknowledgement

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(params=[False, True], ids=["new-output", "existing-output"])
def output_dir(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Exercise both absent outputs and a previously delivered bundle."""
    output = tmp_path / "delivery"
    if cast("bool", request.param):
        case = delivery_case(tmp_path / "previous-authority")
        _ = assemble_selected_delivery(
            case.workflow.root,
            case.identity,
            case.dependencies,
            output,
            finalized_document=case.finalized,
        )
    return output


def assert_delivery_rejected(case: DeliveryCase, output: Path) -> None:
    """Reject at the public seam without changing authority or delivered files."""
    authority_before = stored_bytes(case.workflow.root)
    output_before = stored_bytes(output)
    existed = output.exists()
    with pytest.raises(ValueError, match=r"."):
        _ = assemble_selected_delivery(
            case.workflow.root,
            case.identity,
            case.dependencies,
            output,
            finalized_document=case.finalized,
        )
    assert output.exists() == existed
    assert stored_bytes(output) == output_before
    assert stored_bytes(case.workflow.root) == authority_before


def test_draft_preserves_existing_delivery(tmp_path: Path, output_dir: Path) -> None:
    """A selected draft never overwrites a prior valid delivery."""
    case = delivery_case(tmp_path / "authority")
    assert_delivery_rejected(replace(case, finalized=case.candidate), output_dir)


@pytest.mark.parametrize("stale", [False, True])
def test_review_acknowledgement_cannot_replace_current_review(
    tmp_path: Path,
    output_dir: Path,
    *,
    stale: bool,
) -> None:
    """Even a legitimately finalized unreviewed/stale record is not deliverable."""
    case = delivery_case(tmp_path / "authority")
    reviews = ()
    acknowledgement = ReviewAcknowledgement.NOT_RUN_ACKNOWLEDGED
    if stale:
        review = case.dependencies.reviews[0]
        reviews = (
            bind_canonical_digests(
                review.model_copy(
                    update={
                        "binding": review.binding.model_copy(
                            update={"translation_revision_id": "old-revision"}
                        )
                    }
                )
            ),
        )
        acknowledgement = ReviewAcknowledgement.STALE_ACKNOWLEDGED
    dependencies = replace(case.dependencies, reviews=reviews)
    finalized = finalize_selected_translation(
        case.workflow.root,
        case.identity.document_id,
        case.identity.revision_id,
        translation_finalization_fixture(acknowledgement),
        validation_environment_fixture(
            dependencies.source,
            dependencies.context,
            dependencies.glossary,
            reviews=reviews,
        ),
    )
    assert_delivery_rejected(
        replace(case, dependencies=dependencies, finalized=finalized),
        output_dir,
    )


@pytest.mark.parametrize(
    "field",
    ["source_cue_ids", "target_cue_ids", "glossary_entry_ids"],
)
def test_incomplete_review_cannot_deliver(
    tmp_path: Path,
    output_dir: Path,
    field: str,
) -> None:
    """Exact review bindings cannot conceal missing independent coverage."""
    case = delivery_case(tmp_path / "authority")
    review = case.dependencies.reviews[0]
    incomplete = bind_canonical_digests(
        review.model_copy(
            update={"coverage": review.coverage.model_copy(update={field: ()})}
        )
    )
    finalization = translation_finalization_fixture(
        ReviewAcknowledgement.FAILED_FINDINGS_DISPOSED,
    )
    finalized = bind_canonical_digests(
        case.finalized.model_copy(update={"finalization": finalization})
    )
    assert_delivery_rejected(
        replace(
            case,
            finalized=finalized,
            dependencies=replace(case.dependencies, reviews=(incomplete,)),
        ),
        output_dir,
    )


@pytest.mark.parametrize("field", ["selection_id", "attempt_id"])
def test_review_must_bind_selected_attempt(
    tmp_path: Path,
    output_dir: Path,
    field: str,
) -> None:
    """Content-identical review evidence cannot cross selected authority."""
    case = delivery_case(tmp_path / "authority")
    review = case.dependencies.reviews[0]
    foreign = bind_canonical_digests(
        review.model_copy(
            update={"binding": review.binding.model_copy(update={field: "foreign"})}
        )
    )
    assert_delivery_rejected(
        replace(case, dependencies=replace(case.dependencies, reviews=(foreign,))),
        output_dir,
    )


@pytest.mark.parametrize(
    "field",
    ["document_id", "revision_id", "supersedes_revision_id"],
)
def test_finalized_record_must_exactly_match_selected_record(
    tmp_path: Path,
    output_dir: Path,
    field: str,
) -> None:
    """Valid digests and identical subtitle content do not prove record identity."""
    case = delivery_case(tmp_path / "authority")
    foreign = bind_canonical_digests(
        case.finalized.model_copy(update={field: "foreign"})
    )
    assert foreign.content_digest == case.finalized.content_digest
    assert_delivery_rejected(replace(case, finalized=foreign), output_dir)


def test_changed_final_content_cannot_deliver(tmp_path: Path, output_dir: Path) -> None:
    """A caller cannot substitute digest-valid final text for the reviewed draft."""
    case = delivery_case(tmp_path / "authority")
    finalized = bind_canonical_digests(
        case.finalized.model_copy(
            update={
                "cues": (
                    case.finalized.cues[0].model_copy(update={"text": "Changed."}),
                )
            }
        )
    )
    assert_delivery_rejected(replace(case, finalized=finalized), output_dir)


@pytest.mark.parametrize("field", ["content_digest", "record_digest"])
def test_invalid_final_digest_cannot_deliver(
    tmp_path: Path,
    output_dir: Path,
    field: str,
) -> None:
    """Caller supplied finalization must retain both canonical digests."""
    case = delivery_case(tmp_path / "authority")
    invalid = case.finalized.model_copy(update={field: "f" * 64})
    assert_delivery_rejected(replace(case, finalized=invalid), output_dir)


@pytest.mark.parametrize("field", ["source", "context", "glossary"])
def test_stale_dependency_cannot_deliver(
    tmp_path: Path,
    output_dir: Path,
    field: str,
) -> None:
    """The final record remains bound to every current canonical dependency."""
    case = delivery_case(tmp_path / "authority")
    dependency = {
        "source": case.dependencies.source,
        "context": case.dependencies.context,
        "glossary": case.dependencies.glossary,
    }[field]
    stale = bind_canonical_digests(
        dependency.model_copy(update={"revision_id": "stale-revision"})
    )
    dependencies = replace(case.dependencies, **{field: stale})
    assert_delivery_rejected(replace(case, dependencies=dependencies), output_dir)


def test_wrong_review_profile_cannot_deliver(tmp_path: Path, output_dir: Path) -> None:
    """An exact review under another policy cannot authorize delivery."""
    case = delivery_case(tmp_path / "authority")
    profile = case.dependencies.review_profile.model_copy(update={"version": "other"})
    assert_delivery_rejected(
        replace(case, dependencies=replace(case.dependencies, review_profile=profile)),
        output_dir,
    )


def test_failed_render_parity_never_publishes(tmp_path: Path, output_dir: Path) -> None:
    """A real SBV round-trip mismatch cannot create or overwrite a delivery."""
    case = delivery_case(tmp_path / "authority", text="Welcome back. ")
    assert_delivery_rejected(case, output_dir)
