# Copyright 2026 Transcription Module contributors

"""Canonical speaker metadata shared by review and export boundaries."""

from __future__ import annotations


class SpeakerNotationError(ValueError):
    """Raised when speaker metadata cannot map to transcript lines."""


def parse_speaker_labels(speaker: str | None) -> tuple[str, ...]:
    """Parse canonical ordered labels separated only by ASCII pipes."""
    if speaker is None:
        return ()
    legacy_marks = ("→", "·", "\uff5c", "(", ")")
    if speaker != speaker.strip() or any(mark in speaker for mark in legacy_marks):
        message = "speaker labels must use canonical ASCII pipe separators"
        raise SpeakerNotationError(message)
    labels = tuple(speaker.split("|"))
    if any(not label or label != label.strip() for label in labels):
        message = "speaker labels must be non-empty and unpadded"
        raise SpeakerNotationError(message)
    return labels


def validate_speaker_notation(
    speaker: str | None,
    speech: str,
    *,
    require_speaker: bool = False,
) -> None:
    """Require sequential speech lines to follow ordered speaker labels."""
    labels = parse_speaker_labels(speaker)
    if require_speaker and not labels:
        message = "a final cue must identify at least one speaker"
        raise SpeakerNotationError(message)
    speech_lines = speech.split("\n")
    if any(not line for line in speech_lines):
        message = "speech lines must be non-empty"
        raise SpeakerNotationError(message)
    if len(speech_lines) == 1 and len(labels) != len(set(labels)):
        message = "simultaneous speaker labels must not repeat"
        raise SpeakerNotationError(message)
    if len(speech_lines) > 1 and len(labels) != len(speech_lines):
        message = "sequential speech lines must match speaker label order"
        raise SpeakerNotationError(message)


__all__ = [
    "SpeakerNotationError",
    "parse_speaker_labels",
    "validate_speaker_notation",
]
