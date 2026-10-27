# Copyright 2026 Transcription Module contributors

"""CLI commands for preparing canonical translation assets."""

import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from translation_module.canonical.asset_preparation import (
    AssetPreparationError,
    prepare_translation_assets,
)
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.validation import (
    CanonicalFinalizationError,
    ready_context_asset,
    ready_glossary_asset,
)
from translation_module.cli_io import read_json, write_models_atomic
from translation_module.cli_output import echo_json as _echo
from translation_module.cli_output import fail as _fail

AssetOutputDirectory = Annotated[
    Path,
    typer.Option("--output-dir", help="Prepared asset output directory."),
]


def _relative_locator(path: Path, *, from_directory: Path) -> str:
    return Path(
        os.path.relpath(
            path.resolve(strict=False),
            start=from_directory.resolve(strict=False),
        )
    ).as_posix()


def _prepare_assets_command(
    source_path: Path,
    input_context_path: Path,
    output_dir: AssetOutputDirectory,
) -> None:
    try:
        source = SourceTranscriptV2.model_validate_json(read_json(source_path))
        input_context = ContentContextAsset.model_validate_json(
            read_json(input_context_path)
        )
        bundle = prepare_translation_assets(
            source,
            input_context,
            source_path=_relative_locator(source_path, from_directory=output_dir),
        )
        context_output = output_dir / "context.prepared.json"
        glossary_output = output_dir / "glossary.json"
        write_models_atomic(
            (
                (context_output, bundle.context),
                (glossary_output, bundle.glossary),
            )
        )
    except (AssetPreparationError, OSError, ValidationError) as error:
        _fail("asset_preparation_failed", error)
    _echo(
        source_document_id=source.document_id,
        source_revision_id=source.revision_id,
        context_content_digest=bundle.context.content_digest,
        glossary_content_digest=bundle.glossary.content_digest,
        context_output=str(context_output),
        glossary_output=str(glossary_output),
    )


def _ready_assets_command(
    source_path: Path,
    context_path: Path,
    glossary_path: Path,
    output_dir: AssetOutputDirectory,
) -> None:
    try:
        source = SourceTranscriptV2.model_validate_json(read_json(source_path))
        context = ContentContextAsset.model_validate_json(read_json(context_path))
        glossary = TranslationGlossaryAsset.model_validate_json(
            read_json(glossary_path)
        )
        ready_context = ready_context_asset(context, source=source)
        ready_glossary = ready_glossary_asset(glossary, source=source)
        context_output = output_dir / "context.prepared.json"
        glossary_output = output_dir / "glossary.json"
        write_models_atomic(
            (
                (context_output, ready_context),
                (glossary_output, ready_glossary),
            )
        )
    except (CanonicalFinalizationError, OSError, ValidationError) as error:
        _fail("asset_ready_failed", error)
    _echo(
        source_document_id=source.document_id,
        source_revision_id=source.revision_id,
        context_content_digest=ready_context.content_digest,
        glossary_content_digest=ready_glossary.content_digest,
        context_output=str(context_output),
        glossary_output=str(glossary_output),
    )


def register_asset_commands(app: typer.Typer) -> None:
    """Register canonical context and glossary preparation commands."""
    _ = app.command("prepare-assets")(_prepare_assets_command)
    _ = app.command("ready-assets")(_ready_assets_command)
