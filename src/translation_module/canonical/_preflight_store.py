# Copyright 2026 Transcription Module contributors

"""Immutable descriptor-relative persistence for candidate preflight reports."""

from pathlib import Path

from translation_module.canonical._authority_namespace import open_authority_namespace
from translation_module.canonical._preflight_models import (
    PreflightGateCode,
    PreflightGateError,
    PreflightReport,
)


def write_preflight_report(path: Path, report: PreflightReport) -> Path:
    """Write exact report bytes through a no-follow parent namespace."""
    payload = f"{report.model_dump_json(indent=2)}\n".encode()
    with open_authority_namespace(path.parent, create=True) as namespace:
        if namespace.exists_regular(path.name):
            if namespace.read_regular(path.name) == payload:
                return path
            raise PreflightGateError(
                PreflightGateCode.REPORT_CONFLICT,
                "existing preflight report bytes differ",
            )
        namespace.write_atomic(path.name, payload)
    return path
