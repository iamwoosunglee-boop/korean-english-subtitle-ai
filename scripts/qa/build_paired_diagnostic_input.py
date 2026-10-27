# Copyright 2026 Transcription Module contributors

"""Build and exclusively publish authoritative paired-diagnostic v2 input."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Never

import typer
from pydantic import ValidationError

from scripts.qa.real_diagnostic_fixture_builder import (
    DiagnosticBuildError,
    build_fixture_input,
    canonical_model_bytes,
    diagnostic_error,
    refuse,
)
from transcription_module.boundaries import parse_run_id
from transcription_module.errors import CliBoundaryError

if TYPE_CHECKING:
    from scripts.qa.real_diagnostic_fixture_models import (
        EvidencePairedDiagnosticInput,
    )

app = typer.Typer(add_completion=False)
_REAL_GATE_MESSAGE = (
    "reviewed speaker authority v2, derived turns v3, and downstream "
    "bindings are required"
)


def _fail(error: DiagnosticBuildError) -> Never:
    payload = {"error": {"code": error.code, "message": error.message}}
    typer.echo(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        err=True,
    )
    raise typer.Exit(code=2)


def _confined_run_dir(runs_dir: Path, run_id: str) -> Path:
    try:
        parsed = parse_run_id(run_id)
    except CliBoundaryError as error:
        refuse("invalid_run_id", str(error))
    try:
        root = runs_dir.resolve(strict=True)
        root_metadata = root.lstat()
        selected = root / str(parsed)
        selected_metadata = selected.lstat()
        resolved = selected.resolve(strict=True)
    except OSError:
        refuse(
            "invalid_run_id",
            "selected run directory does not exist",
        )
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(selected_metadata.st_mode)
        or not stat.S_ISDIR(selected_metadata.st_mode)
        or resolved.parent != root
    ):
        refuse(
            "invalid_run_id",
            "selected run directory is not safely confined",
        )
    return resolved


def build_real_run_input(
    fixture_path: Path,
    run_dir: Path,
) -> EvidencePairedDiagnosticInput:
    """Remain closed until reviewed real-run speaker authority is available."""
    del fixture_path, run_dir
    refuse(
        "required_speaker_authority",
        _REAL_GATE_MESSAGE,
    )


def _validated_output_parent(output: Path) -> Path:
    parent = output.parent
    try:
        metadata = parent.lstat()
    except OSError:
        refuse(
            "invalid_output",
            "output directory does not exist",
        )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        refuse(
            "invalid_output",
            "output directory is not a regular directory",
        )
    return parent


def _require_output_absent(output: Path) -> None:
    if os.path.lexists(output):
        refuse("output_exists", "output already exists")


def _publish_exclusive(output: Path, payload: bytes) -> None:
    parent = _validated_output_parent(output)
    _require_output_absent(output)
    temporary = parent / f".{output.name}.{os.getpid()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, output, follow_symlinks=False)
        except OSError as error:
            if error.errno == errno.EEXIST:
                refuse(
                    "output_exists",
                    "output already exists",
                )
            raise
        temporary.unlink()
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except DiagnosticBuildError:
        raise
    except OSError:
        refuse(
            "output_publication_failed",
            "could not publish diagnostic input",
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _select_input(
    fixture_path: Path,
    run_fixture: str | None,
    run_id: str | None,
    runs_dir: str,
) -> EvidencePairedDiagnosticInput:
    if (run_fixture is None) == (run_id is None):
        refuse(
            "invalid_builder_mode",
            "exactly one of --run-fixture or --run-id is required",
        )
    if run_fixture is not None:
        return build_fixture_input(fixture_path, Path(run_fixture))
    selected = _confined_run_dir(Path(runs_dir), run_id or "")
    return build_real_run_input(fixture_path, selected)


@app.command()
def main(
    fixture: Annotated[str, typer.Option("--fixture")],
    output: Annotated[str, typer.Option("--output")],
    run_fixture: Annotated[
        str | None,
        typer.Option("--run-fixture"),
    ] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    runs_dir: Annotated[str, typer.Option("--runs-dir")] = "runs",
) -> None:
    """Build fixture v2 input or enforce the closed real-run authority gate."""
    try:
        output_path = Path(output)
        _require_output_absent(output_path)
        fixture_path = Path(fixture)
        model = _select_input(fixture_path, run_fixture, run_id, runs_dir)
        payload = canonical_model_bytes(model)
        _publish_exclusive(output_path, payload)
        typer.echo(
            json.dumps(
                {
                    "input_mode": model.input_mode.value,
                    "output": str(output_path),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except DiagnosticBuildError as error:
        _fail(error)
    except (OSError, ValidationError, KeyError) as error:
        _fail(
            diagnostic_error(
                "invalid_fixture",
                f"fixture replay could not be validated: {error}",
            )
        )


if __name__ == "__main__":
    app()


__all__ = ["app", "build_real_run_input", "main"]
