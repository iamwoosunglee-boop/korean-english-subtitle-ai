# Copyright 2026 Transcription Module contributors

"""Canonical v2 translation validation for the public CLI."""

from __future__ import annotations

from pathlib import Path

from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.review import ReviewProfile
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.canonical.validation import (
    TranslationValidationContext,
    draft_translation_diagnostics,
    validate_canonical_save,
)
from translation_module.cli_io import read_json


def canonical_dependency_path(
    document_path: Path,
    locator: str | None,
    *,
    label: str,
) -> Path:
    """Resolve one required relative dependency beside a canonical document."""
    if locator is None:
        message = f"canonical {label} reference has no path"
        raise ValueError(message)
    relative = Path(locator)
    if relative.is_absolute():
        message = f"canonical {label} reference path must be relative"
        raise ValueError(message)
    path = document_path.parent / relative
    if not path.exists():
        message = f"canonical {label} dependency is missing: {path}"
        raise ValueError(message)
    return path


def _assert_source_binding(
    source: SourceTranscriptV2,
    document: TranslationDocumentV2,
) -> None:
    reference = document.source_ref
    if (
        reference.document_id != source.document_id
        or reference.revision_id != source.revision_id
        or reference.content_digest != source.content_digest
    ):
        message = "canonical source reference does not match supplied source"
        raise ValueError(message)


def _assert_asset_binding(
    asset: ContentContextAsset | TranslationGlossaryAsset,
    *,
    asset_id: str,
    revision_id: str,
    content_digest: str,
    label: str,
) -> None:
    if (
        asset.asset_id != asset_id
        or asset.revision_id != revision_id
        or asset.content_digest != content_digest
    ):
        message = f"canonical {label} reference does not match dependency"
        raise ValueError(message)


def validate_canonical_document(
    source_path: Path,
    document_path: Path,
) -> dict[str, object]:
    """Reload the exact canonical dependency graph and return diagnostics."""
    source = SourceTranscriptV2.model_validate_json(read_json(source_path))
    document = TranslationDocumentV2.model_validate_json(read_json(document_path))
    _assert_source_binding(source, document)
    context = ContentContextAsset.model_validate_json(
        read_json(
            canonical_dependency_path(
                document_path,
                document.prepared_context_ref.path,
                label="context",
            )
        )
    )
    glossary = TranslationGlossaryAsset.model_validate_json(
        read_json(
            canonical_dependency_path(
                document_path,
                document.glossary_ref.path,
                label="glossary",
            )
        )
    )
    validate_canonical_save(source)
    validate_canonical_save(context)
    validate_canonical_save(glossary)
    _assert_asset_binding(
        context,
        asset_id=document.prepared_context_ref.asset_id,
        revision_id=document.prepared_context_ref.revision_id,
        content_digest=document.prepared_context_ref.content_digest,
        label="context",
    )
    _assert_asset_binding(
        glossary,
        asset_id=document.glossary_ref.asset_id,
        revision_id=document.glossary_ref.revision_id,
        content_digest=document.glossary_ref.content_digest,
        label="glossary",
    )
    validate_canonical_save(document)
    report = draft_translation_diagnostics(
        document,
        TranslationValidationContext(
            source=source,
            context=context,
            glossary=glossary,
            reviews=(),
            review_profile=ReviewProfile(
                profile_id="cli-canonical-draft-validation",
                version="1",
            ),
        ),
    )
    if report.has_blocking:
        message = "; ".join(
            diagnostic.message
            for diagnostic in report.diagnostics
            if diagnostic.severity.value == "blocking"
        )
        raise ValueError(message or "canonical validation failed")
    return {
        "schema_version": document.schema_version,
        "valid": True,
        "blocking": False,
        "source_content_digest": source.content_digest,
        "translation_content_digest": document.content_digest,
        "diagnostics": [
            {
                "code": diagnostic.code.value,
                "severity": diagnostic.severity.value,
                "message": diagnostic.message,
                "source_cue_ids": list(diagnostic.source_cue_ids),
                "target_cue_ids": list(diagnostic.target_cue_ids),
            }
            for diagnostic in report.diagnostics
        ],
    }


__all__ = ["canonical_dependency_path", "validate_canonical_document"]
