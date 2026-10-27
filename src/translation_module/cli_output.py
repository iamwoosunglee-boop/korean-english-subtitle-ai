# Copyright 2026 Transcription Module contributors

"""Machine-readable translation CLI output."""

import json
from typing import NoReturn

import typer


def echo_json(**payload: object) -> None:
    """Write deterministic compact JSON to stdout."""
    typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def fail(code: str, error: Exception) -> NoReturn:
    """Write one typed JSON error and exit nonzero."""
    typer.echo(
        json.dumps(
            {"error": {"code": code, "message": str(error)}},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        err=True,
    )
    raise typer.Exit(code=2)
