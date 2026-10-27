# Copyright 2026 Transcription Module contributors

"""Typed source-edit lifecycle failures shared across run boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import override


class SourceEditFailure(StrEnum):
    """Stable source-edit lifecycle failure reasons."""

    HUMAN_EDIT_CHANGED = "human-edit source changed after review"
    INVALID_CONTEXT = "source input context binding is invalid"
    WRONG_RUN = "source document does not match the run"
    INVALID_ORIGIN = "generated source must be an unfinalized draft"
    WRONG_CHILD_DOCUMENT = "human-edit source document is invalid"
    REUSED_REVISION = "human-edit source must use a distinct revision"
    WRONG_CHILD_LINEAGE = "human-edit source lineage is invalid"
    INVALID_CHILD_LIFECYCLE = "human-edit source must remain a draft"
    TRANSCRIPT_MUTATION = (
        "transcript mutation is unavailable after source editing starts"
    )
    REVALIDATION = "canonical re-validation is unavailable after source editing starts"
    FINALIZATION_CONFLICT = (
        "source.final.json already selects a different human-edit finalization"
    )
    LEGACY_EXPORT = (
        "legacy translation-source export is unavailable after source editing starts"
    )


class SourceEditLifecycleError(Exception):
    """A run-owned source cannot enter the requested edit lifecycle."""

    reason: SourceEditFailure

    def __init__(self, reason: SourceEditFailure) -> None:
        """Create one source-edit lifecycle failure."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason.value


__all__ = ["SourceEditFailure", "SourceEditLifecycleError"]
