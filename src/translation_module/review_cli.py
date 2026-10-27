# Copyright 2026 Transcription Module contributors

"""CLI command for assembling canonical review artifacts."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from translation_module import cli_options
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.review_assembly import (
    ReviewAssemblyError,
    ReviewAssemblyManifest,
)
from translation_module.canonical.runtime import (
    ReviewAssemblyInputs,
    TranslationReviewHarnessError,
    assemble_selected_review,
    build_translation_review_harness,
)
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.cli_io import read_json, write_model_atomic
from translation_module.cli_output import echo_json as _echo
from translation_module.cli_output import fail as _fail
from translation_module.preparation import TranslationBatchPlan

AuthorityRoot = Annotated[
    Path, typer.Option("--authority-root", help="Attempt/preflight authority root.")
]


def _prepare_shared_review_harness_command(
    paths: Annotated[
        tuple[Path, Path, Path],
        typer.Argument(metavar="SOURCE TRANSLATION PLAN"),
    ],
    output: cli_options.Output,
) -> None:
    try:
        source_path, translation_path, plan_path = paths
        source = SourceTranscriptV2.model_validate_json(read_json(source_path))
        translation = TranslationDocumentV2.model_validate_json(
            read_json(translation_path)
        )
        plan = TranslationBatchPlan.model_validate_json(read_json(plan_path))
        harness = build_translation_review_harness(source, translation, plan)
        write_model_atomic(output, harness)
    except (
        OSError,
        TranslationReviewHarnessError,
        ValidationError,
        ValueError,
    ) as error:
        _fail("shared_review_harness_failed", error)
    _echo(
        plan_id=harness.plan_id,
        chunk_count=len(harness.chunks),
        candidate_content_digest=harness.candidate_content_digest,
        output=str(output),
    )


def _assemble_review_command(
    paths: Annotated[
        tuple[Path, Path, Path, Path, Path],
        typer.Argument(metavar="TRANSLATION SOURCE CONTEXT GLOSSARY MANIFEST"),
    ],
    output: cli_options.Output,
    authority_root: AuthorityRoot,
) -> None:
    translation_path, source_path, context_path, glossary_path, manifest_path = paths
    try:
        translation = TranslationDocumentV2.model_validate_json(
            read_json(translation_path)
        )
        source = SourceTranscriptV2.model_validate_json(read_json(source_path))
        context = ContentContextAsset.model_validate_json(read_json(context_path))
        glossary = TranslationGlossaryAsset.model_validate_json(
            read_json(glossary_path)
        )
        manifest = ReviewAssemblyManifest.model_validate_json(read_json(manifest_path))
        review = assemble_selected_review(
            authority_root,
            translation.document_id,
            translation.revision_id,
            ReviewAssemblyInputs(source, context, glossary, manifest),
        )
        write_model_atomic(output, review)
    except (OSError, ReviewAssemblyError, ValidationError, ValueError) as error:
        _fail("review_assembly_failed", error)
    _echo(
        review_id=review.review_id,
        result=review.result.value,
        content_digest=review.content_digest,
        output=str(output),
    )


def register_review_commands(app: typer.Typer) -> None:
    """Register canonical review assembly commands."""
    _ = app.command("prepare-shared-review-harness")(
        _prepare_shared_review_harness_command
    )
    _ = app.command("assemble-review")(_assemble_review_command)
