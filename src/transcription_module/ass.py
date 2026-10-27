# Copyright 2026 Transcription Module contributors

"""Strict standard-library parser for the supported ASS Events profile."""

from __future__ import annotations

import re
from dataclasses import dataclass

_EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)
_TIMESTAMP = re.compile(r"([0-9]+):([0-5][0-9]):([0-5][0-9])\.([0-9]{2})")
_FIELD_COUNT = 10


@dataclass(frozen=True, slots=True)
class AssDialogue:
    """One Dialogue record with its original file-order identity."""

    event_index: int
    start_ms: int
    end_ms: int
    style: str
    name: str | None
    effect: str | None
    text: str


def parse_ass_dialogues(payload: bytes) -> tuple[AssDialogue, ...]:
    """Parse strict UTF-8 ten-field ASS Dialogue records or raise ValueError."""
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        message = "ASS must be strict UTF-8"
        raise ValueError(message) from error
    return _event_dialogues(lines)


def _event_dialogues(lines: list[str]) -> tuple[AssDialogue, ...]:
    event_headers = tuple(
        index for index, line in enumerate(lines) if line == "[Events]"
    )
    if len(event_headers) != 1:
        message = "ASS must contain one Events section"
        raise ValueError(message)
    start = event_headers[0] + 1
    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index].startswith("[") and lines[index].endswith("]")
        ),
        len(lines),
    )
    event_lines = lines[start:end]
    format_indexes = tuple(
        index for index, line in enumerate(event_lines) if line.startswith("Format:")
    )
    if len(format_indexes) != 1 or event_lines[format_indexes[0]] != _EVENT_FORMAT:
        message = "ASS Events format is unsupported"
        raise ValueError(message)
    format_index = format_indexes[0]
    if any(line for line in event_lines[:format_index]):
        message = "ASS record precedes Events format"
        raise ValueError(message)
    records = tuple(line for line in event_lines[format_index + 1 :] if line)
    if not records or any(not line.startswith("Dialogue:") for line in records):
        message = "ASS Events Dialogue records are required"
        raise ValueError(message)
    return tuple(
        _parse_dialogue(line, event_index)
        for event_index, line in enumerate(records, start=1)
    )


def _parse_dialogue(line: str, event_index: int) -> AssDialogue:
    record = line.removeprefix("Dialogue:").removeprefix(" ")
    fields = record.split(",", 9)
    if len(fields) != _FIELD_COUNT:
        message = "ASS Dialogue must contain ten fields"
        raise ValueError(message)
    layer, start, end, style, name, margin_l, margin_r, margin_v, effect, text = fields
    if not layer.isdecimal() or any(
        not margin.isdecimal() for margin in (margin_l, margin_r, margin_v)
    ):
        message = "ASS Dialogue numeric fields are malformed"
        raise ValueError(message)
    start_ms = _timestamp_ms(start)
    end_ms = _timestamp_ms(end)
    if end_ms <= start_ms:
        message = "ASS Dialogue timing must be forward"
        raise ValueError(message)
    if not style or not text:
        message = "ASS Dialogue style and text are required"
        raise ValueError(message)
    return AssDialogue(
        event_index=event_index,
        start_ms=start_ms,
        end_ms=end_ms,
        style=style,
        name=name or None,
        effect=effect or None,
        text=text,
    )


def _timestamp_ms(value: str) -> int:
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        message = "ASS timestamp must use H:MM:SS.CS"
        raise ValueError(message)
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1_000 + centiseconds * 10
