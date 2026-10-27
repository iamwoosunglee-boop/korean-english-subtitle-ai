# Copyright 2026 Transcription Module contributors

"""Selected canonical translation delivery CLI command."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter, ValidationError

from translation_module import cli_options
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.delivery import (
    DELIVERY_PARITY_NAME,
    DeliveryDependencies,
    ReviewTimecodeSpec,
    SelectedTranslationIdentity,
    assemble_selected_delivery,
)
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.review import ReviewProfile, TranslationReviewArtifact
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.cli_io import read_json
from translation_module.cli_output import echo_json, fail

DeliveryPaths = Annotated[
    tuple[Path, Path, Path, Path, Path, Path],
    typer.Argument(metavar="FINALIZED SOURCE CONTEXT GLOSSARY REVIEW TIMECODE_SPEC"),
]
OutputDirectoryOption = Annotated[Path, typer.Option("--output-dir")]
_TIMECODE_SPEC = TypeAdapter(ReviewTimecodeSpec)


def assemble_delivery_command(
    paths: DeliveryPaths,
    authority_root: cli_options.AuthorityRoot,
    output_dir: OutputDirectoryOption,
    review_profile: cli_options.ReviewProfileIdentity,
) -> None:
    """Assemble all selected delivery formats and their parity report."""
    (
        finalized_path,
        source_path,
        context_path,
        glossary_path,
        review_path,
        timecode_path,
    ) = paths
    try:
        source = SourceTranscriptV2.model_validate_json(read_json(source_path))
        glossary = TranslationGlossaryAsset.model_validate_json(
            read_json(glossary_path)
        )
        review_timecode = _TIMECODE_SPEC.validate_json(read_json(timecode_path))
        finalized = TranslationDocumentV2.model_validate_json(read_json(finalized_path))
        prepared_context = ContentContextAsset.model_validate_json(
            read_json(context_path)
        )
        review = TranslationReviewArtifact.model_validate_json(read_json(review_path))
        profile_id, version = review_profile
        report = assemble_selected_delivery(
            authority_root,
            SelectedTranslationIdentity(finalized.document_id, finalized.revision_id),
            DeliveryDependencies(
                source,
                glossary,
                review_timecode,
                prepared_context,
                (review,),
                ReviewProfile(profile_id=profile_id, version=version),
            ),
            output_dir,
            finalized_document=finalized,
        )
    except (OSError, ValidationError, ValueError) as error:
        fail("delivery_assembly_failed", error)
    echo_json(
        matches=report.matches,
        attempt_id=report.attempt_id,
        output=str(output_dir / DELIVERY_PARITY_NAME),
    )


__all__ = ["assemble_delivery_command"]
