# Copyright 2026 Transcription Module contributors

"""Typed constructors for constrained translation test values."""

from typing import cast

from translation_module.common import ContextId, EvidenceId, Milliseconds


def context_id(value: str) -> ContextId:
    """Build a typed context ID for a valid test fixture."""
    return cast("ContextId", cast("object", value))


def evidence_id(value: str) -> EvidenceId:
    """Build a typed evidence ID for a valid test fixture."""
    return cast("EvidenceId", value)


def milliseconds(value: int) -> Milliseconds:
    """Build typed milliseconds for a valid test fixture."""
    return cast("Milliseconds", value)
