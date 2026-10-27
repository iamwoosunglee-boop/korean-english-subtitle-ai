# Copyright 2026 Transcription Module contributors

"""Typed mlx-whisper 0.4.3 Python adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from importlib import import_module
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Final,
    Protocol,
    Self,
    override,
    runtime_checkable,
)

from pydantic import ConfigDict, Field, ValidationError, model_validator

from transcription_module.models import (
    FrozenModel,
    RunId,
)
from transcription_module.visual_evidence_policy import EvidenceEra

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

type JsonValue = (
    str
    | int
    | float
    | bool
    | list[JsonValue]
    | dict[str, JsonValue]
    | None
)

DEFAULT_ASR_MODEL: Final = "mlx-community/whisper-large-v3-turbo"
RETRANSCRIBE_ASR_MODEL: Final = "mlx-community/whisper-large-v3"


@dataclass(frozen=True, slots=True)
class AsrBackendError(Exception):
    """mlx-whisper is unavailable or returned an invalid response."""

    detail: str

    @override
    def __str__(self) -> str:
        """Return the stable adapter diagnostic."""
        return self.detail


class AsrSegment(FrozenModel):
    """One temporary speech segment produced by local ASR."""

    source_ordinal: int | None = Field(default=None, ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speech: str = Field(min_length=1)
    avg_logprob: float | None = None
    no_speech_probability: float | None = Field(default=None, ge=0, le=1)
    words: tuple[AsrWord, ...] = ()


class AsrWord(FrozenModel):
    """One word-level timing and original model probability."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speech: str = Field(min_length=1)
    probability: float | None = Field(default=None, ge=0, le=1)


class AsrTranscript(FrozenModel):
    """Normalized temporary transcript from mlx-whisper."""

    text: str
    language: str
    segments: tuple[AsrSegment, ...]

    @model_validator(mode="after")
    def source_ordinals_are_all_legacy_or_stably_ordered(self) -> Self:
        """Reject mixed or reordered source identities without rewriting them."""
        ordinals = tuple(segment.source_ordinal for segment in self.segments)
        present = tuple(ordinal for ordinal in ordinals if ordinal is not None)
        if present and len(present) != len(ordinals):
            message = "ASR source ordinals must be present on every segment"
            raise ValueError(message)
        if present and (
            len(present) != len(set(present))
            or present != tuple(sorted(present))
        ):
            message = "ASR source ordinals must be unique and increasing"
            raise ValueError(message)
        return self


class AsrRunArtifact(FrozenModel):
    """Persisted local-ASR result with legacy-compatible source binding."""

    run_id: RunId
    model: str = DEFAULT_ASR_MODEL
    era: EvidenceEra = EvidenceEra.LEGACY
    policy_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    audio_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    transcript: AsrTranscript

    @model_validator(mode="after")
    def policy_bound_artifact_has_complete_identity(self) -> Self:
        """Require current policy/audio bindings and stable source ordinals."""
        if self.era is EvidenceEra.LEGACY:
            return self
        if self.policy_digest is None or self.audio_sha256 is None:
            message = "policy-bound ASR requires policy and audio digests"
            raise ValueError(message)
        if any(
            segment.source_ordinal is None for segment in self.transcript.segments
        ):
            message = "policy-bound ASR requires a source ordinal on every segment"
            raise ValueError(message)
        return self


class AsrAdapter(Protocol):
    """Transcribe prepared audio into normalized temporary segments."""

    def transcribe(
        self,
        audio_path: Path,
        model: str = DEFAULT_ASR_MODEL,
    ) -> AsrTranscript:
        """Transcribe one prepared WAV input."""
        ...


@runtime_checkable
class MlxWhisperModule(Protocol):
    """Python call contract documented by mlx-whisper 0.4.3."""

    def transcribe(
        self,
        audio_path: str,
        *,
        path_or_hf_repo: str,
        word_timestamps: bool,
        language: str,
    ) -> Mapping[str, JsonValue]:
        """Return the package's JSON-compatible transcription mapping."""
        ...


class _MlxWord(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        frozen=True,
    )

    start: Decimal
    end: Decimal
    word: str
    probability: float | None = None


class _MlxSegment(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        frozen=True,
    )

    start: Decimal
    end: Decimal
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    words: tuple[_MlxWord, ...] = ()


class _MlxOutput(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        frozen=True,
    )

    text: str
    language: str
    segments: tuple[_MlxSegment, ...]


@dataclass(frozen=True, slots=True)
class MlxWhisperPython:
    """Concrete Python adapter for the pinned optional dependency."""

    module: MlxWhisperModule

    def transcribe(
        self,
        audio_path: Path,
        model: str = DEFAULT_ASR_MODEL,
    ) -> AsrTranscript:
        """Call mlx-whisper and normalize its boundary response."""
        raw = self.module.transcribe(
            str(audio_path),
            path_or_hf_repo=model,
            word_timestamps=True,
            language="ko",
        )
        try:
            output = _MlxOutput.model_validate(raw)
        except ValidationError as error:
            raise AsrBackendError(detail=str(error)) from error
        segments = tuple(
            AsrSegment(
                source_ordinal=source_ordinal,
                start_ms=_seconds_to_milliseconds(segment.start),
                end_ms=_seconds_to_milliseconds(segment.end),
                speech=speech,
                avg_logprob=segment.avg_logprob,
                no_speech_probability=segment.no_speech_prob,
                words=tuple(
                    AsrWord(
                        start_ms=_seconds_to_milliseconds(word.start),
                        end_ms=_seconds_to_milliseconds(word.end),
                        speech=word_speech,
                        probability=word.probability,
                    )
                    for word in segment.words
                    if (word_speech := word.word.strip())
                ),
            )
            for source_ordinal, segment in enumerate(output.segments, start=1)
            if (speech := segment.text.strip())
        )
        return AsrTranscript(
            text=output.text.strip(),
            language=output.language,
            segments=segments,
        )


def load_mlx_whisper() -> MlxWhisperPython:
    """Load the optional mlx-whisper package behind its typed contract."""
    try:
        module = import_module("mlx_whisper")
    except ImportError as error:
        detail = "mlx-whisper 0.4.3 is required for local ASR"
        raise AsrBackendError(detail=detail) from error
    if not isinstance(module, MlxWhisperModule):
        detail = "mlx-whisper does not expose the expected transcribe contract"
        raise AsrBackendError(detail=detail)
    return MlxWhisperPython(module=module)


def _seconds_to_milliseconds(seconds: Decimal) -> int:
    return int(
        (seconds * 1_000).to_integral_value(rounding=ROUND_HALF_UP)
    )
