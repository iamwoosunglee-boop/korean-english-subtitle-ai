# Copyright 2026 Transcription Module contributors

"""Concrete local-tool implementations for media preparation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Protocol

import anyio
from pydantic import ValidationError

from transcription_module import subtitle_admission
from transcription_module.media import (
    AudioExtractionError,
    BrowserCookieSource,
    FrameExtractionError,
    FrameProbe,
    MediaDownloadError,
    MediaProbe,
    MediaProbeError,
    PreparedSubtitleTrack,
    SubtitleDownloadError,
    SubtitleSource,
)
from transcription_module.models import FrozenModel, YouTubeVideoUrl

if TYPE_CHECKING:
    from pathlib import Path
    from subprocess import CompletedProcess


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Captured result from one argv-only local process."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Run a local tool without invoking a command shell."""

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        """Return captured process output without raising on tool failure."""
        ...


@dataclass(frozen=True, slots=True)
class LocalCommandRunner:
    """Concrete anyio subprocess runner shared by local media tools."""

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        """Run one argv vector and capture decoded output."""
        completed = anyio.run(_run_process, argv)
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout.decode(),
            stderr=completed.stderr.decode(),
        )


async def _run_process(argv: tuple[str, ...]) -> CompletedProcess[bytes]:
    return await anyio.run_process(argv, check=False)


@dataclass(frozen=True, slots=True)
class YtDlpSubprocess:
    """yt-dlp argv contract for video and Korean subtitle downloads."""

    browser: BrowserCookieSource = BrowserCookieSource.CHROME
    runner: CommandRunner = LocalCommandRunner()

    def download_video(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> Path:
        """Download and merge the required source video."""
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / "source.mp4"
        argv = (
            "yt-dlp",
            "--ignore-config",
            "--no-playlist",
            "--cookies-from-browser",
            self.browser.value,
            "--format",
            "bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
            "--output",
            str(destination),
            str(source_url),
        )
        result = self.runner.run(argv)
        if result.returncode != 0:
            raise MediaDownloadError(argv=argv, stderr=result.stderr)
        return destination

    def download_subtitles(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> tuple[PreparedSubtitleTrack, ...]:
        """Download only the exact automatic Korean-original VTT."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_template = output_dir / "automatic.%(language)s.%(ext)s"
        expected = output_dir / subtitle_admission.AUTOMATIC_KO_ORIG_FILENAME
        argv = (
            "yt-dlp",
            "--ignore-config",
            "--no-playlist",
            "--cookies-from-browser",
            self.browser.value,
            "--skip-download",
            "--write-auto-subs",
            "--sub-langs",
            "ko-orig",
            "--sub-format",
            "vtt",
            "--output",
            str(output_template),
            str(source_url),
        )
        result = self.runner.run(argv)
        if result.returncode != 0:
            raise SubtitleDownloadError(argv=argv, stderr=result.stderr)
        unexpected = tuple(
            path for path in sorted(output_dir.glob("*.vtt")) if path != expected
        )
        if unexpected:
            message = f"unexpected subtitle variant: {unexpected[0].name}"
            raise subtitle_admission.SubtitleAdmissionError(message)
        if not expected.is_file():
            return ()
        with expected.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return (
            PreparedSubtitleTrack(
                path=str(expected),
                language="ko",
                source=SubtitleSource.AUTOMATIC,
                track_id="automatic-ko-orig",
                variant="ko-orig",
                sha256=digest,
                eligible_for_speech=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class FfmpegSubprocess:
    """FFmpeg argv contract for deterministic Whisper input audio."""

    runner: CommandRunner = LocalCommandRunner()

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """Create mono 16 kHz PCM WAV audio."""
        argv = (
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        )
        result = self.runner.run(argv)
        if result.returncode != 0:
            raise AudioExtractionError(argv=argv, stderr=result.stderr)
        return audio_path

    def extract_frames(
        self,
        video_path: Path,
        frames_dir: Path,
        sample_interval_ms: int,
    ) -> tuple[Path, ...]:
        """Save the first decoded frame in each source-time bucket."""
        frames_dir.mkdir(parents=True, exist_ok=True)
        output_template = frames_dir / "f%06d.jpg"
        interval_seconds = f"{sample_interval_ms / 1_000:g}"
        selection = (
            "isnan(prev_selected_t)+"
            f"gt(floor(t/{interval_seconds})\\,"
            f"floor(prev_selected_t/{interval_seconds}))"
        )
        argv = (
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"select={selection}",
            "-fps_mode",
            "vfr",
            "-q:v",
            "2",
            str(output_template),
        )
        result = self.runner.run(argv)
        if result.returncode != 0:
            raise FrameExtractionError(argv=argv, stderr=result.stderr)
        frame_paths = tuple(sorted(frames_dir.glob("f*.jpg")))
        if not frame_paths:
            raise FrameExtractionError(
                argv=argv,
                stderr="ffmpeg produced no frame images",
            )
        return frame_paths


class _ProbeFormat(FrozenModel):
    duration: Decimal


class _ProbeFrame(FrozenModel):
    pts: int
    pts_time: str
    best_effort_timestamp: int
    best_effort_timestamp_time: str


class _ProbePayload(FrozenModel):
    format: _ProbeFormat
    frames: tuple[_ProbeFrame, ...]


@dataclass(frozen=True, slots=True)
class FfprobeSubprocess:
    """ffprobe JSON contract preserving actual frame PTS fields."""

    runner: CommandRunner = LocalCommandRunner()

    def probe(self, video_path: Path) -> MediaProbe:
        """Return duration and actual decoded-frame timestamps."""
        argv = (
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            (
                "format=duration:"
                "frame=pts,pts_time,best_effort_timestamp,"
                "best_effort_timestamp_time"
            ),
            "-of",
            "json",
            str(video_path),
        )
        result = self.runner.run(argv)
        if result.returncode != 0:
            raise MediaProbeError(argv=argv, stderr=result.stderr)
        try:
            payload = _ProbePayload.model_validate_json(result.stdout)
        except ValidationError as error:
            raise MediaProbeError(argv=argv, stderr=str(error)) from error
        frames = tuple(
            FrameProbe(
                pts=frame.pts,
                pts_time=frame.pts_time,
                best_effort_timestamp=frame.best_effort_timestamp,
                best_effort_timestamp_time=frame.best_effort_timestamp_time,
                pts_ms=_seconds_to_milliseconds(Decimal(frame.pts_time)),
            )
            for frame in payload.frames
        )
        return MediaProbe(
            duration_ms=_seconds_to_milliseconds(payload.format.duration),
            frames=frames,
        )


def _seconds_to_milliseconds(seconds: Decimal) -> int:
    return int((seconds * 1_000).to_integral_value(rounding=ROUND_HALF_UP))
