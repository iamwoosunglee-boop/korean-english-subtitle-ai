# Copyright 2026 Transcription Module contributors

"""Receipt construction, replay, and reuse rules for media checkpoints."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from transcription_module._media_checkpoint_fs import locked_checkpoint_store
from transcription_module._media_checkpoint_recovery import recover_checkpoint
from transcription_module.media_checkpoint_models import (
    FRAME_SAMPLING_PROFILE,
    CheckpointPublicationError,
    CheckpointStorageError,
    CheckpointValidation,
    MediaAdapterContracts,
    MediaCheckpoint,
    OutputArtifact,
    ReceiptStatus,
    Sha256Digest,
    StageCompletion,
    StageName,
    StageReceipt,
    checkpoint_fingerprint,
    tree_digest,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from transcription_module._media_checkpoint_fs import CheckpointFiles
    from transcription_module._media_checkpoint_io import StageFile
    from transcription_module.models import YouTubeVideoUrl

_INVALID_CHECKPOINT: Final = "invalid media checkpoint"


@dataclass(frozen=True, slots=True)
class Publication:
    """One typed public stage request."""

    stage: StageName
    completion: StageCompletion
    source_url: YouTubeVideoUrl | None = None


@contextmanager
def recovering_entry(root: Path) -> Generator[CheckpointFiles]:
    """Lock one fixed store and recover before yielding its descriptors."""
    with locked_checkpoint_store(root) as files:
        recover_checkpoint(files)
        files.assert_bound()
        yield files
        files.assert_bound()


def load_checkpoint(files: CheckpointFiles) -> tuple[MediaCheckpoint, bytes | None]:
    payload = files.read_checkpoint()
    if payload is None:
        return MediaCheckpoint(), None
    try:
        return MediaCheckpoint.model_validate_json(payload), payload
    except ValidationError as error:
        raise CheckpointStorageError(_INVALID_CHECKPOINT) from error


def publication_fingerprint(
    files: CheckpointFiles,
    checkpoint: MediaCheckpoint,
    publication: Publication,
) -> Sha256Digest:
    contract = publication.completion.adapter_contract_version
    source_fingerprints = {
        StageName.VIDEO: publication.source_url,
        StageName.SUBTITLES: publication.source_url,
    }
    if publication.stage in source_fingerprints:
        source_url = source_fingerprints[publication.stage]
        if source_url is None:
            raise CheckpointPublicationError(
                publication.stage,
                "source URL is required",
            )
        return checkpoint_fingerprint((str(source_url), contract))
    video_digest = verified_video_digest(files, checkpoint)
    return {
        StageName.AUDIO: checkpoint_fingerprint((video_digest, contract)),
        StageName.PROBE: checkpoint_fingerprint((video_digest, contract)),
        StageName.FRAMES: checkpoint_fingerprint(
            (video_digest, contract, FRAME_SAMPLING_PROFILE)
        ),
    }[publication.stage]


def verified_video_digest(
    files: CheckpointFiles,
    checkpoint: MediaCheckpoint,
) -> Sha256Digest:
    receipt = checkpoint.video
    if receipt is None or not receipt_outputs_match(files, receipt):
        raise CheckpointPublicationError(
            StageName.VIDEO,
            "verified video digest is required",
        )
    return receipt.outputs[0].sha256


def replay_publication(
    files: CheckpointFiles,
    publication: Publication,
    fingerprint: Sha256Digest,
    receipt: StageReceipt,
) -> StageReceipt:
    completion = publication.completion
    exact = (
        receipt.status is ReceiptStatus.SUCCEEDED
        and receipt.input_fingerprint == fingerprint
        and receipt.adapter_contract_version == completion.adapter_contract_version
        and receipt.completed_at == completion.completed_at
        and receipt_tree_digest(receipt) == completion.expected_tree_digest
        and files.scan_staging(publication.stage) is None
        and receipt_outputs_match(files, receipt)
    )
    if exact:
        return receipt
    raise CheckpointPublicationError(
        publication.stage,
        "stage already published with different receipt or bytes",
    )


def validate_checkpoint(
    files: CheckpointFiles,
    source_url: YouTubeVideoUrl,
    contracts: MediaAdapterContracts,
) -> CheckpointValidation:
    """Verify fingerprints and every persisted output byte."""
    checkpoint, _ = load_checkpoint(files)
    valid: dict[StageName, bool] = {
        StageName.VIDEO: receipt_valid(
            files,
            checkpoint.video,
            checkpoint_fingerprint((str(source_url), contracts.downloader)),
        ),
        StageName.SUBTITLES: receipt_valid(
            files,
            checkpoint.subtitles,
            checkpoint_fingerprint((str(source_url), contracts.subtitles)),
        ),
    }
    video_digest = (
        checkpoint.video.outputs[0].sha256
        if valid[StageName.VIDEO] and checkpoint.video is not None
        else None
    )
    for stage, contract, fixed_parts in (
        (StageName.AUDIO, contracts.audio, ()),
        (StageName.PROBE, contracts.probe, ()),
        (StageName.FRAMES, contracts.frames, (FRAME_SAMPLING_PROFILE,)),
    ):
        parts = (video_digest or "", contract, *fixed_parts)
        valid[stage] = video_digest is not None and receipt_valid(
            files,
            checkpoint.receipt(stage),
            checkpoint_fingerprint(parts),
        )
    return CheckpointValidation(
        valid_stages=tuple(stage for stage in StageName if valid[stage]),
        invalid_stages=tuple(stage for stage in StageName if not valid[stage]),
    )



def receipt_valid(
    files: CheckpointFiles,
    receipt: StageReceipt | None,
    fingerprint: Sha256Digest,
) -> bool:
    return (
        receipt is not None
        and receipt.input_fingerprint == fingerprint
        and receipt_outputs_match(files, receipt)
    )


def receipt_outputs_match(files: CheckpointFiles, receipt: StageReceipt) -> bool:
    observed = files.scan_published(receipt.stage)
    if receipt.status is ReceiptStatus.UNAVAILABLE_OPTIONAL:
        return observed is None and not receipt.outputs
    if observed is None:
        return False
    manifest = tuple((item.name, item.sha256) for item in observed)
    return receipt_outputs(receipt.stage, manifest) == receipt.outputs


def validated_manifest(
    stage: StageName,
    observed: tuple[StageFile, ...] | None,
) -> tuple[tuple[str, Sha256Digest], ...]:
    if not observed:
        raise CheckpointPublicationError(stage, "staged output is missing or empty")
    manifest = tuple((item.name, item.sha256) for item in observed)
    names = tuple(name for name, _ in manifest)
    valid = {
        StageName.VIDEO: names == ("source.mp4",),
        StageName.SUBTITLES: (
            bool(names) and all(name.endswith(".vtt") for name in names)
        ),
        StageName.AUDIO: names == ("audio.wav",),
        StageName.PROBE: names == ("probe.json",),
        StageName.FRAMES: names
        == tuple(f"f{index:06d}.jpg" for index in range(1, len(names) + 1)),
    }[stage]
    if not valid:
        raise CheckpointPublicationError(stage, "invalid fixed stage output layout")
    return manifest


def receipt_outputs(
    stage: StageName,
    manifest: tuple[tuple[str, Sha256Digest], ...],
) -> tuple[OutputArtifact, ...]:
    return tuple(
        OutputArtifact(path=f"media/{stage.value}/{name}", sha256=digest)
        for name, digest in manifest
    )


def receipt_tree_digest(receipt: StageReceipt) -> Sha256Digest:
    prefix = f"media/{receipt.stage.value}/"
    manifest = tuple(
        (item.path.removeprefix(prefix), item.sha256) for item in receipt.outputs
    )
    return tree_digest(manifest)
