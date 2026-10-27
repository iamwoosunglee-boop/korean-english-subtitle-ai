# Copyright 2026 Transcription Module contributors

"""Concrete failed-stage-only media preparation orchestration."""

from __future__ import annotations

import os
import shutil
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final

from transcription_module._media_checkpoint_validation import (
    load_checkpoint,
    recovering_entry,
    validate_checkpoint,
)
from transcription_module.media import (
    MediaProbe,
    MediaProbeError,
    OptionalSubtitleWarning,
    PreparedMedia,
    PreparedSubtitleTrack,
    SubtitleDownloadError,
    SubtitleSource,
    sample_media_probe,
)
from transcription_module.media_checkpoint import (
    CheckpointValidation,
    MediaAdapterContracts,
    MediaCheckpointStore,
    ReceiptStatus,
    StageName,
    StageReceipt,
    SubtitleUnavailable,
)
from transcription_module.media_checkpoint_models import checkpoint_payload
from transcription_module.media_runtime import CheckpointedMediaAdapters
from transcription_module.media_runtime import stage_completion as _completion
from transcription_module.subtitle_admission import (
    AUTOMATIC_KO_ORIG_FILENAME,
    SubtitleAdmissionError,
    validate_prepared_subtitle_tracks,
)
from transcription_module.visual_evidence_policy import (
    default_evidence_policy,
    subtitle_adapter_contract,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from transcription_module.models import YouTubeVideoUrl

FRAME_SAMPLE_INTERVAL_MS: Final = 500
DEFAULT_MEDIA_ADAPTER_CONTRACTS: Final = MediaAdapterContracts(
    downloader="yt-dlp-video.v1",
    subtitles=subtitle_adapter_contract(default_evidence_policy()),
    audio="ffmpeg-pcm-s16le-16khz-mono.v1",
    probe="ffprobe-frame-pts.v1",
    frames="ffmpeg-source-time-buckets.v1",
)


def prepare_media_with_checkpoint(
    source_url: YouTubeVideoUrl,
    output_dir: Path,
    runtime: CheckpointedMediaAdapters,
) -> PreparedMedia:
    """Validate every stage and invoke only invalid concrete adapters."""
    output_dir.mkdir(parents=True, exist_ok=True)
    store = MediaCheckpointStore(output_dir)
    validation = _discard_invalid(output_dir, source_url, runtime.contracts)
    invalid = set(validation.invalid_stages)

    if StageName.VIDEO in invalid:
        with _scratch(output_dir, StageName.VIDEO) as scratch:
            video = runtime.adapters.youtube.download_video(source_url, Path(scratch))
            _stage_files(store, StageName.VIDEO, (("source.mp4", video),))
        _ = store.publish_video(
            source_url,
            _completion(store, StageName.VIDEO, runtime),
        )

    video_path = output_dir / "media/video/source.mp4"
    if StageName.SUBTITLES in invalid:
        with _scratch(output_dir, StageName.SUBTITLES) as scratch:
            try:
                tracks = runtime.adapters.youtube.download_subtitles(
                    source_url, Path(scratch)
                )
            except SubtitleDownloadError:
                tracks = ()
            try:
                tracks = validate_prepared_subtitle_tracks(tracks)
            except SubtitleAdmissionError as error:
                raise MediaProbeError(
                    argv=("transcription-cli", "prepare"),
                    stderr=str(error),
                ) from error
            if tracks:
                _stage_files(
                    store,
                    StageName.SUBTITLES,
                    tuple(
                        (Path(track.path).name, Path(track.path)) for track in tracks
                    ),
                )
                _ = store.publish_subtitles(
                    source_url,
                    _completion(store, StageName.SUBTITLES, runtime),
                )
            else:
                _ = store.mark_subtitles_unavailable(
                    SubtitleUnavailable(
                        source_url=source_url,
                        adapter_contract_version=runtime.contracts.subtitles,
                        completed_at=runtime.completed_at,
                    )
                )

    if StageName.AUDIO in invalid:
        with _scratch(output_dir, StageName.AUDIO) as scratch:
            audio = runtime.adapters.ffmpeg.extract_audio(
                video_path, Path(scratch) / "audio.wav"
            )
            _stage_files(store, StageName.AUDIO, (("audio.wav", audio),))
        _ = store.publish_audio(_completion(store, StageName.AUDIO, runtime))

    if StageName.PROBE in invalid:
        probe = runtime.adapters.ffprobe.probe(video_path)
        with store.open_stage_file(StageName.PROBE) as output:
            _ = output.write(f"{probe.model_dump_json(indent=2)}\n".encode())
        _ = store.publish_probe(_completion(store, StageName.PROBE, runtime))

    if StageName.FRAMES in invalid:
        with _scratch(output_dir, StageName.FRAMES) as scratch:
            frames = runtime.adapters.ffmpeg.extract_frames(
                video_path, Path(scratch) / "frames", FRAME_SAMPLE_INTERVAL_MS
            )
            _stage_files(
                store,
                StageName.FRAMES,
                tuple(
                    (f"f{index:06d}.jpg", frame)
                    for index, frame in enumerate(frames, start=1)
                ),
            )
        _ = store.publish_frames(_completion(store, StageName.FRAMES, runtime))

    validation = store.validate(source_url, runtime.contracts)
    _require_complete(validation.invalid_stages)
    return _load_prepared_media(output_dir, store)


def _discard_invalid(
    root: Path,
    source_url: YouTubeVideoUrl,
    contracts: MediaAdapterContracts,
) -> CheckpointValidation:
    with recovering_entry(root) as files:
        validation = validate_checkpoint(files, source_url, contracts)
        checkpoint, _ = load_checkpoint(files)
        for stage in validation.invalid_stages:
            for parent_fd in (files.staging_fd, files.published_fd):
                descriptor = files.open_optional_stage(parent_fd, stage)
                if descriptor is not None:
                    try:
                        files.remove_stage(parent_fd, stage, descriptor)
                    finally:
                        os.close(descriptor)
            checkpoint = checkpoint.model_copy(update={stage.value: None})
        files.write_checkpoint(checkpoint_payload(checkpoint))
        return validation


def _load_prepared_media(
    output_dir: Path,
    store: MediaCheckpointStore,
) -> PreparedMedia:
    checkpoint = store.load()
    probe = MediaProbe.model_validate_json(
        (output_dir / "media/probe/probe.json").read_bytes()
    )
    sampled_probe = sample_media_probe(probe, FRAME_SAMPLE_INTERVAL_MS)
    frame_paths = tuple(
        output_dir / output.path
        for output in _required_receipt(checkpoint.frames).outputs
    )
    if len(frame_paths) != len(sampled_probe.frames):
        raise MediaProbeError(
            argv=("ffprobe", "-show_frames"),
            stderr="saved frame count does not match decoded PTS count",
        )
    subtitle_receipt = _required_receipt(checkpoint.subtitles)
    subtitle_available = {
        ReceiptStatus.SUCCEEDED: True,
        ReceiptStatus.UNAVAILABLE_OPTIONAL: False,
    }[subtitle_receipt.status]
    if not subtitle_available:
        warnings.warn(
            "no Korean subtitle was available",
            OptionalSubtitleWarning,
            stacklevel=2,
        )
    return PreparedMedia(
        video_path=output_dir / "media/video/source.mp4",
        audio_path=output_dir / "media/audio/audio.wav",
        subtitle_tracks=_subtitle_tracks(output_dir, subtitle_receipt),
        probe=sampled_probe,
        frame_paths=frame_paths,
    )


def _stage_files(
    store: MediaCheckpointStore,
    stage: StageName,
    files: Iterable[tuple[str, Path]],
) -> None:
    with store.open_stage_directory(stage) as directory:
        for name, path in files:
            with path.open("rb") as source, directory.open_file(name) as output:
                shutil.copyfileobj(source, output)


def _scratch(root: Path, stage: StageName) -> TemporaryDirectory[str]:
    return TemporaryDirectory(prefix=f".media-adapter-{stage.value}-", dir=root)


def _require_complete(invalid_stages: tuple[StageName, ...]) -> None:
    if invalid_stages:
        raise MediaProbeError(
            argv=("transcription-cli", "prepare"),
            stderr="media checkpoint remained incomplete after preparation",
        )


def _required_receipt(receipt: StageReceipt | None) -> StageReceipt:
    if receipt is None:
        raise MediaProbeError(
            argv=("transcription-cli", "prepare"),
            stderr="validated media checkpoint lacks a stage receipt",
        )
    return receipt


def _subtitle_tracks(
    root: Path,
    receipt: StageReceipt,
) -> tuple[PreparedSubtitleTrack, ...]:
    if not receipt.outputs:
        return ()
    if (
        len(receipt.outputs) != 1
        or Path(receipt.outputs[0].path).name != AUTOMATIC_KO_ORIG_FILENAME
    ):
        raise MediaProbeError(
            argv=("transcription-cli", "prepare"),
            stderr="subtitle checkpoint contains a disallowed variant",
        )
    output = receipt.outputs[0]
    return (
        PreparedSubtitleTrack(
            path=str(root / output.path),
            language="ko",
            source=SubtitleSource.AUTOMATIC,
            track_id="automatic-ko-orig",
            variant="ko-orig",
            sha256=output.sha256,
            eligible_for_speech=True,
        ),
    )
