# Copyright 2026 Transcription Module contributors

"""Shared typed-error rendering for CLI command modules."""

from __future__ import annotations

from typing import Never

import typer

from transcription_module.errors import (
    CliBoundaryError,
    ErrorDetail,
    ErrorEnvelope,
)


def render_error(error: CliBoundaryError) -> Never:
    """Write one typed error and terminate the current command."""
    payload = ErrorEnvelope(
        error=ErrorDetail(code=error.code, message=error.message)
    )
    typer.echo(payload.model_dump_json(), err=True)
    raise typer.Exit(code=2)
