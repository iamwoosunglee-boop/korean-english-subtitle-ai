# Copyright 2026 Transcription Module contributors

"""Run-owned admission into canonical translation asset preparation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from transcription_module.artifact_io import read_confined_bytes
from transcription_module.models import FrozenModel, RunId
from translation_module.canonical.asset_preparation import (
    prepare_translation_assets,
)
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.cli_io import write_models_atomic

if TYPE_CHECKING:
    from transcription_module.storage import RunStore


class RunTranslationAssetsResult(FrozenModel):
    """Machine-consumed outputs from one run-owned translation admission."""

    run_id: RunId
    source_document_id: str
    source_revision_id: str
    source_content_digest: str
    context_output: str
    glossary_output: str


def _relative_locator(path: Path, *, from_directory: Path) -> str:
    return Path(
        os.path.relpath(
            path.resolve(strict=False),
            start=from_directory.resolve(strict=False),
        )
    ).as_posix()


def prepare_run_translation_assets(
    store: RunStore,
    run_id: RunId,
    output_dir: Path,
) -> RunTranslationAssetsResult:
    """Prepare translation assets from the run's exact finalized source."""
    resolved_output_dir = output_dir.resolve(strict=False)
    with store.lock_run(run_id):
        paths = store.paths(run_id)
        source_payload = read_confined_bytes(paths.source_final)
        context_payload = read_confined_bytes(paths.input_context)
        if source_payload is None or context_payload is None:
            raise FileNotFoundError(paths.source_final)
        source = SourceTranscriptV2.model_validate_json(source_payload)
        input_context = ContentContextAsset.model_validate_json(context_payload)
        bundle = prepare_translation_assets(
            source,
            input_context,
            source_path=_relative_locator(
                paths.source_final,
                from_directory=resolved_output_dir,
            ),
        )
        context_output = resolved_output_dir / "context.prepared.json"
        glossary_output = resolved_output_dir / "glossary.json"
        write_models_atomic(
            (
                (context_output, bundle.context),
                (glossary_output, bundle.glossary),
            )
        )
        return RunTranslationAssetsResult(
            run_id=run_id,
            source_document_id=source.document_id,
            source_revision_id=source.revision_id,
            source_content_digest=source.content_digest,
            context_output=str(context_output),
            glossary_output=str(glossary_output),
        )


__all__ = ["RunTranslationAssetsResult", "prepare_run_translation_assets"]
