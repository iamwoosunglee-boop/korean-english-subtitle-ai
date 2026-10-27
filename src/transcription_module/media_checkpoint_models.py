# Copyright 2026 Transcription Module contributors

"""Strict contracts for five-stage media checkpoint receipts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Final, Literal, override

from pydantic import (
    AwareDatetime,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from transcription_module.models import FrozenModel, YouTubeVideoUrl

FRAME_SAMPLING_PROFILE: Final = "source-time-buckets-500ms.v1"
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_OUTPUT_PATH_CODE: Final = "output_path"
_STAGE_OUTPUT_CODE: Final = "stage_outputs"
_RECEIPT_STAGE_CODE: Final = "receipt_stage"


class StageName(StrEnum):
    """The fixed media preparation stages in dependency order."""

    VIDEO = "video"
    SUBTITLES = "subtitles"
    AUDIO = "audio"
    PROBE = "probe"
    FRAMES = "frames"


class ReceiptStatus(StrEnum):
    """Terminal checkpoint outcomes."""

    SUCCEEDED = "succeeded"
    UNAVAILABLE_OPTIONAL = "unavailable_optional"


class OutputArtifact(FrozenModel):
    """One checkpoint-root-relative regular output and exact digest."""

    path: str
    sha256: Sha256Digest

    @field_validator("path")
    @classmethod
    def path_is_lexically_confined(cls, value: str) -> str:
        """Reject absolute, parent, alternate-separator, and empty paths."""
        parsed = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or parsed.is_absolute()
            or ".." in parsed.parts
            or "." in parsed.parts
            or parsed.as_posix() != value
        ):
            raise PydanticCustomError(_OUTPUT_PATH_CODE, "invalid output path")
        return value


class StageReceipt(FrozenModel):
    """One strict frozen terminal stage receipt."""

    stage: StageName
    status: ReceiptStatus
    input_fingerprint: Sha256Digest
    outputs: tuple[OutputArtifact, ...]
    adapter_contract_version: NonBlankText
    completed_at: AwareDatetime
    sampling_profile: Literal["source-time-buckets-500ms.v1"] | None = None

    @model_validator(mode="after")
    def output_contract_matches_stage(self) -> StageReceipt:
        """Bind status, paths, and sampling profile to the fixed stage."""
        if len(self.outputs) != len({item.path for item in self.outputs}):
            raise PydanticCustomError(_STAGE_OUTPUT_CODE, "duplicate output path")
        paths = tuple(item.path for item in self.outputs)
        subtitle_valid = (
            self.status is ReceiptStatus.SUCCEEDED
            and bool(paths)
            and all(
                PurePosixPath(path).parent == PurePosixPath("media/subtitles")
                and path.endswith(".vtt")
                for path in paths
            )
            and self.sampling_profile is None
        ) or (
            self.status is ReceiptStatus.UNAVAILABLE_OPTIONAL
            and not paths
            and self.sampling_profile is None
        )
        expected_frames = tuple(
            f"media/frames/f{index:06d}.jpg" for index in range(1, len(paths) + 1)
        )
        valid = {
            StageName.VIDEO: (
                self.status is ReceiptStatus.SUCCEEDED
                and paths == ("media/video/source.mp4",)
                and self.sampling_profile is None
            ),
            StageName.SUBTITLES: subtitle_valid,
            StageName.AUDIO: (
                self.status is ReceiptStatus.SUCCEEDED
                and paths == ("media/audio/audio.wav",)
                and self.sampling_profile is None
            ),
            StageName.PROBE: (
                self.status is ReceiptStatus.SUCCEEDED
                and paths == ("media/probe/probe.json",)
                and self.sampling_profile is None
            ),
            StageName.FRAMES: (
                self.status is ReceiptStatus.SUCCEEDED
                and bool(paths)
                and paths == expected_frames
                and self.sampling_profile == FRAME_SAMPLING_PROFILE
            ),
        }[self.stage]
        if not valid:
            raise PydanticCustomError(
                _STAGE_OUTPUT_CODE,
                "status or outputs do not match fixed stage layout",
            )
        return self


class MediaCheckpoint(FrozenModel):
    """Independent frozen receipts for the five concrete stages."""

    schema_version: Literal["media-checkpoint.v1"] = "media-checkpoint.v1"
    video: StageReceipt | None = None
    subtitles: StageReceipt | None = None
    audio: StageReceipt | None = None
    probe: StageReceipt | None = None
    frames: StageReceipt | None = None

    def receipt(self, stage: StageName) -> StageReceipt | None:
        """Return the receipt occupying one typed stage field."""
        return {
            StageName.VIDEO: self.video,
            StageName.SUBTITLES: self.subtitles,
            StageName.AUDIO: self.audio,
            StageName.PROBE: self.probe,
            StageName.FRAMES: self.frames,
        }[stage]

    def with_receipt(self, receipt: StageReceipt) -> MediaCheckpoint:
        """Return a checkpoint with exactly one typed stage replaced."""
        return self.model_copy(update={receipt.stage.value: receipt})

    @model_validator(mode="after")
    def fields_match_receipt_stages(self) -> MediaCheckpoint:
        """Reject a receipt persisted in another stage's field."""
        for stage in StageName:
            receipt = self.receipt(stage)
            if receipt is not None and receipt.stage is not stage:
                raise PydanticCustomError(
                    _RECEIPT_STAGE_CODE,
                    "receipt occupies the wrong stage field",
                )
        return self


class MediaAdapterContracts(FrozenModel):
    """Adapter contract versions used when evaluating reuse."""

    downloader: NonBlankText
    subtitles: NonBlankText
    audio: NonBlankText
    probe: NonBlankText
    frames: NonBlankText


class StageCompletion(FrozenModel):
    """Injected adapter identity, time, and expected staged tree digest."""

    expected_tree_digest: Sha256Digest
    adapter_contract_version: NonBlankText
    completed_at: AwareDatetime


class SubtitleUnavailable(FrozenModel):
    """Terminal optional-subtitle completion without output bytes."""

    source_url: YouTubeVideoUrl
    adapter_contract_version: NonBlankText
    completed_at: AwareDatetime


class CheckpointValidation(FrozenModel):
    """Reusable and invalid stage sets in dependency order."""

    valid_stages: tuple[StageName, ...]
    invalid_stages: tuple[StageName, ...]


class CheckpointPublicationError(Exception):
    """A requested stage publication conflicts or lacks trusted bytes."""

    stage: StageName
    reason: str

    def __init__(self, stage: StageName, reason: str) -> None:
        """Retain the typed conflicting stage and stable reason."""
        super().__init__(stage, reason)
        self.stage = stage
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"{self.stage.value}: {self.reason}"


class CheckpointRecoveryError(Exception):
    """A pending publication cannot yet converge safely."""

    reason: str

    def __init__(self, reason: str) -> None:
        """Retain the stable recovery reason."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


class CheckpointStorageError(Exception):
    """A fixed checkpoint path is unsafe or has an invalid file type."""

    reason: str

    def __init__(self, reason: str) -> None:
        """Retain the stable unsafe-storage reason."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


def checkpoint_payload(checkpoint: MediaCheckpoint) -> bytes:
    """Serialize one checkpoint in its sole durable representation."""
    return f"{checkpoint.model_dump_json(indent=2)}\n".encode()


def sha256_bytes(payload: bytes) -> Sha256Digest:
    """Return the lowercase SHA-256 digest of exact bytes."""
    return hashlib.sha256(payload).hexdigest()


def checkpoint_fingerprint(parts: tuple[str, ...]) -> Sha256Digest:
    """Digest one ordered stage input identity deterministically."""
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256_bytes(payload)


def tree_digest(files: tuple[tuple[str, Sha256Digest], ...]) -> Sha256Digest:
    """Digest an ordered direct-file stage manifest."""
    payload = json.dumps(files, separators=(",", ":")).encode()
    return sha256_bytes(payload)
