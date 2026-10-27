# Copyright 2026 Transcription Module contributors

"""Shared typed failures for correction and cue provenance authority."""


class CueProvenanceError(ValueError):
    """Correction or cue provenance is stale, incomplete, or ambiguous."""


__all__ = ["CueProvenanceError"]
