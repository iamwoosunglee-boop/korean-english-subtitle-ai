# Copyright 2026 Transcription Module contributors

"""Canonical transcript speaker-notation tests."""

from __future__ import annotations

import pytest

from transcription_module.speaker_notation import (
    SpeakerNotationError,
    parse_speaker_labels,
    validate_speaker_notation,
)


@pytest.mark.parametrize(
    ("speaker", "speech", "labels"),
    [
        (None, "미확정 발화", ()),
        ("루미", "좋습니다.", ("루미",)),
        ("루미|한지아", "같이 외칩니다!", ("루미", "한지아")),
        (
            "루미|한지아",
            "좋습니다.\n너무 좋아, 너무 좋아.",
            ("루미", "한지아"),
        ),
        (
            "오태우|오태우",
            "없어.\n없어.",
            ("오태우", "오태우"),
        ),
    ],
)
def test_canonical_speaker_notation(
    speaker: str | None,
    speech: str,
    labels: tuple[str, ...],
) -> None:
    """Pipe order maps to speech-line order when turns are sequential."""
    assert parse_speaker_labels(speaker) == labels
    validate_speaker_notation(speaker, speech)


@pytest.mark.parametrize(
    ("speaker", "speech"),
    [
        ("루미 → 한지아", "좋습니다. 너무 좋아요."),
        ("루미·한지아", "같이 외칩니다!"),
        ("루미\uff5c한지아", "같이 외칩니다!"),
        ("루미||한지아", "같이 외칩니다!"),
        ("루미| 한지아", "같이 외칩니다!"),
        ("루미|루미", "같이 외칩니다!"),
        ("루미|한지아", "첫째 줄\n둘째 줄\n셋째 줄"),
        ("루미|오태우|박도현", "선창\n후창"),
        ("루미", "첫째 줄\n둘째 줄"),
        ("진행자들(중첩)", "같이 외칩니다!"),
    ],
)
def test_noncanonical_speaker_notation_is_rejected(
    speaker: str,
    speech: str,
) -> None:
    """Legacy separators, commentary, and turn mismatches fail closed."""
    with pytest.raises(SpeakerNotationError):
        validate_speaker_notation(speaker, speech)


def test_final_speaker_notation_rejects_missing_identity() -> None:
    """A certain final cue cannot keep an unresolved speaker."""
    with pytest.raises(SpeakerNotationError):
        validate_speaker_notation(None, "확정 발화", require_speaker=True)
