# Copyright 2026 Transcription Module contributors

"""FFmpeg-backed voice-activity intervals for missing-speech recovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from transcription_module.media import MediaToolError
from transcription_module.media_subprocess import CommandRunner, LocalCommandRunner
from transcription_module.models import Milliseconds
from transcription_module.recovery import VadSpeechInterval

if TYPE_CHECKING:
    from pathlib import Path

_SILENCE_START = re.compile(r"silence_start:\s*(\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(\d+(?:\.\d+)?)")


class VadDetectionError(MediaToolError):
    """FFmpeg could not produce audio-activity evidence."""


@dataclass(frozen=True, slots=True)
class FfmpegVadSubprocess:
    """Convert FFmpeg silence events into speech intervals."""

    runner: CommandRunner = field(default_factory=LocalCommandRunner)

    def detect(
        self,
        audio_path: Path,
        *,
        duration_ms: int,
    ) -> tuple[VadSpeechInterval, ...]:
        """Return non-silent half-open intervals without shell execution."""
        argv = (
            "ffmpeg",
            "-nostdin",
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=noise=-35dB:d=0.20",
            "-f",
            "null",
            "-",
        )
        result = self.runner.run(argv)
        if result.returncode != 0:
            raise VadDetectionError(argv=argv, stderr=result.stderr)
        silence = _parse_silence(result.stderr, duration_ms)
        speech: list[VadSpeechInterval] = []
        cursor = 0
        for start_ms, end_ms in silence:
            if start_ms > cursor:
                speech.append(
                    VadSpeechInterval(
                        start_tc_ms=Milliseconds(cursor),
                        end_tc_ms=Milliseconds(start_ms),
                    )
                )
            cursor = max(cursor, end_ms)
        if cursor < duration_ms:
            speech.append(
                VadSpeechInterval(
                    start_tc_ms=Milliseconds(cursor),
                    end_tc_ms=Milliseconds(duration_ms),
                )
            )
        return tuple(speech)


def _parse_silence(stderr: str, duration_ms: int) -> tuple[tuple[int, int], ...]:
    intervals: list[tuple[int, int]] = []
    pending_start: int | None = None
    for line in stderr.splitlines():
        if match := _SILENCE_START.search(line):
            pending_start = _seconds_to_ms(match.group(1))
        if match := _SILENCE_END.search(line):
            start_ms = pending_start if pending_start is not None else 0
            end_ms = _seconds_to_ms(match.group(1))
            if end_ms > start_ms:
                intervals.append(
                    (max(0, start_ms), min(duration_ms, end_ms))
                )
            pending_start = None
    if pending_start is not None and pending_start < duration_ms:
        intervals.append((max(0, pending_start), duration_ms))
    return tuple(sorted(intervals))


def _seconds_to_ms(value: str) -> int:
    return int(
        (Decimal(value) * Decimal(1_000)).quantize(
            Decimal(1),
            rounding=ROUND_HALF_UP,
        )
    )
