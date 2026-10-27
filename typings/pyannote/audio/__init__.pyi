# Copyright 2026 Transcription Module contributors

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, Self

class _DiarizationTurn(Protocol):
    start: float
    end: float


class _DiarizationTrack(Protocol):
    ...


class _DiarizationAnnotation(Protocol):
    def itertracks(
        self,
        *,
        yield_label: bool,
    ) -> Iterable[tuple[_DiarizationTurn, _DiarizationTrack, str]]: ...


class _DiarizationOutput(Protocol):
    speaker_diarization: _DiarizationAnnotation


class Pipeline:
    @classmethod
    def from_pretrained(
        cls,
        checkpoint: str | Path,
        revision: str | None = ...,
        hparams_file: str | Path | None = ...,
        subfolder: str | None = ...,
        token: str | bool | None = ...,
        cache_dir: Path | str | None = ...,
    ) -> Self | None: ...

    def __call__(
        self,
        file: str | Path,
        preload: bool = ...,
    ) -> _DiarizationOutput: ...
