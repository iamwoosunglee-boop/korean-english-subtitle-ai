# Copyright 2026 Transcription Module contributors

"""Command-line boundaries for agent-driven subtitle translation."""

import json
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import ValidationError

from translation_module import cli_options
from translation_module.asset_cli import register_asset_commands
from translation_module.authority_cli import register_authority_commands
from translation_module.bridge_cli import register_bridge_commands
from translation_module.canonical.attempts import (
    CorruptAttemptLedgerError,
    SelectionBindingError,
)
from translation_module.canonical.legacy_authority import (
    LegacyAuthorityError,
)
from translation_module.canonical.migration import (
    LegacyMigrationError,
    migrate_v1_project,
    write_migration_project,
)
from translation_module.canonical.preflight import PreflightGateError
from translation_module.canonical_validation_cli import (
    validate_canonical_document,
)
from translation_module.cli_io import (
    MAX_JSON_BYTES as IO_MAX_JSON_BYTES,
)
from translation_module.cli_io import (
    read_json as _read_json,
)
from translation_module.cli_io import write_model_atomic
from translation_module.cli_output import echo_json as _echo
from translation_module.cli_output import fail as _fail
from translation_module.document import TranslationDocument
from translation_module.preparation import bind_source_digest
from translation_module.publication import (
    PublicationError,
    PublicationReviewInputs,
    publish_translation,
)
from translation_module.review_cli import register_review_commands
from translation_module.review_state import ReviewAttempts
from translation_module.source import SourceSnapshot
from translation_module.validation import (
    TranslationValidationError,
    validate_translation,
)
from translation_module.workflow import ReviewAssignment, ReviewResult

MAX_JSON_BYTES = IO_MAX_JSON_BYTES

app = typer.Typer(no_args_is_help=True)
OutputOption = Annotated[Path, typer.Option("--output", help="Destination JSON path.")]
DocumentReviewPaths = Annotated[
    tuple[Path, Path], typer.Argument(metavar="CANDIDATE REVIEW")
]
AttemptsOption = Annotated[
    Path,
    typer.Option(
        "--attempts",
        help="Candidate-bound revision-attempt and focused-domain state JSON.",
    ),
]
AssignmentOption = Annotated[
    Path, typer.Option("--assignment", help="Parent-issued review assignment JSON.")
]
MigrationTranslationOption = Annotated[
    Path | None,
    typer.Option("--translation", help="Optional legacy translation-document.v1 JSON."),
]
MigrationOutputDirectory = Annotated[
    Path,
    typer.Option("--output-dir", help="New canonical project directory."),
]
MigrationEvidenceRoot = Annotated[
    Path | None,
    typer.Option(
        "--evidence-root",
        help="Optional local evidence root to verify before portable rebasing.",
    ),
]


def _load_json_object(path: Path) -> dict[str, object]:
    decoded = cast(
        "object",
        json.loads(_read_json(path)),
    )
    if not isinstance(decoded, dict):
        message = "source JSON must be an object"
        raise TypeError(message)
    return cast("dict[str, object]", decoded)


@app.command("bind-source")
def bind_source_command(source_path: Path, output: OutputOption) -> None:
    """Bind a prepared source snapshot to its canonical content digest."""
    try:
        source = SourceSnapshot.model_validate_json(_read_json(source_path))
        published = bind_source_digest(source)
        write_model_atomic(output, published)
    except (OSError, ValidationError) as error:
        _fail("invalid_source", error)
    _echo(
        document_id=published.document_id,
        content_digest=published.content_digest,
        output=str(output),
    )


register_bridge_commands(app)
register_asset_commands(app)
register_review_commands(app)
register_authority_commands(app)


@app.command("migrate-project")
def migrate_project_command(
    source_path: Path,
    output_dir: MigrationOutputDirectory,
    translation_path: MigrationTranslationOption = None,
    evidence_root: MigrationEvidenceRoot = None,
) -> None:
    """Migrate legacy v1 JSON into a canonical v2 project."""
    try:
        source = SourceSnapshot.model_validate_json(_read_json(source_path))
        translation = (
            None
            if translation_path is None
            else TranslationDocument.model_validate_json(_read_json(translation_path))
        )
        bundle = migrate_v1_project(
            source,
            translation,
            output_dir=output_dir,
            evidence_root=evidence_root,
        )
        write_migration_project(bundle, output_dir)
    except (LegacyMigrationError, OSError, ValidationError) as error:
        _fail("migration_failed", error)
    typer.echo(bundle.receipt.model_dump_json(exclude_none=True))


@app.command("validate")
def validate_command(source_path: Path, document_path: Path) -> None:
    """Validate exact translation JSON against its source snapshot."""
    try:
        source_payload = _load_json_object(source_path)
    except (
        json.JSONDecodeError,
        OSError,
        TypeError,
        UnicodeDecodeError,
    ) as error:
        _fail("invalid_document", error)
    if source_payload.get("schema_version") == "source-transcript.v2":
        try:
            payload = validate_canonical_document(
                source_path,
                document_path,
            )
        except (OSError, ValidationError, ValueError) as error:
            _fail("validation_failed", error)
        _echo(**payload)
        return
    try:
        source = SourceSnapshot.model_validate_json(_read_json(source_path))
        document = TranslationDocument.model_validate_json(_read_json(document_path))
        validate_translation(source, document)
    except TranslationValidationError as error:
        _fail("validation_failed", error)
    except (OSError, ValidationError) as error:
        _fail("invalid_document", error)
    _echo(valid=True, document_id=document.document_id, status=document.status.value)


@app.command("publish")
def publish_command(
    source_path: Path,
    document_review_paths: DocumentReviewPaths,
    output: OutputOption,
    publication_authority: cli_options.PublicationAuthority,
) -> None:
    """Publish only a validated candidate with current canonical authority."""
    document_path, review_path = document_review_paths
    attempts_path, assignment_path, authority_root = publication_authority
    try:
        source = SourceSnapshot.model_validate_json(_read_json(source_path))
        document = TranslationDocument.model_validate_json(_read_json(document_path))
        review = ReviewResult.model_validate_json(_read_json(review_path))
        attempts = ReviewAttempts.model_validate_json(_read_json(attempts_path))
        assignment = ReviewAssignment.model_validate_json(_read_json(assignment_path))
        published = publish_translation(
            authority_root,
            source,
            document,
            review,
            PublicationReviewInputs(assignment, attempts),
        )
        write_model_atomic(output, published)
    except (
        CorruptAttemptLedgerError,
        LegacyAuthorityError,
        PreflightGateError,
        PublicationError,
        SelectionBindingError,
        TranslationValidationError,
    ) as error:
        _fail("publication_failed", error)
    except (OSError, ValidationError) as error:
        _fail("invalid_publication_input", error)
    _echo(
        document_id=published.document_id,
        status=published.status.value,
        output=str(output),
    )


if __name__ == "__main__":
    app()
