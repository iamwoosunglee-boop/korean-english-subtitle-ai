# Copyright 2026 Transcription Module contributors

"""Integration contracts for media preparation and local ASR adapters."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, override

import pytest
from pydantic import ValidationError

from transcription_module.asr import (
    DEFAULT_ASR_MODEL,
    AsrRunArtifact,
    AsrSegment,
    AsrTranscript,
    JsonValue,
    MlxWhisperPython,
)
from transcription_module.media import (
    AudioExtractionError,
    FrameExtractionError,
    FrameProbe,
    MediaAdapters,
    MediaDownloadError,
    MediaProbe,
    OptionalSubtitleWarning,
    PreparedMedia,
    PreparedMediaManifest,
    PreparedSubtitleTrack,
    SubtitleDownloadError,
    SubtitleSource,
    prepare_media,
)
from transcription_module.media_subprocess import (
    CommandResult,
    FfmpegSubprocess,
    FfprobeSubprocess,
    YtDlpSubprocess,
)
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.stages import transcribe_media_run
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    default_evidence_policy,
    policy_digest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

EXPECTED_DURATION_MS: Final = 1_234
EXPECTED_FRAME_PTS: Final = 1_001
EXPECTED_FRAME_MS: Final = 42
EXPECTED_SEGMENT_START_MS: Final = 125
EXPECTED_SEGMENT_END_MS: Final = 1_750
EXPECTED_SAMPLED_FRAME_COUNT: Final = 3
FRAME_SAMPLE_INTERVAL_MS: Final = 500
EXPECTED_AVG_LOGPROB: Final = -0.25
EXPECTED_NO_SPEECH_PROBABILITY: Final = 0.1
EXPECTED_WORD_PROBABILITY: Final = 0.92
EXPECTED_SOURCE_ORDINAL: Final = 2


class RecordingRunner:
    """Record argv values while returning one fixture result."""

    def __init__(self, result: CommandResult) -> None:
        """Initialize the fixture result and empty call ledger."""
        self.result: CommandResult = result
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...]) -> CommandResult:
        """Record one argv call and return the configured result."""
        self.calls.append(argv)
        return self.result


class SubtitleFailingYtDlp:
    """Provide video while failing the optional subtitle path."""

    def download_video(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> Path:
        """Return a deterministic source path."""
        del source_url
        return output_dir / "source.mp4"

    def download_subtitles(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> tuple[PreparedSubtitleTrack, ...]:
        """Raise the typed optional-subtitle failure."""
        del source_url, output_dir
        raise SubtitleDownloadError(
            argv=("yt-dlp", "--write-subs"),
            stderr="subtitles unavailable",
        )


class SubtitleMissingYtDlp:
    """Provide required video while reporting no Korean subtitle."""

    def download_video(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> Path:
        """Return a deterministic source path."""
        del source_url
        return output_dir / "source.mp4"

    def download_subtitles(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> tuple[PreparedSubtitleTrack, ...]:
        """Return no optional subtitle file."""
        del source_url, output_dir
        return ()


class FixtureFfmpeg:
    """Return the requested fixture audio destination."""

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """Return the destination without invoking FFmpeg."""
        del video_path
        return audio_path

    def extract_frames(
        self,
        video_path: Path,
        frames_dir: Path,
        sample_interval_ms: int,
    ) -> tuple[Path, ...]:
        """Return no frames for the timing-free fixture."""
        del video_path, frames_dir, sample_interval_ms
        return ()


class FixtureFfprobe:
    """Return deterministic fixture duration without frames."""

    def probe(self, video_path: Path) -> MediaProbe:
        """Return one fixed probe response."""
        del video_path
        return MediaProbe(duration_ms=EXPECTED_DURATION_MS, frames=())


class SubtitleBundleYtDlp:
    """Return both manual and automatic Korean tracks unchanged."""

    def download_video(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> Path:
        """Return the fixture source video path."""
        del source_url
        return output_dir / "source.mp4"

    def download_subtitles(
        self,
        source_url: YouTubeVideoUrl,
        output_dir: Path,
    ) -> tuple[PreparedSubtitleTrack, ...]:
        """Return both source-distinct Korean tracks."""
        del source_url
        return (
            PreparedSubtitleTrack(
                path=str(output_dir / "manual.ko.vtt"),
                language="ko",
                source=SubtitleSource.MANUAL,
            ),
            PreparedSubtitleTrack(
                path=str(output_dir / "automatic.ko.vtt"),
                language="ko",
                source=SubtitleSource.AUTOMATIC,
            ),
        )


class SamplingFfmpeg(FixtureFfmpeg):
    """Record the requested source-timeline sampling interval."""

    def __init__(self) -> None:
        """Start without a requested interval."""
        self.sample_interval_ms: int | None = None

    @override
    def extract_frames(
        self,
        video_path: Path,
        frames_dir: Path,
        sample_interval_ms: int,
    ) -> tuple[Path, ...]:
        """Record the interval and return paths for each fixture bucket."""
        del video_path
        self.sample_interval_ms = sample_interval_ms
        return tuple(
            frames_dir / f"f{position:06d}.jpg"
            for position in range(1, EXPECTED_SAMPLED_FRAME_COUNT + 1)
        )


class SamplingFfprobe:
    """Return VFR timestamps around each half-second sampling target."""

    def probe(self, video_path: Path) -> MediaProbe:
        """Return deterministic VFR timestamps."""
        del video_path
        return MediaProbe(
            duration_ms=2_000,
            frames=tuple(
                FrameProbe(
                    pts=index,
                    pts_time=f"{pts_ms / 1000:.3f}",
                    best_effort_timestamp=index,
                    best_effort_timestamp_time=f"{pts_ms / 1000:.3f}",
                    pts_ms=pts_ms,
                )
                for index, pts_ms in enumerate((0, 490, 510, 999, 1_501))
            ),
        )


class FixtureMlxWhisper:
    """Capture the mlx-whisper Python call and return fixture segments."""

    def __init__(
        self,
        *,
        include_empty_segment: bool = False,
        prepend_empty_segment: bool = False,
    ) -> None:
        """Initialize captured call values."""
        self.audio_path: str = ""
        self.options: dict[str, str | bool] = {}
        self.include_empty_segment: bool = include_empty_segment
        self.prepend_empty_segment: bool = prepend_empty_segment

    def transcribe(
        self,
        audio_path: str,
        *,
        path_or_hf_repo: str,
        word_timestamps: bool,
        language: str,
    ) -> Mapping[str, JsonValue]:
        """Capture options and return one Korean segment."""
        self.audio_path = audio_path
        self.options = {
            "path_or_hf_repo": path_or_hf_repo,
            "word_timestamps": word_timestamps,
            "language": language,
        }
        segments: list[JsonValue] = [
            {
                "id": 0,
                "start": 0.125,
                "end": 1.75,
                "text": " 안녕하세요 ",
                "avg_logprob": -0.25,
                "no_speech_prob": 0.1,
                "words": [
                    {
                        "word": " 안녕",
                        "start": 0.125,
                        "end": 0.5,
                        "probability": 0.92,
                    }
                ],
            }
        ]
        empty_segment: JsonValue = {
            "id": 1,
            "start": 1.75,
            "end": 2.0,
            "text": "   ",
            "words": [],
        }
        if self.prepend_empty_segment:
            segments.insert(0, empty_segment)
        elif self.include_empty_segment:
            segments.append(empty_segment)
        return {
            "text": " 안녕하세요 ",
            "language": "ko",
            "task": "transcribe",
            "segments": segments,
        }


def test_prepare_media_warns_and_continues_when_subtitles_fail(
    tmp_path: Path,
) -> None:
    """Optional subtitle failure warns while required processing continues."""
    # Given
    adapters = MediaAdapters(
        youtube=SubtitleFailingYtDlp(),
        ffmpeg=FixtureFfmpeg(),
        ffprobe=FixtureFfprobe(),
    )

    # When
    with pytest.warns(OptionalSubtitleWarning, match="subtitles unavailable"):
        prepared = prepare_media(
            YouTubeVideoUrl("https://www.youtube.com/watch?v=fixture"),
            tmp_path,
            adapters,
        )

    # Then
    assert prepared == PreparedMedia(
        video_path=tmp_path / "source.mp4",
        audio_path=tmp_path / "audio.wav",
        subtitle_tracks=(),
        probe=MediaProbe(duration_ms=EXPECTED_DURATION_MS, frames=()),
    )


def test_prepare_media_warns_and_continues_when_subtitles_are_missing(
    tmp_path: Path,
) -> None:
    """Missing optional subtitles warn while required processing continues."""
    adapters = MediaAdapters(
        youtube=SubtitleMissingYtDlp(),
        ffmpeg=FixtureFfmpeg(),
        ffprobe=FixtureFfprobe(),
    )

    with pytest.warns(OptionalSubtitleWarning, match="no Korean subtitle"):
        prepared = prepare_media(
            YouTubeVideoUrl("https://www.youtube.com/watch?v=fixture"),
            tmp_path,
            adapters,
        )

    assert prepared.subtitle_tracks == ()
    assert prepared.audio_path == tmp_path / "audio.wav"


def test_prepare_media_preserves_all_tracks_and_samples_vfr_at_two_fps(
    tmp_path: Path,
) -> None:
    """All Korean tracks survive while frames sample actual source PTS."""
    ffmpeg = SamplingFfmpeg()

    prepared = prepare_media(
        YouTubeVideoUrl("https://www.youtube.com/watch?v=fixture"),
        tmp_path,
        MediaAdapters(
            youtube=SubtitleBundleYtDlp(),
            ffmpeg=ffmpeg,
            ffprobe=SamplingFfprobe(),
        ),
    )

    assert [track.source for track in prepared.subtitle_tracks] == [
        SubtitleSource.MANUAL,
        SubtitleSource.AUTOMATIC,
    ]
    assert ffmpeg.sample_interval_ms == FRAME_SAMPLE_INTERVAL_MS
    assert [frame.pts_ms for frame in prepared.probe.frames] == [
        0,
        510,
        1_501,
    ]
    assert len(prepared.frame_paths) == EXPECTED_SAMPLED_FRAME_COUNT


def test_yt_dlp_maps_video_failure_to_typed_error(tmp_path: Path) -> None:
    """Required video download failure becomes a typed error."""
    # Given
    runner = RecordingRunner(
        CommandResult(returncode=1, stdout="", stderr="video unavailable")
    )
    adapter = YtDlpSubprocess(runner=runner)

    # When
    with pytest.raises(MediaDownloadError, match="video unavailable"):
        _ = adapter.download_video(
            YouTubeVideoUrl("https://www.youtube.com/watch?v=fixture"),
            tmp_path,
        )

    # Then
    assert runner.calls == [
        (
            "yt-dlp",
            "--ignore-config",
            "--no-playlist",
            "--cookies-from-browser",
            "chrome",
            "--format",
            "bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
            "--output",
            str(tmp_path / "source.mp4"),
            "https://www.youtube.com/watch?v=fixture",
        )
    ]


def test_yt_dlp_requests_only_exact_automatic_ko_orig(
    tmp_path: Path,
) -> None:
    """Subtitle acquisition makes one exact automatic ko-orig request."""
    class SubtitleRunner(RecordingRunner):
        @override
        def run(self, argv: tuple[str, ...]) -> CommandResult:
            self.calls.append(argv)
            _ = (tmp_path / "automatic.ko.ko-orig.vtt").write_text(
                "WEBVTT\n", encoding="utf-8"
            )
            return CommandResult(returncode=0, stdout="", stderr="")

    runner = SubtitleRunner(CommandResult(returncode=0, stdout="", stderr=""))
    tracks = YtDlpSubprocess(runner=runner).download_subtitles(
        YouTubeVideoUrl("https://www.youtube.com/watch?v=fixture"),
        tmp_path,
    )

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert "--write-auto-subs" in call
    assert "--write-subs" not in call
    language_option = call.index("--sub-langs")
    assert call[language_option + 1] == "ko-orig"
    browser_option = call.index("--cookies-from-browser")
    assert call[browser_option + 1] == "chrome"
    assert len(tracks) == 1
    track = tracks[0]
    assert track.source is SubtitleSource.AUTOMATIC
    assert track.language == "ko"
    assert track.variant == "ko-orig"
    assert track.track_id
    assert track.sha256 == (
        "73420fdc06730575e08b8a09b5b3824e0f2a9d2dd742640613490ef3abac2bd0"
    )
    assert track.eligible_for_speech is True


def test_ffmpeg_maps_audio_failure_to_typed_error(tmp_path: Path) -> None:
    """Required audio extraction failure becomes a typed error."""
    # Given
    runner = RecordingRunner(
        CommandResult(returncode=1, stdout="", stderr="no audio stream")
    )
    adapter = FfmpegSubprocess(runner=runner)
    video_path = tmp_path / "source.mp4"
    audio_path = tmp_path / "audio.wav"

    # When
    with pytest.raises(AudioExtractionError, match="no audio stream"):
        _ = adapter.extract_audio(video_path, audio_path)

    # Then
    assert runner.calls == [
        (
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
    ]


def test_ffmpeg_extracts_two_fps_from_source_timestamps(
    tmp_path: Path,
) -> None:
    """Frame extraction uses a constant-size source-time bucket filter."""
    runner = RecordingRunner(CommandResult(returncode=0, stdout="", stderr=""))
    adapter = FfmpegSubprocess(runner=runner)
    video_path = tmp_path / "source.mp4"
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    expected_frame = frames_dir / "f000001.jpg"
    _ = expected_frame.write_bytes(b"fixture")

    frame_paths = adapter.extract_frames(
        video_path,
        frames_dir,
        FRAME_SAMPLE_INTERVAL_MS,
    )

    assert frame_paths == (expected_frame,)
    assert runner.calls == [
        (
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            (
                "select=isnan(prev_selected_t)+"
                "gt(floor(t/0.5)\\,floor(prev_selected_t/0.5))"
            ),
            "-fps_mode",
            "vfr",
            "-q:v",
            "2",
            str(frames_dir / "f%06d.jpg"),
        )
    ]


def test_ffmpeg_maps_frame_failure_to_typed_error(tmp_path: Path) -> None:
    """Frame extraction failure becomes a typed required-media error."""
    runner = RecordingRunner(
        CommandResult(returncode=1, stdout="", stderr="frame decode failed")
    )
    adapter = FfmpegSubprocess(runner=runner)

    with pytest.raises(FrameExtractionError, match="frame decode failed"):
        _ = adapter.extract_frames(
            tmp_path / "source.mp4",
            tmp_path / "frames",
            FRAME_SAMPLE_INTERVAL_MS,
        )


def test_ffprobe_preserves_actual_pts_fields(tmp_path: Path) -> None:
    """Ffprobe output preserves source PTS rather than average FPS."""
    # Given
    runner = RecordingRunner(
        CommandResult(
            returncode=0,
            stdout=(
                '{"format":{"duration":"1.234"},'
                '"frames":['
                '{"pts":0,"pts_time":"0.000000",'
                '"best_effort_timestamp":0,'
                '"best_effort_timestamp_time":"0.000000"},'
                '{"pts":1001,"pts_time":"0.041708",'
                '"best_effort_timestamp":1001,'
                '"best_effort_timestamp_time":"0.041708"}'
                "]}"
            ),
            stderr="",
        )
    )
    adapter = FfprobeSubprocess(runner=runner)
    video_path = tmp_path / "variable-frame-rate.mp4"

    # When
    probe = adapter.probe(video_path)

    # Then
    assert probe.duration_ms == EXPECTED_DURATION_MS
    assert probe.frames[1].pts == EXPECTED_FRAME_PTS
    assert probe.frames[1].pts_time == "0.041708"
    assert probe.frames[1].best_effort_timestamp == EXPECTED_FRAME_PTS
    assert probe.frames[1].best_effort_timestamp_time == "0.041708"
    assert probe.frames[1].pts_ms == EXPECTED_FRAME_MS


def test_mlx_whisper_uses_pinned_python_contract(tmp_path: Path) -> None:
    """The default adapter calls the pinned large-v3-turbo contract."""
    # Given
    module = FixtureMlxWhisper()
    adapter = MlxWhisperPython(module=module)
    audio_path = tmp_path / "audio.wav"

    # When
    transcript = adapter.transcribe(audio_path)

    # Then
    assert module.audio_path == str(audio_path)
    assert module.options == {
        "path_or_hf_repo": DEFAULT_ASR_MODEL,
        "word_timestamps": True,
        "language": "ko",
    }
    assert transcript.text == "안녕하세요"
    assert transcript.segments[0].start_ms == EXPECTED_SEGMENT_START_MS
    assert transcript.segments[0].end_ms == EXPECTED_SEGMENT_END_MS
    assert transcript.segments[0].speech == "안녕하세요"
    assert transcript.segments[0].avg_logprob == EXPECTED_AVG_LOGPROB
    assert (
        transcript.segments[0].no_speech_probability
        == EXPECTED_NO_SPEECH_PROBABILITY
    )
    assert transcript.segments[0].words[0].speech == "안녕"
    assert (
        transcript.segments[0].words[0].probability
        == EXPECTED_WORD_PROBABILITY
    )


def test_mlx_whisper_omits_empty_segments(tmp_path: Path) -> None:
    """Whitespace-only decoder segments do not abort a valid transcript."""
    # Given
    adapter = MlxWhisperPython(
        module=FixtureMlxWhisper(include_empty_segment=True)
    )

    # When
    transcript = adapter.transcribe(tmp_path / "audio.wav")

    # Then
    assert [segment.speech for segment in transcript.segments] == ["안녕하세요"]


def test_mlx_whisper_preserves_source_ordinal_across_empty_filter(
    tmp_path: Path,
) -> None:
    """A retained segment keeps its original decoder position after filtering."""
    adapter = MlxWhisperPython(
        module=FixtureMlxWhisper(prepend_empty_segment=True)
    )

    transcript = adapter.transcribe(tmp_path / "audio.wav")

    assert len(transcript.segments) == 1
    assert transcript.segments[0].source_ordinal == EXPECTED_SOURCE_ORDINAL


def test_legacy_asr_artifact_without_ordinals_remains_readable() -> None:
    """Historical ASR JSON parses without policy bindings or source ordinals."""
    artifact = AsrRunArtifact.model_validate(
        {
            "run_id": "legacy-asr-run",
            "model": DEFAULT_ASR_MODEL,
            "transcript": {
                "text": "기존 발화",
                "language": "ko",
                "segments": [
                    {
                        "start_ms": 0,
                        "end_ms": 500,
                        "speech": "기존 발화",
                    }
                ],
            },
        }
    )

    assert artifact.era is EvidenceEra.LEGACY
    assert artifact.transcript.segments[0].source_ordinal is None


def test_policy_bound_asr_requires_every_source_ordinal() -> None:
    """New-era ASR cannot persist a segment without stable source identity."""
    with pytest.raises(ValidationError, match="source ordinal"):
        _ = AsrRunArtifact(
            run_id=RunId("policy-asr-run"),
            era=EvidenceEra.POLICY_BOUND,
            policy_digest="a" * 64,
            audio_sha256="b" * 64,
            transcript=AsrTranscript(
                text="정책 발화",
                language="ko",
                segments=(
                    AsrSegment(start_ms=0, end_ms=500, speech="정책 발화"),
                ),
            ),
        )


def test_asr_stage_persists_evidence_without_creating_cues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASR remains evidence until the window-drafting stages consume it."""
    run_id = RunId("00000000-0000-0000-0000-000000000003")
    store = RunStore(tmp_path / "runs")
    _ = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=asr-evidence"
            ),
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    paths = store.paths(run_id)
    audio_path = paths.run_dir / "input" / "audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_payload = b"policy-bound audio"
    _ = audio_path.write_bytes(audio_payload)
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=asr-evidence"
            ),
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(paths.run_dir / "input" / "source.mp4"),
            audio_path=str(audio_path),
            duration_ms=2_500,
            frame_count=0,
        ),
    )
    monkeypatch.setattr(
        "transcription_module.stages.load_mlx_whisper",
        lambda: MlxWhisperPython(module=FixtureMlxWhisper()),
    )
    vad_calls: list[RunId] = []

    def record_vad(
        vad_store: RunStore,
        vad_run_id: RunId,
    ) -> VadEvidenceArtifact:
        assert vad_store is store
        vad_calls.append(vad_run_id)
        return VadEvidenceArtifact(
            run_id=vad_run_id,
            policy_digest=policy_digest(default_evidence_policy()),
            audio_sha256=hashlib.sha256(audio_payload).hexdigest(),
            ffmpeg_filter="silencedetect=noise=-35dB:d=0.20",
            duration_ms=2_500,
            speech_intervals=(),
        )

    monkeypatch.setattr(
        "transcription_module.stages.ensure_vad_evidence",
        record_vad,
        raising=False,
    )

    artifact = transcribe_media_run(run_id, store)

    assert vad_calls == [run_id]
    assert artifact.era is EvidenceEra.POLICY_BOUND
    assert artifact.policy_digest == policy_digest(default_evidence_policy())
    assert artifact.audio_sha256 == hashlib.sha256(audio_payload).hexdigest()
    assert artifact.transcript.segments[0].source_ordinal == 1
    assert artifact.transcript.segments[0].speech == "안녕하세요"
    assert paths.asr_transcript.exists()
    assert not paths.transcript.exists()
    assert store.load_state(run_id).validation_status is ValidationStatus.PENDING
