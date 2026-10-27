# Copyright 2026 Transcription Module contributors

"""Public five-stage media checkpoint store."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from transcription_module._media_checkpoint_io import scan_directory
from transcription_module._media_checkpoint_publication import (
    PublicationPlan,
    publish_checkpoint,
)
from transcription_module._media_checkpoint_staging import (
    StageDirectoryWriter,
    StageWriterClosedError,
)
from transcription_module._media_checkpoint_validation import (
    Publication,
    load_checkpoint,
    publication_fingerprint,
    receipt_outputs,
    recovering_entry,
    replay_publication,
    validate_checkpoint,
    validated_manifest,
)
from transcription_module.media_checkpoint_models import (
    FRAME_SAMPLING_PROFILE,
    CheckpointPublicationError,
    CheckpointRecoveryError,
    CheckpointStorageError,
    CheckpointValidation,
    MediaAdapterContracts,
    MediaCheckpoint,
    OutputArtifact,
    ReceiptStatus,
    StageCompletion,
    StageName,
    StageReceipt,
    SubtitleUnavailable,
    checkpoint_fingerprint,
    checkpoint_payload,
    tree_digest,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from contextlib import AbstractContextManager
    from datetime import datetime
    from pathlib import Path
    from typing import BinaryIO

    from transcription_module._media_checkpoint_fs import CheckpointFiles
    from transcription_module.models import YouTubeVideoUrl

_DIRECTORY_STAGE: Final = "stage requires a descriptor-backed directory writer"
_SINGLE_FILE_NAMES: Final = {
    StageName.VIDEO: "source.mp4",
    StageName.AUDIO: "audio.wav",
    StageName.PROBE: "probe.json",
}
_SAMPLING_PROFILES: Final[
    dict[StageName, Literal["source-time-buckets-500ms.v1"] | None]
] = {
    StageName.VIDEO: None,
    StageName.SUBTITLES: None,
    StageName.AUDIO: None,
    StageName.PROBE: None,
    StageName.FRAMES: FRAME_SAMPLING_PROFILE,
}


@dataclass(frozen=True, slots=True)
class MediaCheckpointStore:
    """Publish and validate one checkpoint beneath an existing root."""

    root: Path

    @contextmanager
    def open_stage_file(
        self,
        stage: StageName,
    ) -> Generator[BinaryIO]:
        """Yield an already-open writer for one fixed single-file stage."""
        name = _SINGLE_FILE_NAMES.get(stage)
        if name is None:
            raise CheckpointStorageError(_DIRECTORY_STAGE)
        with (
            self.open_stage_directory(stage) as directory,
            directory.open_file(name) as output,
        ):
            yield output

    @contextmanager
    def open_stage_directory(
        self,
        stage: StageName,
    ) -> Generator[StageDirectoryWriter]:
        """Yield a writer confined to one held staging-directory inode."""
        with (
            self._entry() as files,
            files.open_staging(stage, create=True) as descriptor,
        ):
            writer = StageDirectoryWriter(descriptor)
            try:
                yield writer
            finally:
                writer.close()

    def inspect_staged(
        self,
        stage: StageName,
        adapter_contract_version: str,
        completed_at: datetime,
    ) -> StageCompletion:
        """Bind injected metadata to the exact fixed staged tree digest."""
        with (
            self._entry() as files,
            files.open_staging(stage, create=False) as descriptor,
        ):
            manifest = validated_manifest(stage, scan_directory(descriptor))
        return StageCompletion(
            expected_tree_digest=tree_digest(manifest),
            adapter_contract_version=adapter_contract_version,
            completed_at=completed_at,
        )

    def load(self) -> MediaCheckpoint:
        """Recover first, then parse the strict persisted checkpoint."""
        with self._entry() as files:
            return load_checkpoint(files)[0]

    def publish_video(
        self,
        source_url: YouTubeVideoUrl,
        completion: StageCompletion,
    ) -> StageReceipt:
        """Publish source-keyed video bytes."""
        return self._publish(Publication(StageName.VIDEO, completion, source_url))

    def publish_subtitles(
        self,
        source_url: YouTubeVideoUrl,
        completion: StageCompletion,
    ) -> StageReceipt:
        """Publish independently source-keyed optional subtitle bytes."""
        return self._publish(Publication(StageName.SUBTITLES, completion, source_url))

    def publish_audio(self, completion: StageCompletion) -> StageReceipt:
        """Publish audio bound to verified video bytes."""
        return self._publish(Publication(StageName.AUDIO, completion))

    def publish_probe(self, completion: StageCompletion) -> StageReceipt:
        """Publish probe data bound to verified video bytes."""
        return self._publish(Publication(StageName.PROBE, completion))

    def publish_frames(self, completion: StageCompletion) -> StageReceipt:
        """Publish fixed-profile frames bound to verified video bytes."""
        return self._publish(Publication(StageName.FRAMES, completion))

    def mark_subtitles_unavailable(
        self,
        completion: SubtitleUnavailable,
    ) -> StageReceipt:
        """Persist terminal optional absence with committed-success replay."""
        receipt = StageReceipt(
            stage=StageName.SUBTITLES,
            status=ReceiptStatus.UNAVAILABLE_OPTIONAL,
            input_fingerprint=checkpoint_fingerprint(
                (str(completion.source_url), completion.adapter_contract_version)
            ),
            outputs=(),
            adapter_contract_version=completion.adapter_contract_version,
            completed_at=completion.completed_at,
        )
        with self._entry() as files:
            checkpoint, _ = load_checkpoint(files)
            existing = checkpoint.subtitles
            if existing is not None:
                if (
                    existing == receipt
                    and files.scan_published(StageName.SUBTITLES) is None
                ):
                    return existing
                raise CheckpointPublicationError(
                    StageName.SUBTITLES,
                    "stage already published with different receipt",
                )
            files.write_checkpoint(checkpoint_payload(checkpoint.with_receipt(receipt)))
        return receipt

    def validate(
        self,
        source_url: YouTubeVideoUrl,
        contracts: MediaAdapterContracts,
    ) -> CheckpointValidation:
        """Verify fingerprints and every persisted output byte."""
        with self._entry() as files:
            return validate_checkpoint(files, source_url, contracts)


    def _publish(self, publication: Publication) -> StageReceipt:
        with self._entry() as files:
            checkpoint, prior_bytes = load_checkpoint(files)
            fingerprint = publication_fingerprint(files, checkpoint, publication)
            existing = checkpoint.receipt(publication.stage)
            if existing is not None:
                return replay_publication(files, publication, fingerprint, existing)
            with files.open_staging(
                publication.stage, create=False, stable=False
            ) as source_fd:
                manifest = validated_manifest(
                    publication.stage,
                    scan_directory(source_fd),
                )
                observed_digest = tree_digest(manifest)
                if observed_digest != publication.completion.expected_tree_digest:
                    raise CheckpointPublicationError(
                        publication.stage,
                        "staged output digest differs from requested publication",
                    )
                receipt = StageReceipt(
                    stage=publication.stage,
                    status=ReceiptStatus.SUCCEEDED,
                    input_fingerprint=fingerprint,
                    outputs=receipt_outputs(publication.stage, manifest),
                    adapter_contract_version=(
                        publication.completion.adapter_contract_version
                    ),
                    completed_at=publication.completion.completed_at,
                    sampling_profile=_SAMPLING_PROFILES[publication.stage],
                )
                publish_checkpoint(
                    files,
                    PublicationPlan(
                        stage=publication.stage,
                        expected_tree_digest=observed_digest,
                        checkpoint=checkpoint.with_receipt(receipt),
                        prior_checkpoint=prior_bytes,
                    ),
                    source_fd,
                )
                return receipt

    def _entry(self) -> AbstractContextManager[CheckpointFiles]:
        """Return the locked fixed-layout boundary used by every operation."""
        return recovering_entry(self.root)


__all__ = [
    "FRAME_SAMPLING_PROFILE",
    "CheckpointPublicationError",
    "CheckpointRecoveryError",
    "CheckpointStorageError",
    "CheckpointValidation",
    "MediaAdapterContracts",
    "MediaCheckpoint",
    "MediaCheckpointStore",
    "OutputArtifact",
    "ReceiptStatus",
    "StageCompletion",
    "StageName",
    "StageReceipt",
    "StageWriterClosedError",
    "SubtitleUnavailable",
]
