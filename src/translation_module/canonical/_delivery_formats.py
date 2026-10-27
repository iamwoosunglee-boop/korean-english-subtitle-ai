# Copyright 2026 Transcription Module contributors

"""Render and parse canonical CSV and YouTube SBV delivery projections."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from translation_module.canonical._attempt_models import TranslationAttempt
    from translation_module.canonical.source import SourceTranscriptV2
    from translation_module.canonical.translation import TranslationDocumentV2

_CSV_FIELDS = (
    "sequence",
    "start_tc",
    "end_tc",
    "speakers",
    "source_text",
    "target_text",
    "source_context",
    "rationales_ko",
)
_STRINGS = TypeAdapter(tuple[str, ...])
type CsvProjection = tuple[
    tuple[str, str, str, tuple[str, ...], str, str, str, tuple[str, ...]], ...
]
type SbvProjection = tuple[tuple[int, int, str], ...]


@dataclass(frozen=True, slots=True)
class ReviewTimecodeSpec:
    """Frame rate and counting mode for professional review timecodes."""

    frame_rate_numerator: int
    frame_rate_denominator: int
    drop_frame: bool

    def __post_init__(self) -> None:
        if self.frame_rate_numerator <= 0 or self.frame_rate_denominator <= 0:
            message = "review timecode frame rate must be positive"
            raise ValueError(message)
        if self.drop_frame and (
            self.frame_rate_numerator,
            self.frame_rate_denominator,
        ) not in {(30_000, 1_001), (60_000, 1_001)}:
            message = "drop-frame review timecode requires 30000/1001 or 60000/1001"
            raise ValueError(message)


def _format_review_timecode(
    milliseconds: int,
    spec: ReviewTimecodeSpec,
) -> str:
    denominator = 1_000 * spec.frame_rate_denominator
    total_frames = (
        milliseconds * spec.frame_rate_numerator + denominator // 2
    ) // denominator
    nominal_rate = round(
        spec.frame_rate_numerator / spec.frame_rate_denominator
    )
    separator = ":"
    if spec.drop_frame:
        separator = ";"
        dropped_per_minute = nominal_rate // 15
        frames_per_minute = nominal_rate * 60 - dropped_per_minute
        frames_per_ten_minutes = nominal_rate * 600 - dropped_per_minute * 9
        total_frames %= frames_per_ten_minutes * 6 * 24
        ten_minute_blocks, remainder = divmod(
            total_frames,
            frames_per_ten_minutes,
        )
        dropped = dropped_per_minute * 9 * ten_minute_blocks
        if remainder > dropped_per_minute:
            dropped += dropped_per_minute * (
                (remainder - dropped_per_minute) // frames_per_minute
            )
        total_frames += dropped
    else:
        total_frames %= nominal_rate * 3_600 * 24
    hours, remainder = divmod(total_frames, nominal_rate * 3_600)
    minutes, remainder = divmod(remainder, nominal_rate * 60)
    seconds, frames = divmod(remainder, nominal_rate)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{frames:02}"


def _rationales_by_cue(
    attempt: TranslationAttempt,
) -> dict[str, tuple[str, ...]]:
    by_cue: dict[str, list[tuple[int, str]]] = {}
    if attempt.rationales is None:
        return {}
    for rationale in attempt.rationales:
        by_cue.setdefault(rationale.target_cue_id, []).append(
            (rationale.line_number, rationale.rationale_ko)
        )
    return {
        cue_id: tuple(text for _, text in sorted(values))
        for cue_id, values in by_cue.items()
    }


def csv_projection(
    candidate: TranslationDocumentV2,
    attempt: TranslationAttempt,
    source: SourceTranscriptV2,
    timecode: ReviewTimecodeSpec,
) -> CsvProjection:
    """Project cue and rationale rows in canonical display order."""
    rationales = _rationales_by_cue(attempt)
    source_by_id = {cue.cue_id: cue for cue in source.cues}
    rows: list[
        tuple[str, str, str, tuple[str, ...], str, str, str, tuple[str, ...]]
    ] = []
    for cue in candidate.cues:
        source_cues = tuple(source_by_id[item] for item in cue.source_cue_ids)
        notes: list[str] = []
        for source_cue in source_cues:
            if (
                source_cue.context_note is not None
                and source_cue.context_note not in notes
            ):
                notes.append(source_cue.context_note)
        rows.append(
            (
                str(cue.sequence),
                _format_review_timecode(cue.start_ms, timecode),
                _format_review_timecode(cue.end_ms, timecode),
                cue.speakers,
                "\n".join(source_cue.text for source_cue in source_cues),
                cue.text,
                "\n".join(notes),
                rationales.get(cue.cue_id, ()),
            )
        )
    return tuple(rows)


def render_csv(
    candidate: TranslationDocumentV2,
    attempt: TranslationAttempt,
    source: SourceTranscriptV2,
    timecode: ReviewTimecodeSpec,
) -> bytes:
    """Render spreadsheet-safe UTF-8 CSV with ordered rationale arrays."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    for (
        sequence,
        start,
        end,
        speakers,
        source_text,
        target_text,
        source_context,
        rationales,
    ) in csv_projection(candidate, attempt, source, timecode):
        writer.writerow(
            (
                sequence,
                start,
                end,
                json.dumps(speakers, ensure_ascii=False, separators=(",", ":")),
                source_text,
                target_text,
                source_context,
                json.dumps(rationales, ensure_ascii=False, separators=(",", ":")),
            )
        )
    return f"\ufeff{output.getvalue()}".encode()


def parse_csv(payload: bytes) -> CsvProjection | None:
    """Parse the review CSV into its comparable canonical projection."""
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        if tuple(reader.fieldnames or ()) != _CSV_FIELDS:
            return None
        rows = [
            (
                (
                    row["sequence"],
                    row["start_tc"],
                    row["end_tc"],
                    _STRINGS.validate_json(row["speakers"]),
                    row["source_text"],
                    row["target_text"],
                    row["source_context"],
                    _STRINGS.validate_json(row["rationales_ko"]),
                )
            )
            for row in reader
        ]
    except (KeyError, UnicodeDecodeError, ValueError):
        return None
    return tuple(rows)


def _format_sbv_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours}:{minutes:02}:{seconds:02}.{millis:03}"


def render_sbv(candidate: TranslationDocumentV2) -> bytes:
    """Render YouTube SBV without speaker-name decoration."""
    blocks = (
        f"{_format_sbv_time(cue.start_ms)},{_format_sbv_time(cue.end_ms)}\n{cue.text}"
        for cue in candidate.cues
    )
    return ("\n\n".join(blocks) + "\n").encode()


def _parse_sbv_time(value: str) -> int:
    hours_text, minutes_text, remainder = value.split(":", maxsplit=2)
    seconds_text, millis_text = remainder.split(".", maxsplit=1)
    return (
        int(hours_text) * 3_600_000
        + int(minutes_text) * 60_000
        + int(seconds_text) * 1_000
        + int(millis_text)
    )


def sbv_projection(candidate: TranslationDocumentV2) -> SbvProjection:
    """Project SBV-supported timing and displayed text."""
    return tuple((cue.start_ms, cue.end_ms, cue.text) for cue in candidate.cues)


def parse_sbv(payload: bytes) -> SbvProjection | None:
    """Parse SBV blocks into comparable timing and displayed text."""
    try:
        blocks = payload.decode().strip().split("\n\n")
        parsed: list[tuple[int, int, str]] = []
        for block in blocks:
            timing, *text = block.splitlines()
            start, end = timing.split(",", maxsplit=1)
            parsed.append(
                (_parse_sbv_time(start), _parse_sbv_time(end), "\n".join(text))
            )
    except (UnicodeDecodeError, ValueError):
        return None
    return tuple(parsed)


__all__ = [
    "ReviewTimecodeSpec",
    "csv_projection",
    "parse_csv",
    "parse_sbv",
    "render_csv",
    "render_sbv",
    "sbv_projection",
]
