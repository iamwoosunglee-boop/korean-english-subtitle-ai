# Copyright 2026 Transcription Module contributors

"""Typer command for canonical transcript validation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import ValidationError

from transcription_module.boundaries import parse_run_id
from transcription_module.cli_errors import render_error
from transcription_module.errors import CliBoundaryError, ErrorCode
from transcription_module.source_edit_errors import SourceEditLifecycleError
from transcription_module.storage import RunStore
from transcription_module.validation import (
    ValidationContractError,
    validate_run,
)

RunsDirOption = Annotated[
    Path,
    typer.Option(
        "--runs-dir",
        file_okay=False,
        help="Directory that owns isolated run artifacts.",
    ),
]
DEFAULT_RUNS_DIR: Final = Path("runs")


def validate_command(
    run_id: str,
    runs_dir: RunsDirOption = DEFAULT_RUNS_DIR,
) -> None:
    """Validate canonical JSON, derived CSV, fields, order, and timecodes."""
    try:
        parsed_run_id = parse_run_id(run_id)
        artifact = validate_run(RunStore(runs_dir), parsed_run_id)
    except CliBoundaryError as error:
        render_error(error)
    except SourceEditLifecycleError as error:
        render_error(CliBoundaryError(ErrorCode.SOURCE_EDIT_FAILED, str(error)))
    except (OSError, ValidationError, ValidationContractError) as error:
        render_error(
            CliBoundaryError(
                ErrorCode.VALIDATION_FAILED,
                str(error),
            )
        )
    typer.echo(artifact.model_dump_json())
