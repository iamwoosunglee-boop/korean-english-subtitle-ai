# Copyright 2026 Transcription Module contributors

"""End-to-end proof for the scratch canonical workflow."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel

from tests.e2e.canonical_workflow_finalization_support import (
    run_finalization_workflow,
)
from tests.e2e.canonical_workflow_media_support import run_media_workflow
from tests.e2e.canonical_workflow_regression_support import (
    EXPECTED_CASES,
    FIXTURE_PATH,
    RegressionFixture,
)
from tests.e2e.canonical_workflow_review_support import run_review_workflow
from tests.e2e.canonical_workflow_source_support import run_source_workflow
from tests.e2e.canonical_workflow_translation_fixture_support import successful_attempt
from tests.e2e.canonical_workflow_translation_support import (
    checker_evidence,
    run_translation_workflow,
    selection_receipt,
)
from translation_module.canonical.attempts import SelectionBindingError
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.finalization_authority import (
    finalize_translation_core,
)
from translation_module.canonical.preflight import (
    CandidatePreflightContext,
    PreflightReport,
    build_preflight_report,
    write_preflight_report,
)
from translation_module.canonical.review import ReviewProfile
from translation_module.canonical.runtime import (
    CanonicalTranslationWorkflow,
    finalize_selected_translation,
)
from translation_module.canonical.translation import (
    ReviewAcknowledgement,
    TranslationDocumentV2,
    TranslationFinalization,
)
from translation_module.canonical.validation import (
    TranslationValidationContext,
    create_translation_draft_revision,
)

CLI_ERROR_EXIT = 2


def _exercise_finalization_cli(
    tmp_path: Path,
    inputs: dict[str, BaseModel],
    authority_root: Path,
) -> TranslationDocumentV2:
    paths = {name: tmp_path / f"{name}.json" for name in inputs}
    for name, artifact in inputs.items():
        _ = paths[name].write_text(artifact.model_dump_json(), encoding="utf-8")
    output = tmp_path / "finalized-translation.json"
    arguments = [
        "uv",
        "run",
        "translation-cli",
        "finalize-translation",
        *(str(path) for path in paths.values()),
        "--review-profile",
        "editorial-review",
        "1",
        "--output",
        str(output),
    ]
    missing = subprocess.run(
        [*arguments, "--authority-root", str(tmp_path / "missing-authority")],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert missing.returncode == CLI_ERROR_EXIT
    assert not output.exists()
    completed = subprocess.run(
        [*arguments, "--authority-root", str(authority_root)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    finalized = TranslationDocumentV2.model_validate_json(output.read_bytes())
    output.unlink()
    linked_root = tmp_path / "finalization-authority-link"
    linked_root.symlink_to(authority_root, target_is_directory=True)
    linked = subprocess.run(
        [*arguments, "--authority-root", str(linked_root)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert linked.returncode == CLI_ERROR_EXIT
    assert not output.exists()
    return finalized


def test_canonical_scratch_workflow_completes_end_to_end(tmp_path: Path) -> None:
    """Given fixed adapters, every canonical stage fails closed then completes."""
    fixture = RegressionFixture.model_validate_json(FIXTURE_PATH.read_bytes())
    assert set(fixture.cases) == EXPECTED_CASES

    confirmed = run_source_workflow(tmp_path / "source")
    finalized = run_finalization_workflow(confirmed)
    translated = run_translation_workflow(finalized, tmp_path / "translation")
    reviewed = run_review_workflow(translated, tmp_path / "review")
    profile = ReviewProfile(profile_id="editorial-review", version="1")
    finalization = TranslationFinalization(
        finalized_at="2026-08-18T12:30:00Z",
        finalized_by="human-editor",
        review_acknowledgement=ReviewAcknowledgement.PASSED,
        finding_dispositions=(),
    )
    cli_finalized = _exercise_finalization_cli(
        tmp_path,
        {
            "translation": translated.candidate,
            "source": translated.source,
            "context": translated.context,
            "glossary": translated.glossary,
            "review": reviewed.review,
            "finalization": finalization,
        },
        translated.authority_root,
    )
    assert cli_finalized.lifecycle_status.value == "finalized"

    finalized_translation = finalize_selected_translation(
        translated.authority_root,
        translated.candidate.document_id,
        translated.candidate.revision_id,
        finalization,
        TranslationValidationContext(
            source=translated.source,
            context=translated.context,
            glossary=translated.glossary,
            reviews=(reviewed.review,),
            review_profile=profile,
        ),
    )
    draft = create_translation_draft_revision(
        finalized_translation, revision_id="translation-revision-c"
    )
    revised = bind_canonical_digests(
        draft.model_copy(
            update={
                "cues": (
                    draft.cues[0].model_copy(update={"text": "Revised line 1."}),
                    *draft.cues[1:],
                )
            }
        )
    )
    workflow = CanonicalTranslationWorkflow(translated.authority_root)
    _ = workflow.record_attempt(successful_attempt(revised, "attempt-d", 4), revised)
    revision_context = CandidatePreflightContext(
        revised, translated.source, translated.glossary
    )
    revision_preflight = build_preflight_report(
        revision_context, checker_evidence(revised)
    )
    _ = write_preflight_report(workflow.preflight_path(revised), revision_preflight)
    with pytest.raises(SelectionBindingError, match="selection receipt"):
        _ = finalize_translation_core(
            translated.authority_root,
            revised.document_id,
            revised.revision_id,
            finalization,
            TranslationValidationContext(
                source=translated.source,
                context=translated.context,
                glossary=translated.glossary,
                reviews=(),
                review_profile=profile,
            ),
        )
    with pytest.raises(SelectionBindingError, match="selection receipt"):
        _ = finalize_selected_translation(
            translated.authority_root,
            revised.document_id,
            revised.revision_id,
            finalization,
            TranslationValidationContext(
                source=translated.source,
                context=translated.context,
                glossary=translated.glossary,
                reviews=(),
                review_profile=profile,
            ),
        )
    workflow.preflight_and_authorize(
        revision_context,
        checker_evidence(revised),
        selection_receipt(revised, "attempt-d"),
    )
    revision_report = PreflightReport.model_validate_json(
        workflow.preflight_path(revised).read_bytes()
    )
    revised_workflow = replace(
        translated,
        candidate=revised,
        preflight=revision_report,
        selection_path=workflow.attempt_store.selection_path(
            revised.document_id, revised.revision_id
        ),
    )
    revised_review = run_review_workflow(revised_workflow, tmp_path / "review-revision")
    finalized_revision = finalize_selected_translation(
        translated.authority_root,
        revised.document_id,
        revised.revision_id,
        finalization.model_copy(update={"finalized_at": "2026-08-18T13:00:00Z"}),
        TranslationValidationContext(
            source=translated.source,
            context=translated.context,
            glossary=translated.glossary,
            reviews=(revised_review.review,),
            review_profile=profile,
        ),
    )
    media = run_media_workflow(tmp_path / "media")

    assert confirmed.observations["answer_one_closures"] == 1
    assert confirmed.observations["incomplete_closure_blocked"] is True
    assert finalized.observations["rejection_preserved"] is True
    assert finalized.observations["stale_preserved"] is True
    assert translated.observations["selected_attempt"] == "attempt-b"
    assert translated.observations["candidate_unchanged"] is True
    assert finalized_translation.lifecycle_status.value == "finalized"
    assert finalized_revision.lifecycle_status.value == "finalized"
    assert tuple(
        item.binding.translation_revision_id for item in workflow.attempt_store.load()
    ) == (
        "translation-revision-b",
        "translation-revision-b",
        "translation-revision-b",
        "translation-revision-c",
    )
    assert reviewed.observations["read_ledger"] == [
        "frames/frame-a.jpg",
        "frames/frame-b.jpg",
    ]
    assert reviewed.observations["reviewer_input_has_rationale"] is False
    assert media.observations["frames_only_resume"] is True
    assert media.observations["audio_only_repair"] is True
