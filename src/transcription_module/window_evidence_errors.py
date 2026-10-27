# Copyright 2026 Transcription Module contributors

"""Stable failure type for policy-bound window evidence validation."""

from __future__ import annotations

from typing import Never


class WindowEvidenceError(ValueError):
    """A policy-bound window result is incomplete, stale, or unsupported."""


def fail_window_evidence(message: str) -> Never:
    """Raise the stable window-evidence failure at one lint-safe seam."""
    raise WindowEvidenceError(message)


__all__ = ["WindowEvidenceError", "fail_window_evidence"]
