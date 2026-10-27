# Copyright 2026 Transcription Module contributors

"""Explicit canonical attempt, preflight, and selection CLI lifecycle."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import TypeAdapter, ValidationError

from translation_module import cli_options
from translation_module.canonical._attempt_models import (
    TranslationAttemptKind,
    TranslationSelectionReceipt,
)
from translation_module.canonical._preflight_models import (
    PreflightCheckerEvidence,
)
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.legacy_authority import (
    AttemptRationaleInput,
    AttemptRecordIdentity,
    LegacyAuthorityError,
    project_legacy_candidate,
    record_failed_attempt,
    record_legacy_success,
)
from translation_module.canonical.review import ReviewProfile, TranslationReviewArtifact
from translation_module.canonical.runtime import (
    CanonicalTranslationWorkflow,
    finalize_selected_translation,
)
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import (
    TranslationDocumentV2,
    TranslationFinalization,
)
from translation_module.canonical.validation import TranslationValidationContext
from translation_module.canonical_attempt_cli import record_canonical_candidate
from translation_module.canonical_preflight_cli import preflight_select_canonical
from translation_module.cli_io import read_json, write_model_atomic
from translation_module.cli_output import echo_json as _echo
from translation_module.cli_output import fail as _fail
from translation_module.delivery_cli import assemble_delivery_command
from translation_module.document import TranslationDocument
from translation_module.source import SourceSnapshot

LegacyPaths = Annotated[tuple[Path, Path], typer.Argument(metavar="SOURCE CANDIDATE")]
CanonicalFinalizePaths = Annotated[
    tuple[Path, Path, Path, Path, Path, Path],
    typer.Argument(metavar="TRANSLATION SOURCE CONTEXT GLOSSARY REVIEW FINALIZATION"),
]
PreflightPaths = Annotated[
    tuple[Path, Path, Path], typer.Argument(metavar="SOURCE CANDIDATE CHECKER")
]
type SourceInput = SourceSnapshot | SourceTranscriptV2
SourceInputAdapter: TypeAdapter[SourceInput] = TypeAdapter(SourceInput)


def _identity(
    value: tuple[str, TranslationAttemptKind, str],
) -> AttemptRecordIdentity:
    attempt_id, kind, parent = value
    return AttemptRecordIdentity(attempt_id, kind, None if parent == "-" else parent)


def record_legacy_candidate(
    source: SourceSnapshot,
    document: TranslationDocument,
    authority_root: Path,
    identity: AttemptRecordIdentity,
    rationale_path: Path,
) -> None:
    """Project and append one successful legacy production candidate."""
    rationale = AttemptRationaleInput.model_validate_json(read_json(rationale_path))
    projected = project_legacy_candidate(source, document, authority_root)
    workflow = CanonicalTranslationWorkflow(authority_root)
    _ = record_legacy_success(
        workflow,
        projected,
        identity,
        rationale,
    )


def _record_candidate_command(
    paths: LegacyPaths,
    authority_root: cli_options.AuthorityRoot,
    attempt: cli_options.AttemptIdentity,
    rationale_path: cli_options.Rationales,
) -> None:
    source_path, document_path = paths
    identity = _identity(attempt)
    try:
        source = SourceInputAdapter.validate_json(read_json(source_path))
        match source:
            case SourceSnapshot():
                document = TranslationDocument.model_validate_json(
                    read_json(document_path)
                )
                record_legacy_candidate(
                    source,
                    document,
                    authority_root,
                    identity,
                    rationale_path,
                )
            case SourceTranscriptV2():
                candidate = TranslationDocumentV2.model_validate_json(
                    read_json(document_path)
                )
                record_canonical_candidate(
                    source,
                    candidate,
                    (authority_root, identity, rationale_path),
                )
    except (LegacyAuthorityError, OSError, ValidationError, ValueError) as error:
        _fail("attempt_recording_failed", error)
    _echo(attempt_id=attempt[0], outcome="succeeded")


def _record_failed_command(
    candidate_path: Path,
    authority_root: cli_options.AuthorityRoot,
    attempt: cli_options.AttemptIdentity,
) -> None:
    try:
        candidate = TranslationDocumentV2.model_validate_json(read_json(candidate_path))
        workflow = CanonicalTranslationWorkflow(authority_root)
        _ = record_failed_attempt(
            workflow,
            candidate,
            _identity(attempt),
        )
    except (OSError, ValidationError, ValueError) as error:
        _fail("attempt_recording_failed", error)
    _echo(attempt_id=attempt[0], outcome="failed")


def _preflight_select_command(
    paths: PreflightPaths,
    authority_root: cli_options.AuthorityRoot,
    attempt_id: Annotated[str, typer.Option("--attempt-id")],
    selection: cli_options.SelectionIdentity,
    dependency_root: cli_options.DependencyRoot = None,
) -> None:
    source_path, document_path, checker_path = paths
    try:
        source = SourceInputAdapter.validate_json(read_json(source_path))
        match source:
            case SourceSnapshot():
                checker = PreflightCheckerEvidence.model_validate_json(
                    read_json(checker_path)
                )
                document = TranslationDocument.model_validate_json(
                    read_json(document_path)
                )
                projected = project_legacy_candidate(source, document, authority_root)
                candidate = projected.candidate
                preflight_context = projected.preflight_context
                selection_id, selected_at, selected_by = selection
                receipt = TranslationSelectionReceipt(
                    selection_id=selection_id,
                    selected_at=selected_at,
                    selected_by=selected_by,
                    translation_document_id=candidate.document_id,
                    translation_revision_id=candidate.revision_id,
                    translation_document_digest=candidate.content_digest,
                    attempt_id=attempt_id,
                    attempt_candidate_digest=candidate.content_digest,
                )
                workflow = CanonicalTranslationWorkflow(authority_root)
                workflow.preflight_and_authorize(
                    preflight_context,
                    checker,
                    receipt,
                )
                selection_path = workflow.attempt_store.selection_path(
                    candidate.document_id,
                    candidate.revision_id,
                )
            case SourceTranscriptV2():
                selection_path = preflight_select_canonical(
                    paths,
                    (authority_root, attempt_id, dependency_root),
                    selection,
                )
    except (LegacyAuthorityError, OSError, ValidationError, ValueError) as error:
        _fail("preflight_selection_failed", error)
    _echo(selection_id=selection[0], output=str(selection_path))


def _finalize_translation_command(
    paths: CanonicalFinalizePaths,
    authority_root: cli_options.AuthorityRoot,
    review_profile: cli_options.ReviewProfileIdentity,
    output: cli_options.Output,
) -> None:
    (
        translation_path,
        source_path,
        context_path,
        glossary_path,
        review_path,
        finalization_path,
    ) = paths
    try:
        translation = TranslationDocumentV2.model_validate_json(
            read_json(translation_path)
        )
        source = SourceTranscriptV2.model_validate_json(read_json(source_path))
        context = ContentContextAsset.model_validate_json(read_json(context_path))
        glossary = TranslationGlossaryAsset.model_validate_json(
            read_json(glossary_path)
        )
        review = TranslationReviewArtifact.model_validate_json(read_json(review_path))
        finalization = TranslationFinalization.model_validate_json(
            read_json(finalization_path)
        )
        profile_id, version = review_profile
        finalized = finalize_selected_translation(
            authority_root,
            translation.document_id,
            translation.revision_id,
            finalization,
            TranslationValidationContext(
                source=source,
                context=context,
                glossary=glossary,
                reviews=(review,),
                review_profile=ReviewProfile(profile_id=profile_id, version=version),
            ),
        )
        write_model_atomic(output, finalized)
    except (OSError, ValidationError, ValueError) as error:
        _fail("translation_finalization_failed", error)
    _echo(document_id=finalized.document_id, output=str(output))


def register_authority_commands(app: typer.Typer) -> None:
    """Register explicit attempt and preflight-selection lifecycle commands."""
    _ = app.command("record-candidate-attempt")(_record_candidate_command)
    _ = app.command("record-failed-attempt")(_record_failed_command)
    _ = app.command("preflight-select")(_preflight_select_command)
    _ = app.command("finalize-translation")(_finalize_translation_command)
    _ = app.command("assemble-delivery")(assemble_delivery_command)


__all__ = ["record_legacy_candidate", "register_authority_commands"]
