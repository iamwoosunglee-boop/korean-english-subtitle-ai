# Copyright 2026 Transcription Module contributors

"""Media preparation resumes only invalid concrete checkpoint stages."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import TYPE_CHECKING, Final, override

import pytest

from transcription_module.media import (
    AudioExtractionError,
    FrameExtractionError,
    FrameProbe,
    MediaAdapters,
    MediaDownloadError,
    MediaProbe,
    MediaProbeError,
    PreparedSubtitleTrack,
    SubtitleSource,
)
from transcription_module.media_checkpoint import (
    CheckpointStorageError,
    MediaAdapterContracts,
    MediaCheckpointStore,
    ReceiptStatus,
    StageName,
)
from transcription_module.media_resume import (
    DEFAULT_MEDIA_ADAPTER_CONTRACTS,
    prepare_media_with_checkpoint,
)
from transcription_module.media_runtime import CheckpointedMediaAdapters
from transcription_module.models import YouTubeVideoUrl
from transcription_module.visual_evidence_policy import (
    default_evidence_policy,
    policy_digest,
    subtitle_adapter_contract,
)

if TYPE_CHECKING:
    from pathlib import Path

SOURCE: Final = YouTubeVideoUrl("https://youtube.test/watch?v=first")
OTHER_SOURCE: Final = YouTubeVideoUrl("https://youtube.test/watch?v=second")
FRAME_INTERVAL_MS: Final = 500
EXPECTED_FRAME_COUNT: Final = 2
EXPECTED_SUBTITLE_CALLS_AFTER_CHANGE: Final = 2
CONTRACTS: Final = MediaAdapterContracts(
    downloader="fixture-video.v1",
    subtitles="fixture-subtitles.v1",
    audio="fixture-audio.v1",
    probe="fixture-probe.v1",
    frames="fixture-frames.v1",
)


class FakeMediaAdapters:
    """Mutable deterministic adapter fixture with injectable failures."""

    def __init__(self) -> None:
        """Initialize an empty call ledger and successful stage behavior."""
        self.calls: Counter[str] = Counter()
        self.fail_video: bool = False
        self.fail_frames: bool = False
        self.fail_audio: bool = False
        self.subtitles_available: bool = True

    def download_video(self, source_url: YouTubeVideoUrl, output_dir: Path) -> Path:
        """Write source-specific video bytes."""
        self.calls["video"] += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.fail_video:
            raise MediaDownloadError(argv=("fake-video",), stderr="failed")
        destination = output_dir / "source.mp4"
        _ = destination.write_bytes(f"video:{source_url}".encode())
        return destination

    def download_subtitles(
        self, source_url: YouTubeVideoUrl, output_dir: Path
    ) -> tuple[PreparedSubtitleTrack, ...]:
        """Write one subtitle track unless absence is configured."""
        del source_url
        self.calls["subtitles"] += 1
        if not self.subtitles_available:
            return ()
        destination = output_dir / "automatic.ko.ko-orig.vtt"
        payload = b"WEBVTT\nfixture\n"
        _ = destination.write_bytes(payload)
        return (
            PreparedSubtitleTrack(
                path=str(destination),
                language="ko",
                source=SubtitleSource.AUTOMATIC,
                track_id="automatic-ko-orig",
                variant="ko-orig",
                sha256=hashlib.sha256(payload).hexdigest(),
                eligible_for_speech=True,
            ),
        )

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        """Write deterministic audio or fail after a partial write."""
        self.calls["audio"] += 1
        _ = audio_path.write_bytes(b"partial-audio")
        if self.fail_audio:
            raise AudioExtractionError(argv=("fake-audio",), stderr="failed")
        _ = audio_path.write_bytes(b"audio:" + video_path.read_bytes())
        return audio_path

    def probe(self, video_path: Path) -> MediaProbe:
        """Return timestamps spanning two half-second buckets."""
        del video_path
        self.calls["probe"] += 1
        return MediaProbe(
            duration_ms=1_000,
            frames=tuple(
                FrameProbe(
                    pts=index,
                    pts_time=f"{pts_ms / 1_000:g}",
                    best_effort_timestamp=index,
                    best_effort_timestamp_time=f"{pts_ms / 1_000:g}",
                    pts_ms=pts_ms,
                )
                for index, pts_ms in enumerate((0, 250, 500))
            ),
        )

    def extract_frames(
        self, video_path: Path, frames_dir: Path, sample_interval_ms: int
    ) -> tuple[Path, ...]:
        """Write two frames or fail after the first partial frame."""
        del video_path
        assert sample_interval_ms == FRAME_INTERVAL_MS
        self.calls["frames"] += 1
        frames_dir.mkdir(parents=True, exist_ok=True)
        first = frames_dir / "f000001.jpg"
        _ = first.write_bytes(b"frame-one")
        if self.fail_frames:
            raise FrameExtractionError(argv=("fake-frames",), stderr="failed")
        second = frames_dir / "f000002.jpg"
        _ = second.write_bytes(b"frame-two")
        return first, second

    def bundle(self) -> MediaAdapters:
        """Expose this fixture through all three adapter protocols."""
        return MediaAdapters(youtube=self, ffmpeg=self, ffprobe=self)


def _runtime(
    fake: FakeMediaAdapters,
    contracts: MediaAdapterContracts = CONTRACTS,
) -> CheckpointedMediaAdapters:
    return CheckpointedMediaAdapters(fake.bundle(), contracts)


def _hashes(root: Path) -> dict[StageName, tuple[str, ...]]:
    checkpoint = MediaCheckpointStore(root).load()
    return {
        stage: tuple(output.sha256 for output in receipt.outputs)
        for stage in StageName
        if (receipt := checkpoint.receipt(stage)) is not None
    }


def test_resumes_only_frames_after_frame_extraction_failure(tmp_path: Path) -> None:
    """A failed frame stage preserves and reuses every earlier success."""
    # Given
    fake = FakeMediaAdapters()
    fake.fail_frames = True

    # When
    with pytest.raises(FrameExtractionError, match="failed"):
        _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))

    # Then
    checkpoint = MediaCheckpointStore(tmp_path)
    assert fake.calls == Counter(
        {"video": 1, "subtitles": 1, "audio": 1, "probe": 1, "frames": 1}
    )
    assert checkpoint.validate(SOURCE, CONTRACTS).valid_stages == (
        StageName.VIDEO,
        StageName.SUBTITLES,
        StageName.AUDIO,
        StageName.PROBE,
    )
    assert not (tmp_path / "media/frames").exists()
    assert not tuple(tmp_path.glob(".media-adapter-*"))
    preserved = _hashes(tmp_path)

    # When
    fake.fail_frames = False
    prepared = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))

    # Then
    assert fake.calls == Counter(
        {"video": 1, "subtitles": 1, "audio": 1, "probe": 1, "frames": 2}
    )
    assert len(prepared.frame_paths) == EXPECTED_FRAME_COUNT
    assert {stage: _hashes(tmp_path)[stage] for stage in preserved} == preserved


def test_tampered_audio_reruns_only_audio_and_replay_runs_nothing(
    tmp_path: Path,
) -> None:
    """Tampered audio alone reruns and an exact replay invokes no adapter."""
    # Given
    fake = FakeMediaAdapters()
    first = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))
    before = _hashes(tmp_path)
    _ = first.audio_path.write_bytes(b"tampered")
    calls = fake.calls.copy()

    # When
    resumed = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))

    # Then
    assert fake.calls == calls + Counter({"audio": 1})
    assert all(_hashes(tmp_path)[stage] == before[stage] for stage in StageName)

    # When
    replayed = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))

    # Then
    assert fake.calls == calls + Counter({"audio": 1})
    assert replayed == resumed


def test_source_and_video_changes_follow_fixed_dependencies(tmp_path: Path) -> None:
    """Source and video changes invalidate only their fixed descendants."""
    # Given
    fake = FakeMediaAdapters()
    _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))
    first_calls = fake.calls.copy()

    # When
    _ = prepare_media_with_checkpoint(OTHER_SOURCE, tmp_path, _runtime(fake))

    # Then
    assert fake.calls == first_calls + Counter(
        {"video": 1, "subtitles": 1, "audio": 1, "probe": 1, "frames": 1}
    )
    subtitle_hash = _hashes(tmp_path)[StageName.SUBTITLES]
    _ = (tmp_path / "media/video/source.mp4").write_bytes(b"corrupt")
    calls = fake.calls.copy()

    # When
    _ = prepare_media_with_checkpoint(OTHER_SOURCE, tmp_path, _runtime(fake))

    # Then
    assert fake.calls == calls + Counter(
        {"video": 1, "audio": 1, "probe": 1, "frames": 1}
    )
    assert _hashes(tmp_path)[StageName.SUBTITLES] == subtitle_hash


def test_optional_absence_is_terminal_until_contract_changes(tmp_path: Path) -> None:
    """Optional absence is reused until its independent contract changes."""
    # Given
    fake = FakeMediaAdapters()
    fake.subtitles_available = False

    # When
    with pytest.warns(UserWarning, match="no Korean subtitle"):
        _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))
    with pytest.warns(UserWarning, match="no Korean subtitle"):
        _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))

    # Then
    assert fake.calls["subtitles"] == 1
    receipt = MediaCheckpointStore(tmp_path).load().subtitles
    assert receipt is not None
    assert receipt.status is ReceiptStatus.UNAVAILABLE_OPTIONAL

    # When
    changed = CONTRACTS.model_copy(update={"subtitles": "fixture-subtitles.v2"})
    fake.subtitles_available = True
    _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake, changed))

    # Then
    assert fake.calls["subtitles"] == EXPECTED_SUBTITLE_CALLS_AFTER_CHANGE
    changed_receipt = MediaCheckpointStore(tmp_path).load().subtitles
    assert changed_receipt is not None
    assert changed_receipt.status is ReceiptStatus.SUCCEEDED


def test_default_subtitle_checkpoint_contract_is_policy_bound() -> None:
    """The subtitle checkpoint identity includes policy and inventory versions."""
    policy = default_evidence_policy()
    contract = subtitle_adapter_contract(policy)

    assert DEFAULT_MEDIA_ADAPTER_CONTRACTS.subtitles == contract
    assert policy_digest(policy) in contract
    assert policy.contract_versions.subtitle_inventory in contract


def test_policy_change_reruns_only_subtitles_and_preserves_media_hashes(
    tmp_path: Path,
) -> None:
    """A policy digest change leaves video-derived checkpoint bytes reusable."""
    fake = FakeMediaAdapters()
    policy = default_evidence_policy()
    contracts = DEFAULT_MEDIA_ADAPTER_CONTRACTS.model_copy(
        update={"subtitles": subtitle_adapter_contract(policy)}
    )
    _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake, contracts))
    before = _hashes(tmp_path)
    calls = fake.calls.copy()
    changed_policy = policy.model_copy(
        update={
            "insertion": policy.insertion.model_copy(
                update={"minimum_distinct_pts": 3}
            )
        }
    )
    changed_contracts = contracts.model_copy(
        update={"subtitles": subtitle_adapter_contract(changed_policy)}
    )

    _ = prepare_media_with_checkpoint(
        SOURCE,
        tmp_path,
        _runtime(fake, changed_contracts),
    )

    assert fake.calls == calls + Counter({"subtitles": 1})
    after = _hashes(tmp_path)
    for stage in (
        StageName.VIDEO,
        StageName.AUDIO,
        StageName.PROBE,
        StageName.FRAMES,
    ):
        assert after[stage] == before[stage]


def test_resume_rejects_duplicate_or_spoofed_subtitles_before_publication(
    tmp_path: Path,
) -> None:
    """A derived duplicate cannot enter the subtitle checkpoint authority."""
    class SpoofedSubtitleAdapters(FakeMediaAdapters):
        @override
        def download_subtitles(
            self,
            source_url: YouTubeVideoUrl,
            output_dir: Path,
        ) -> tuple[PreparedSubtitleTrack, ...]:
            del source_url
            self.calls["subtitles"] += 1
            payload = b"WEBVTT\nfixture\n"
            digest = hashlib.sha256(payload).hexdigest()
            original = output_dir / "automatic.ko.ko-orig.vtt"
            duplicate = output_dir / "automatic.ko-en.vtt"
            _ = original.write_bytes(payload)
            _ = duplicate.write_bytes(payload)
            return (
                PreparedSubtitleTrack(
                    path=str(original),
                    language="ko",
                    source=SubtitleSource.AUTOMATIC,
                    track_id="automatic-ko-orig",
                    variant="ko-orig",
                    sha256=digest,
                    eligible_for_speech=True,
                ),
                PreparedSubtitleTrack(
                    path=str(duplicate),
                    language="ko",
                    source=SubtitleSource.AUTOMATIC,
                    track_id="automatic-ko-en",
                    variant="ko-en",
                    sha256=digest,
                    duplicate_of="automatic-ko-orig",
                ),
            )

    fake = SpoofedSubtitleAdapters()

    with pytest.raises(MediaProbeError, match="exact automatic ko-orig"):
        _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))

    checkpoint = MediaCheckpointStore(tmp_path).load()
    assert checkpoint.subtitles is None
    assert not (tmp_path / "media/subtitles").exists()


def test_corrupt_checkpoint_fails_before_adapters_and_partial_is_deleted(
    tmp_path: Path,
) -> None:
    """Corrupt authority fails early and adapter partials never publish."""
    # Given
    fake = FakeMediaAdapters()
    private = tmp_path / ".media-checkpoint"
    private.mkdir(parents=True)
    _ = (private / "checkpoint.json").write_bytes(b"not-json")

    # When / Then
    with pytest.raises(CheckpointStorageError, match="invalid media checkpoint"):
        _ = prepare_media_with_checkpoint(SOURCE, tmp_path, _runtime(fake))
    assert fake.calls == Counter()

    # Given
    clean = tmp_path / "clean"
    fake.fail_audio = True

    # When / Then
    with pytest.raises(AudioExtractionError, match="failed"):
        _ = prepare_media_with_checkpoint(SOURCE, clean, _runtime(fake))
    assert not (clean / "media/audio").exists()
    assert not tuple(clean.glob(".media-adapter-*"))
