# Copyright 2026 Transcription Module contributors

"""Integration contracts for FFmpeg-backed voice activity detection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from transcription_module.media_subprocess import CommandResult
from transcription_module.vad import FfmpegVadSubprocess, VadDetectionError

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_FIRST_START_MS: Final = 500
EXPECTED_FIRST_END_MS: Final = 1_500
EXPECTED_SECOND_START_MS: Final = 2_000
EXPECTED_SECOND_END_MS: Final = 2_500


class RecordingRunner:
    """Record argv while returning one FFmpeg fixture result."""

    def __init__(self, result: CommandResult) -> None:
        """Initialize the result and empty call ledger."""
        self.result: CommandResult = result
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        """Record one call and return the fixture result."""
        self.calls.append(argv)
        return self.result


def test_ffmpeg_vad_returns_non_silent_intervals(tmp_path: Path) -> None:
    """Silence events become half-open speech intervals in milliseconds."""
    runner = RecordingRunner(
        CommandResult(
            returncode=0,
            stdout="",
            stderr=(
                "[silencedetect] silence_start: 0\n"
                "[silencedetect] silence_end: 0.5 | silence_duration: 0.5\n"
                "[silencedetect] silence_start: 1.5\n"
                "[silencedetect] silence_end: 2.0 | silence_duration: 0.5\n"
            ),
        )
    )
    adapter = FfmpegVadSubprocess(runner=runner)
    audio_path = tmp_path / "audio.wav"

    intervals = adapter.detect(audio_path, duration_ms=2_500)

    assert [
        (interval.start_tc_ms, interval.end_tc_ms)
        for interval in intervals
    ] == [
        (EXPECTED_FIRST_START_MS, EXPECTED_FIRST_END_MS),
        (EXPECTED_SECOND_START_MS, EXPECTED_SECOND_END_MS),
    ]
    assert runner.calls == [
        (
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
    ]


def test_ffmpeg_vad_failure_is_typed(tmp_path: Path) -> None:
    """A failed FFmpeg analysis becomes a typed recovery error."""
    runner = RecordingRunner(
        CommandResult(returncode=1, stdout="", stderr="invalid audio")
    )

    with pytest.raises(VadDetectionError, match="invalid audio"):
        _ = FfmpegVadSubprocess(runner=runner).detect(
            tmp_path / "audio.wav",
            duration_ms=2_500,
        )
