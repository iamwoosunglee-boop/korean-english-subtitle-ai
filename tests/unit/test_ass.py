# Copyright 2026 Transcription Module contributors

"""Strict ASS translation-source parser tests."""

from __future__ import annotations

import pytest

from transcription_module.ass import parse_ass_dialogues

EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)
EXPECTED_DIALOGUE_COUNT = 2
EXPECTED_START_MS = 3_723_450
EXPECTED_END_MS = 3_724_560
SECOND_EVENT_INDEX = 2


def _ass(*events: str, event_format: str = EVENT_FORMAT) -> bytes:
    return (
        "[Script Info]\nTitle: fixture\n\n[Events]\n"
        f"{event_format}\n"
        f"{'\n'.join(events)}\n"
    ).encode()


def test_parse_ass_dialogues_preserves_literal_fields_and_integer_time() -> None:
    """The ten-field seam keeps cue text/context exact and uses centiseconds."""
    text = r"첫 줄\N둘째 줄, 쉼표"
    first_dialogue = (
        f"Dialogue: 0,1:02:03.45,1:02:04.56,Default,한지아,0,0,0,웃음 ,{text}"
    )
    payload = _ass(
        first_dialogue,
        "Dialogue: 0,0:00:00.01,0:00:00.02,Default,,0,0,0,,짧게",
    )

    dialogues = parse_ass_dialogues(payload)

    assert len(dialogues) == EXPECTED_DIALOGUE_COUNT
    assert dialogues[0].event_index == 1
    assert dialogues[0].start_ms == EXPECTED_START_MS
    assert dialogues[0].end_ms == EXPECTED_END_MS
    assert dialogues[0].style == "Default"
    assert dialogues[0].name == "한지아"
    assert dialogues[0].effect == "웃음 "
    assert dialogues[0].text == r"첫 줄\N둘째 줄, 쉼표"
    assert dialogues[1].event_index == SECOND_EVENT_INDEX
    assert dialogues[1].name is None
    assert dialogues[1].effect is None


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"[Script Info]\nTitle: missing events\n",
        _ass(
            "Dialogue: 0,0:00:00.01,0:00:00.02,Default,A,0,0,0,,text",
            event_format="Format: Start, End, Text",
        ),
        (
            "[Events]\n"
            f"{EVENT_FORMAT}\n{EVENT_FORMAT}\n"
            "Dialogue: 0,0:00:00.01,0:00:00.02,Default,A,0,0,0,,text\n"
        ).encode(),
        _ass("Dialogue: 0,0:00:00.01,0:00:00.02,Default,A,0,0,0,text"),
        _ass("Dialogue: 0,0:00:00.001,0:00:00.02,Default,A,0,0,0,,text"),
        _ass("Dialogue: 0,0:00:00.02,0:00:00.02,Default,A,0,0,0,,text"),
        _ass("Dialogue: 0,0:00:00.01,0:00:00.02,Default,A,0,0,0,,"),
        _ass("Comment: 0,0:00:00.01,0:00:00.02,Default,A,0,0,0,,text"),
    ],
)
def test_parse_ass_dialogues_rejects_unsupported_profiles(payload: bytes) -> None:
    """Malformed or non-Dialogue Events records fail at the parser seam."""
    with pytest.raises(ValueError, match="ASS"):
        _ = parse_ass_dialogues(payload)
