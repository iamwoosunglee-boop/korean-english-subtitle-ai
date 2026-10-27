# Copyright 2026 Transcription Module contributors

"""Non-mutating source-finalization assertion boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from transcription_module.validation import validation_findings

if TYPE_CHECKING:
    from transcription_module.models import Transcript, ValidationFinding


class SourceFinalizationBlockedError(Exception):
    """Typed source blockers found before lifecycle publication."""

    findings: tuple[ValidationFinding, ...]

    def __init__(self, findings: tuple[ValidationFinding, ...]) -> None:
        """Retain exact machine findings for the caller."""
        super().__init__()
        self.findings = findings

    @override
    def __str__(self) -> str:
        return "; ".join(finding.issue for finding in self.findings)


def assert_source_finalizable(transcript: Transcript) -> None:
    """Raise typed blockers without mutating the supplied source snapshot."""
    blockers = tuple(
        finding
        for finding in validation_findings(transcript)
        if finding.code.blocks_source_finalization
    )
    if blockers:
        raise SourceFinalizationBlockedError(blockers)
