# Copyright 2026 Transcription Module contributors

"""Typed media preparation adapters for local transcription runs."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import TYPE_CHECKING, Annotated, Protocol, Self, override

from pydantic import Field, StringConstraints, model_validator

from transcription_module.models import (
    FrameId,
    FrozenModel,
    RunId,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.subtitle_inventory import SubtitleInventory  # noqa: TC001

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class MediaToolError(Exception):
    """Failure from a required local media tool."""

    argv: tuple[str, ...]
    stderr: str

    @override
    def __str__(self) -> str:
        """Return the tool diagnostic or a stable fallback."""
        return self.stderr.strip() or f"{self.argv[0]} failed"


class MediaDownloadError(MediaToolError):
    """The source video could not be downloaded."""


class SubtitleDownloadError(MediaToolError):
    """Optional source subtitles could not be downloaded."""


class AudioExtractionError(MediaToolError):
    """The required ASR audio track could not be extracted."""


class FrameExtractionError(MediaToolError):
    """Source frames could not be extracted without resampling."""


class MediaProbeError(MediaToolError):
    """ffprobe could not return a valid media manifest."""


class MediaStateError(MediaToolError):
    """A prepared-media run conflicts with persisted workflow state."""


class OptionalSubtitleWarning(UserWarning):
    """An optional subtitle failed while required media remained usable."""


class FrameProbe(FrozenModel):
    """One decoded frame's real ffprobe timestamp fields."""

    pts: int
    pts_time: str
    best_effort_timestamp: int
    best_effort_timestamp_time: str
    pts_ms: int


class MediaProbe(FrozenModel):
    """Duration and real frame timestamps reported by ffprobe."""

    duration_ms: int
    frames: tuple[FrameProbe, ...]


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    """Required media plus every available Korean subtitle track."""

    video_path: Path
    audio_path: Path
    subtitle_tracks: tuple[PreparedSubtitleTrack, ...]
    probe: MediaProbe
    frame_paths: tuple[Path, ...] = ()


class BrowserCookieSource(StrEnum):
    """Logged-in browser session read directly by yt-dlp."""

    CHROME = "chrome"


class SubtitleSource(StrEnum):
    """Origin declared by YouTube for one Korean subtitle track."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"


class PreparedSubtitleTrack(FrozenModel):
    """One preserved subtitle path with optional policy-bound identity."""

    path: str
    language: str
    source: SubtitleSource
    track_id: NonBlank | None = None
    variant: NonBlank | None = None
    sha256: Sha256Digest | None = None
    eligible_for_speech: bool = False
    duplicate_of: NonBlank | None = None


class PreparedFrameManifest(FrozenModel):
    """One saved image linked to its decoded source PTS."""

    frame_id: FrameId
    frame_index: int
    raw_pts: int
    raw_pts_time: str
    best_effort_timestamp: int
    best_effort_timestamp_time: str
    pts_ms: int
    path: str


class PreparedMediaManifest(FrozenModel):
    """Persisted paths, timing, and optional policy-bound subtitle identity."""

    run_id: RunId
    source_url: YouTubeVideoUrl
    workflow_status: WorkflowStatus
    video_path: str
    audio_path: str
    subtitle_tracks: tuple[PreparedSubtitleTrack, ...] = ()
    subtitle_inventory: SubtitleInventory | None = None
    duration_ms: int
    frame_count: int
    frames: tuple[PreparedFrameManifest, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def subtitle_tracks_match_inventory_admission(self) -> Self:
        """Keep legacy manifests readable and policy-bound speech tracks exact."""
        inventory = self.subtitle_inventory
        if inventory is None:
            if any(track.eligible_for_speech for track in self.subtitle_tracks):
                message = "policy-bound eligible subtitle track requires inventory"
                raise ValueError(message)
            return self
        eligible = inventory.eligible_tracks
        if len(self.subtitle_tracks) != len(eligible):
            message = "manifest subtitle tracks must equal eligible inventory tracks"
            raise ValueError(message)
        eligible_by_id = {track.track_id: track for track in eligible}
        for track in self.subtitle_tracks:
            identity = eligible_by_id.get(track.track_id or "")
            if (
                identity is None
                or PurePath(track.path).name != "automatic.ko.ko-orig.vtt"
                or PurePath(identity.path).name != "automatic.ko.ko-orig.vtt"
                or track.language != "ko"
                or track.source.value != identity.source
                or track.variant != identity.variant
                or track.sha256 != identity.sha256
                or not track.eligible_for_speech
                or track.duplicate_of is not None
            ):
                message = "manifest contains an unadmitted eligible subtitle track"
                raise ValueError(message)
        return self


class YtDlpAdapter(Protocol):
    """Download required video and optional source subtitles."""

    def download_video(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> Path:
        """Download the required source video."""
        ...

    def download_subtitles(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> tuple[PreparedSubtitleTrack, ...]:
        """Download every Korean subtitle track with its source type."""
        ...


class FfmpegAdapter(Protocol):
    """Extract the ASR audio contract from source video."""

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """Create mono 16 kHz PCM WAV audio."""
        ...

    def extract_frames(
        self,
        video_path: Path,
        frames_dir: Path,
        sample_interval_ms: int,
    ) -> tuple[Path, ...]:
        """Save the first source frame in each timeline bucket."""
        ...


class FfprobeAdapter(Protocol):
    """Read duration and actual per-frame PTS values."""

    def probe(self, video_path: Path) -> MediaProbe:
        """Return typed media timing data."""
        ...


@dataclass(frozen=True, slots=True)
class MediaAdapters:
    """Replaceable media tool implementations used by preparation."""

    youtube: YtDlpAdapter
    ffmpeg: FfmpegAdapter
    ffprobe: FfprobeAdapter


def prepare_media(
    source_url: YouTubeVideoUrl,
    output_dir: Path,
    adapters: MediaAdapters,
) -> PreparedMedia:
    """Prepare required video/audio while treating subtitles as optional."""
    video_path = adapters.youtube.download_video(source_url, output_dir)
    try:
        subtitle_tracks = adapters.youtube.download_subtitles(source_url, output_dir)
    except SubtitleDownloadError as error:
        warnings.warn(str(error), OptionalSubtitleWarning, stacklevel=2)
        subtitle_tracks = ()
    else:
        if not subtitle_tracks:
            warnings.warn(
                "no Korean subtitle was available",
                OptionalSubtitleWarning,
                stacklevel=2,
            )
    audio_path = adapters.ffmpeg.extract_audio(video_path, output_dir / "audio.wav")
    full_probe = adapters.ffprobe.probe(video_path)
    sample_interval_ms = 500

    frame_paths = adapters.ffmpeg.extract_frames(
        video_path,
        output_dir / "frames",
        sample_interval_ms,
    )
    sampled_probe = sample_media_probe(full_probe, sample_interval_ms)
    return PreparedMedia(
        video_path=video_path,
        audio_path=audio_path,
        subtitle_tracks=subtitle_tracks,
        probe=sampled_probe,
        frame_paths=frame_paths,
    )


def _sample_frame_indices(
    probe: MediaProbe,
    sample_interval_ms: int,
) -> tuple[int, ...]:
    if not probe.frames:
        return ()
    selected: list[int] = []
    last_bucket = -1
    for index, frame in enumerate(probe.frames):
        bucket = frame.pts_ms // sample_interval_ms
        if bucket > last_bucket:
            selected.append(index)
            last_bucket = bucket
    return tuple(selected)


def sample_media_probe(probe: MediaProbe, sample_interval_ms: int) -> MediaProbe:
    """Keep the first decoded frame in each source-timeline bucket."""
    return MediaProbe(
        duration_ms=probe.duration_ms,
        frames=tuple(
            probe.frames[index]
            for index in _sample_frame_indices(probe, sample_interval_ms)
        ),
    )
