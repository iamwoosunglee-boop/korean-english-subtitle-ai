# Copyright 2026 Transcription Module contributors

"""Canonical v2 preflight-selection boundary."""

from contextlib import ExitStack
from pathlib import Path
from typing import Literal

from translation_module.canonical._authority_namespace import (
    AuthorityNamespace,
    open_authority_namespace,
)
from translation_module.canonical.attempts import TranslationSelectionReceipt
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.preflight import (
    CandidatePreflightContext,
    PreflightCheckerEvidence,
)
from translation_module.canonical.runtime import CanonicalTranslationWorkflow
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2
from translation_module.cli_io import MAX_JSON_BYTES, read_json
from translation_module.common import FrozenModel, Sha256Digest

type CanonicalSelectionPaths = tuple[Path, Path, Path]
type CanonicalSelectionAuthority = tuple[Path, str, Path | None]
type CanonicalSelectionIdentity = tuple[str, str, str]


class BoundPreflightCheckerEvidence(FrozenModel):
    """Checker output bound to one exact canonical dependency graph."""

    schema_version: Literal["translation-preflight-checker.v1"]
    translation_document_id: str
    translation_revision_id: str
    candidate_content_digest: Sha256Digest
    source_document_id: str
    source_revision_id: str
    source_content_digest: Sha256Digest
    glossary_asset_id: str
    glossary_revision_id: str
    glossary_content_digest: Sha256Digest
    evidence: PreflightCheckerEvidence


def _read_dependency(
    scope: tuple[Path, Path],
    locator: str | None,
    label: str,
) -> bytes:
    dependency_root, document_path = scope
    if locator is None:
        message = f"canonical {label} reference has no path"
        raise ValueError(message)
    relative = Path(locator)
    if relative.is_absolute():
        message = f"canonical {label} reference path must be relative"
        raise ValueError(message)
    try:
        document_relative = document_path.relative_to(dependency_root)
    except ValueError as error:
        message = "canonical document is outside dependency root"
        raise ValueError(message) from error
    parts: list[str] = []
    for part in (document_relative.parent / relative).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                message = f"canonical {label} reference escapes dependency root"
                raise ValueError(message)
            _ = parts.pop()
            continue
        parts.append(part)
    if not parts:
        message = f"canonical {label} dependency path is empty"
        raise ValueError(message)
    with ExitStack() as stack:
        namespace: AuthorityNamespace = stack.enter_context(
            open_authority_namespace(dependency_root)
        )
        for directory in parts[:-1]:
            namespace = stack.enter_context(namespace.child(directory))
        return namespace.read_regular_bounded(parts[-1], MAX_JSON_BYTES)


def preflight_select_canonical(
    paths: CanonicalSelectionPaths,
    authority: CanonicalSelectionAuthority,
    selection: CanonicalSelectionIdentity,
) -> Path:
    """Validate and select one persisted canonical v2 candidate."""
    source_path, document_path, checker_path = paths
    authority_root, attempt_id, dependency_root = authority
    if dependency_root is None:
        message = "canonical v2 preflight requires --dependency-root"
        raise ValueError(message)
    source = SourceTranscriptV2.model_validate_json(read_json(source_path))
    candidate = TranslationDocumentV2.model_validate_json(read_json(document_path))
    bound_checker = BoundPreflightCheckerEvidence.model_validate_json(
        read_json(checker_path)
    )
    glossary = TranslationGlossaryAsset.model_validate_json(
        _read_dependency(
            (dependency_root, document_path),
            candidate.glossary_ref.path,
            "glossary",
        )
    )
    context = CandidatePreflightContext(
        candidate=candidate,
        source=source,
        glossary=glossary,
    )
    actual_binding = (
        bound_checker.translation_document_id,
        bound_checker.translation_revision_id,
        bound_checker.candidate_content_digest,
        bound_checker.source_document_id,
        bound_checker.source_revision_id,
        bound_checker.source_content_digest,
        bound_checker.glossary_asset_id,
        bound_checker.glossary_revision_id,
        bound_checker.glossary_content_digest,
    )
    expected_binding = (
        candidate.document_id,
        candidate.revision_id,
        candidate.content_digest,
        source.document_id,
        source.revision_id,
        source.content_digest,
        glossary.asset_id,
        glossary.revision_id,
        glossary.content_digest,
    )
    if actual_binding != expected_binding:
        message = "preflight checker binding is stale"
        raise ValueError(message)
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
    workflow.preflight_and_authorize(context, bound_checker.evidence, receipt)
    return workflow.attempt_store.selection_path(
        candidate.document_id,
        candidate.revision_id,
    )


__all__ = ["BoundPreflightCheckerEvidence", "preflight_select_canonical"]
