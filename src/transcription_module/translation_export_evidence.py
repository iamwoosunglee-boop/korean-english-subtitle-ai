# Copyright 2026 Transcription Module contributors

"""Context-observation and frame-evidence export internals."""

import hashlib
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast

from pydantic import Field, model_validator

from transcription_module.media import PreparedFrameManifest, PreparedMediaManifest
from transcription_module.models import FrozenModel, RunId, YouTubeVideoUrl
from transcription_module.storage import RunStore
from translation_module.source import EvidenceEntry, EvidenceKind

if TYPE_CHECKING:
    from translation_module.common import EvidenceId, Milliseconds


class TranslationExportError(Exception):
    """A transcription run cannot be published as a translation source."""


class ObservedPerson(FrozenModel):
    """One person identified by semantic context observations."""

    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    appearance: str = Field(min_length=1)


class ContextObservations(FrozenModel):
    """Strict provenance and source intervals from episode observation."""

    run_id: RunId
    source_url: YouTubeVideoUrl
    source_intervals_ms: tuple[tuple[int, int], ...] = ()
    source_frame_ids: tuple[str, ...] = ()
    episode: str | None = None
    structure: str | None = None
    identity_rule: str | None = None
    people: tuple[ObservedPerson, ...] = ()

    @model_validator(mode="after")
    def intervals_are_ordered(self) -> Self:
        """Require non-empty, forward source-time intervals."""
        if any(start < 0 or end <= start for start, end in self.source_intervals_ms):
            message = "context observation intervals must be forward and non-negative"
            raise ValueError(message)
        return self


def load_context_observations(
    path: Path,
    run_id: RunId,
    source_url: YouTubeVideoUrl,
    duration_ms: int,
) -> ContextObservations | None:
    """Load optional typed observations and validate their run provenance."""
    if not path.exists():
        return None
    observations = ContextObservations.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if observations.run_id != run_id or observations.source_url != source_url:
        message = "context observation provenance does not match the run"
        raise TranslationExportError(message)
    if any(end > duration_ms for _, end in observations.source_intervals_ms):
        message = "context observation interval exceeds source duration"
        raise TranslationExportError(message)
    return observations


def context_observations(
    path: Path,
    run_id: RunId,
    source_url: YouTubeVideoUrl,
    duration_ms: int,
) -> tuple[tuple[tuple[int, int], ...], tuple[str, ...]]:
    """Keep the old optional exporter observation interface unchanged."""
    observations = load_context_observations(path, run_id, source_url, duration_ms)
    if observations is None:
        return (), ()
    return observations.source_intervals_ms, observations.source_frame_ids


def evidence_entries(
    store: RunStore,
    run_id: RunId,
    duration_ms: int,
    included_frame_ids: list[str],
    observations: tuple[
        tuple[str, ...],
        tuple[tuple[int, int], ...],
    ],
) -> tuple[tuple[EvidenceEntry, ...], tuple[str, ...]]:
    """Build verified cue and context frame evidence in stable order."""
    observed_frame_ids, context_intervals = observations
    if not included_frame_ids and not observed_frame_ids and not context_intervals:
        return (), ()
    paths = store.paths(run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    state = store.load_state(run_id)
    if (
        media.run_id != run_id
        or media.source_url != state.source_url
        or media.duration_ms != duration_ms
        or media.frame_count != len(media.frames)
    ):
        message = "current media manifest does not match the run"
        raise TranslationExportError(message)
    frames = {str(frame.frame_id): frame for frame in media.frames}
    if len(frames) != len(media.frames):
        message = "media frame IDs must be unique"
        raise TranslationExportError(message)
    context_frame_ids = tuple(
        dict.fromkeys(
            (
                *observed_frame_ids,
                *(
                    str(frame.frame_id)
                    for frame in media.frames
                    if any(
                        start <= frame.pts_ms < end for start, end in context_intervals
                    )
                ),
            )
        )
    )
    all_frame_ids = tuple(dict.fromkeys((*included_frame_ids, *context_frame_ids)))
    entries = tuple(
        _evidence_entry(paths.run_dir.resolve(), frames.get(frame_id), frame_id)
        for frame_id in all_frame_ids
    )
    return entries, context_frame_ids


def _evidence_entry(
    run_dir: Path,
    frame: PreparedFrameManifest | None,
    frame_id: str,
) -> EvidenceEntry:
    if frame is None:
        message = f"current media frame is missing: {frame_id}"
        raise TranslationExportError(message)
    locator = Path(frame.path)
    if locator.is_absolute() or ".." in locator.parts:
        message = f"evidence locator must be relative: {frame_id}"
        raise TranslationExportError(message)
    resource = (run_dir / locator).resolve()
    try:
        _ = resource.relative_to(run_dir)
        contents = resource.read_bytes()
    except (OSError, ValueError) as error:
        message = f"evidence resource is unavailable: {frame_id}"
        raise TranslationExportError(message) from error
    media_type = mimetypes.guess_type(locator.name)[0]
    if media_type is None or not media_type.startswith("image/"):
        message = f"frame media type is unsupported: {frame_id}"
        raise TranslationExportError(message)
    return EvidenceEntry(
        evidence_id=cast("EvidenceId", frame_id),
        kind=EvidenceKind.FRAME,
        media_type=media_type,
        resource_digest=hashlib.sha256(contents).hexdigest(),
        locator=locator.as_posix(),
        pts_ms=cast("Milliseconds", frame.pts_ms),
    )
