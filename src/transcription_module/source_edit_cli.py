# Copyright 2026 Transcription Module contributors

"""CLI boundary for run-owned canonical source editing and translation admission."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from transcription_module.boundaries import parse_run_id
from transcription_module.cli_errors import render_error
from transcription_module.errors import CliBoundaryError, ErrorCode
from transcription_module.run_translation_admission import (
    prepare_run_translation_assets,
)
from transcription_module.source_edit_errors import SourceEditLifecycleError
from transcription_module.source_edit_lifecycle import (
    finalize_source_edit,
    start_source_edit,
)
from transcription_module.storage import RunStore
from translation_module.canonical.asset_preparation import AssetPreparationError
from translation_module.canonical.source import SourceFinalization

RunsDirOption = Annotated[
    Path,
    typer.Option(
        "--runs-dir",
        file_okay=False,
        help="Directory that owns isolated run artifacts.",
    ),
]
DEFAULT_RUNS_DIR = Path("runs")


def start_source_edit_command(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Create or reopen the one run-owned canonical human-edit draft."""
    try:
        parsed_run_id = parse_run_id(run_id)
        result = start_source_edit(RunStore(runs_dir), parsed_run_id)
    except CliBoundaryError as error:
        render_error(error)
    except (OSError, SourceEditLifecycleError, ValidationError, ValueError) as error:
        render_error(CliBoundaryError(ErrorCode.SOURCE_EDIT_FAILED, str(error)))
    typer.echo(result.model_dump_json())


def finalize_source_edit_command(
    run_id: str,
    finalized_at: Annotated[str, typer.Option("--finalized-at")],
    finalized_by: Annotated[str, typer.Option("--finalized-by")],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Finalize the exact run-owned canonical human-edit draft."""
    try:
        parsed_run_id = parse_run_id(run_id)
        finalization = SourceFinalization(
            finalized_at=finalized_at,
            finalized_by=finalized_by,
        )
        result = finalize_source_edit(
            RunStore(runs_dir),
            parsed_run_id,
            finalization,
        )
    except CliBoundaryError as error:
        render_error(error)
    except (OSError, SourceEditLifecycleError, ValidationError, ValueError) as error:
        render_error(CliBoundaryError(ErrorCode.SOURCE_EDIT_FAILED, str(error)))
    typer.echo(result.model_dump_json())


def prepare_run_translation_assets_command(
    run_id: str,
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Prepare translation assets from one run-owned finalized source."""
    try:
        parsed_run_id = parse_run_id(run_id)
        result = prepare_run_translation_assets(
            RunStore(runs_dir),
            parsed_run_id,
            output_dir,
        )
    except CliBoundaryError as error:
        render_error(error)
    except (AssetPreparationError, OSError, ValidationError) as error:
        render_error(
            CliBoundaryError(
                ErrorCode.TRANSLATION_ADMISSION_FAILED,
                str(error),
            )
        )
    typer.echo(result.model_dump_json())


__all__ = [
    "finalize_source_edit_command",
    "prepare_run_translation_assets_command",
    "start_source_edit_command",
]
