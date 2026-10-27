# Copyright 2026 Transcription Module contributors

"""Five-stage media checkpoint behavior and dependency contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from tests.integration.media_checkpoint_support import (
    COMPLETED_AT,
    CONTRACTS,
    OTHER_URL,
    SOURCE_URL,
    publish_required_descendants,
    publish_video,
    stage_files,
)
from transcription_module.media_checkpoint import (
    FRAME_SAMPLING_PROFILE,
    MediaCheckpointStore,
    ReceiptStatus,
    StageName,
    SubtitleUnavailable,
)
from transcription_module.media_checkpoint_models import checkpoint_payload

if TYPE_CHECKING:
    from pathlib import Path

NEW_VIDEO: Final = b"replacement-video"


def test_five_stage_receipts_reload_byte_identically_when_outputs_match(
    tmp_path: Path,
) -> None:
    """Given all stage outputs, publication persists five reusable receipts."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    _ = store.publish_subtitles(
        SOURCE_URL,
        stage_files(
            store,
            StageName.SUBTITLES,
            (("manual.ko.vtt", b"WEBVTT\n"),),
        ),
    )
    publish_required_descendants(store)
    receipt_bytes = (store.root / ".media-checkpoint/checkpoint.json").read_bytes()

    # When
    validation = store.validate(SOURCE_URL, CONTRACTS)

    # Then
    assert validation.valid_stages == tuple(StageName)
    assert validation.invalid_stages == ()
    assert (
        store.root / ".media-checkpoint/checkpoint.json"
    ).read_bytes() == receipt_bytes
    frames = store.load().frames
    assert frames is not None
    assert frames.sampling_profile == FRAME_SAMPLING_PROFILE


def test_optional_subtitle_absence_is_terminal_and_nonblocking(
    tmp_path: Path,
) -> None:
    """Given no subtitle track, its terminal receipt remains reusable."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    publish_required_descendants(store)

    # When
    receipt = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )

    # Then
    assert receipt.status is ReceiptStatus.UNAVAILABLE_OPTIONAL
    assert receipt.outputs == ()
    assert store.validate(SOURCE_URL, CONTRACTS).valid_stages == tuple(StageName)


def test_source_change_invalidates_video_and_subtitles_and_descendants(
    tmp_path: Path,
) -> None:
    """A different source invalidates both independent source-keyed stages."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    _ = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )
    publish_required_descendants(store)

    # When
    validation = store.validate(OTHER_URL, CONTRACTS)

    # Then
    assert validation.valid_stages == ()
    assert validation.invalid_stages == tuple(StageName)


def test_video_tamper_invalidates_video_descendants_but_not_subtitles(
    tmp_path: Path,
) -> None:
    """Changed video bytes never invalidate source-keyed subtitle absence."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    _ = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )
    publish_required_descendants(store)
    _ = ((store.root / "media/video") / "source.mp4").write_bytes(b"tampered")

    # When
    validation = store.validate(SOURCE_URL, CONTRACTS)

    # Then
    assert validation.valid_stages == (StageName.SUBTITLES,)
    assert validation.invalid_stages == (
        StageName.VIDEO,
        StageName.AUDIO,
        StageName.PROBE,
        StageName.FRAMES,
    )


def test_one_tampered_frame_invalidates_only_frames(tmp_path: Path) -> None:
    """A partial frame set cannot be reused while sibling stages remain valid."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    _ = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )
    publish_required_descendants(store)
    _ = ((store.root / "media/frames") / "f000002.jpg").write_bytes(b"tampered")

    # When
    validation = store.validate(SOURCE_URL, CONTRACTS)

    # Then
    assert validation.valid_stages == (
        StageName.VIDEO,
        StageName.SUBTITLES,
        StageName.AUDIO,
        StageName.PROBE,
    )
    assert validation.invalid_stages == (StageName.FRAMES,)


@pytest.mark.parametrize(
    ("field", "replacement", "invalid"),
    [
        (
            "downloader",
            "yt-dlp.v2",
            (
                StageName.VIDEO,
                StageName.AUDIO,
                StageName.PROBE,
                StageName.FRAMES,
            ),
        ),
        ("subtitles", "yt-dlp-subtitles.v2", (StageName.SUBTITLES,)),
        ("audio", "ffmpeg-audio.v2", (StageName.AUDIO,)),
        ("probe", "ffprobe.v2", (StageName.PROBE,)),
        ("frames", "ffmpeg-frames.v2", (StageName.FRAMES,)),
    ],
)
def test_adapter_contract_change_invalidates_only_fixed_dependencies(
    tmp_path: Path,
    field: str,
    replacement: str,
    invalid: tuple[StageName, ...],
) -> None:
    """Each adapter version participates only in its fixed stage fingerprint."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    _ = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )
    publish_required_descendants(store)
    changed = CONTRACTS.model_copy(update={field: replacement})

    # When
    validation = store.validate(SOURCE_URL, changed)

    # Then
    assert validation.invalid_stages == invalid


def test_verified_video_digest_change_invalidates_only_digest_descendants(
    tmp_path: Path,
) -> None:
    """A valid new video receipt leaves source-keyed subtitles reusable."""
    # Given
    store = MediaCheckpointStore(tmp_path / "first")
    store.root.mkdir()
    publish_video(store)
    _ = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )
    publish_required_descendants(store)
    replacement = MediaCheckpointStore(tmp_path / "replacement")
    replacement.root.mkdir()
    publish_video(replacement, NEW_VIDEO)
    replacement_receipt = replacement.load().video
    assert replacement_receipt is not None
    _ = ((store.root / "media/video") / "source.mp4").write_bytes(NEW_VIDEO)
    checkpoint = store.load().model_copy(update={"video": replacement_receipt})
    _ = (store.root / ".media-checkpoint/checkpoint.json").write_bytes(
        checkpoint_payload(checkpoint)
    )

    # When
    validation = store.validate(SOURCE_URL, CONTRACTS)

    # Then
    assert validation.valid_stages == (StageName.VIDEO, StageName.SUBTITLES)
    assert validation.invalid_stages == (
        StageName.AUDIO,
        StageName.PROBE,
        StageName.FRAMES,
    )


def test_missing_frame_invalidates_complete_frame_receipt(tmp_path: Path) -> None:
    """One missing file makes the persisted contiguous frame set unusable."""
    # Given
    store = MediaCheckpointStore(tmp_path)
    publish_video(store)
    _ = store.mark_subtitles_unavailable(
        SubtitleUnavailable(
            source_url=SOURCE_URL,
            adapter_contract_version=CONTRACTS.subtitles,
            completed_at=COMPLETED_AT,
        )
    )
    publish_required_descendants(store)
    ((store.root / "media/frames") / "f000002.jpg").unlink()

    # When
    validation = store.validate(SOURCE_URL, CONTRACTS)

    # Then
    assert validation.invalid_stages == (StageName.FRAMES,)
