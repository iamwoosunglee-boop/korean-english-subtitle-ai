# Copyright 2026 Transcription Module contributors

"""Descriptor-backed public staging writers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, final

from transcription_module._media_checkpoint_io import component, create_output
from transcription_module.media_checkpoint_models import CheckpointStorageError

if TYPE_CHECKING:
    from collections.abc import Generator
    from contextlib import AbstractContextManager
    from typing import BinaryIO

_CLOSED_WRITER: Final = "staging directory writer is closed"


class StageWriterClosedError(CheckpointStorageError):
    """A staging writer operation targeted an invalidated generation."""

    def __init__(self) -> None:
        """Initialize the stable typed closed-writer failure."""
        super().__init__(_CLOSED_WRITER)


@final
class StageDirectoryWriter:
    """Own one mutable staging descriptor until explicit invalidation."""

    __slots__: tuple[str, ...] = ("_descriptor", "_generation")

    _descriptor: int | None
    _generation: int

    def __init__(self, descriptor: int) -> None:
        """Duplicate and own the supplied staging directory descriptor."""
        self._descriptor = os.dup(descriptor)
        self._generation = 0

    def close(self) -> None:
        """Invalidate this generation and idempotently close its descriptor."""
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        self._generation += 1
        os.close(descriptor)

    def open_file(self, name: str) -> AbstractContextManager[BinaryIO]:
        """Return a lazy writer bound to the current live generation."""
        generation = self._live_generation()
        component(name)
        return self._opened_output(generation, name)

    @contextmanager
    def _opened_output(
        self,
        generation: int,
        name: str,
    ) -> Generator[BinaryIO]:
        descriptor = create_output(self._descriptor_for(generation), name)
        with os.fdopen(descriptor, "wb") as output:
            yield output
            output.flush()
            os.fsync(output.fileno())
        os.fsync(self._descriptor_for(generation))

    def sync(self) -> None:
        """Durably flush directory entries for the current generation."""
        os.fsync(self._descriptor_for(self._live_generation()))

    def _live_generation(self) -> int:
        if self._descriptor is None:
            raise StageWriterClosedError
        return self._generation

    def _descriptor_for(self, generation: int) -> int:
        descriptor = self._descriptor
        if descriptor is None or generation != self._generation:
            raise StageWriterClosedError
        return descriptor
