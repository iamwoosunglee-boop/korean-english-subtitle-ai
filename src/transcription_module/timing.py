# Copyright (c) 2026
"""Frame-manifest timing derived exclusively from source presentation timestamps."""

from __future__ import annotations

from itertools import pairwise
from typing import Self

from pydantic import Field, model_validator

from transcription_module.models import FrameId, FrozenModel, Milliseconds


class TimingContractError(Exception):
    """A frame manifest cannot support the requested timing operation."""


class FrameManifestEntry(FrozenModel):
    """One extracted frame linked to its source index, PTS, and artifact."""

    frame_id: FrameId
    frame_index: int = Field(ge=0)
    pts_ms: Milliseconds = Field(ge=0)
    path: str = Field(min_length=1)


class FrameManifest(FrozenModel):
    """Ordered VFR-safe lookup from stable frame IDs to actual source PTS."""

    duration_ms: Milliseconds = Field(gt=0)
    frames: tuple[FrameManifestEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def entries_are_unique_and_source_ordered(self) -> Self:
        """Reject ambiguous IDs and manifests that reverse source order."""
        frame_ids = tuple(frame.frame_id for frame in self.frames)
        if len(set(frame_ids)) != len(frame_ids):
            msg = "frame IDs must be unique"
            raise ValueError(msg)
        indices = tuple(frame.frame_index for frame in self.frames)
        if any(current >= following for current, following in pairwise(indices)):
            msg = "frame indices must be strictly increasing"
            raise ValueError(msg)
        timestamps = tuple(frame.pts_ms for frame in self.frames)
        if any(current >= following for current, following in pairwise(timestamps)):
            msg = "frame PTS values must be strictly increasing"
            raise ValueError(msg)
        if timestamps[-1] >= self.duration_ms:
            msg = "frame PTS values must be before duration_ms"
            raise ValueError(msg)
        return self

    def entry(self, frame_id: FrameId) -> FrameManifestEntry:
        """Resolve one stable ID without deriving time from its index."""
        for frame in self.frames:
            if frame.frame_id == frame_id:
                return frame
        msg = f"frame ID is absent from manifest: {frame_id}"
        raise TimingContractError(msg)


class SubtitleInterval(FrozenModel):
    """Half-open interval backed by visible and non-visible frame evidence."""

    start_tc_ms: Milliseconds = Field(ge=0)
    end_tc_ms: Milliseconds = Field(gt=0)
    first_visible_frame_id: FrameId
    first_not_visible_frame_id: FrameId | None

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        """Reject empty or reversed subtitle intervals."""
        if self.end_tc_ms <= self.start_tc_ms:
            msg = "subtitle interval end must follow start"
            raise ValueError(msg)
        return self


def subtitle_interval_from_pts(
    manifest: FrameManifest,
    *,
    first_visible_frame_id: FrameId,
    first_not_visible_frame_id: FrameId | None,
) -> SubtitleInterval:
    """Build ``[first-visible PTS, first-not-visible PTS)`` from actual PTS."""
    visible = manifest.entry(first_visible_frame_id)
    if first_not_visible_frame_id is None:
        end_tc_ms = manifest.duration_ms
    else:
        not_visible = manifest.entry(first_not_visible_frame_id)
        if not_visible.frame_index <= visible.frame_index:
            msg = "first-not-visible frame must follow first-visible frame"
            raise TimingContractError(msg)
        end_tc_ms = not_visible.pts_ms
    return SubtitleInterval(
        start_tc_ms=visible.pts_ms,
        end_tc_ms=end_tc_ms,
        first_visible_frame_id=first_visible_frame_id,
        first_not_visible_frame_id=first_not_visible_frame_id,
    )
