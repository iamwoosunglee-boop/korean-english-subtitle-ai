# Copyright 2026 Transcription Module contributors

"""Legacy-compatible subtitle inventory with exact policy-bound admission."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from transcription_module.models import FrozenModel
from transcription_module.visual_evidence_policy import EvidenceEra

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EvidenceAuthorityIdentity(FrozenModel):
    """One exact independently admitted policy-evidence authority file."""

    kind: Literal["speaker_turn_authority"]
    path: Literal["speaker-turn-authority.json"]
    sha256: Sha256Digest


class SubtitleTrackIdentity(FrozenModel):
    """One exact downloaded track, variant, origin, path, and byte identity."""

    track_id: NonBlank
    variant: NonBlank
    source: Literal["automatic", "manual"]
    path: str
    sha256: Sha256Digest
    eligible_for_speech: bool
    duplicate_of: NonBlank | None = None

    @field_validator("path")
    @classmethod
    def path_is_relative_and_confined(cls, value: str) -> str:
        """Reject absolute, parent, alternate-separator, and empty paths."""
        parsed = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or parsed.is_absolute()
            or ".." in parsed.parts
            or parsed.as_posix() != value
        ):
            message = "subtitle inventory path is not lexically confined"
            raise ValueError(message)
        return value


def _validate_unique_tracks(tracks: tuple[SubtitleTrackIdentity, ...]) -> None:
    track_ids = tuple(track.track_id for track in tracks)
    paths = tuple(track.path for track in tracks)
    if len(track_ids) != len(set(track_ids)):
        message = "subtitle track IDs must be unique"
        raise ValueError(message)
    if len(paths) != len(set(paths)):
        message = "subtitle track paths must be unique"
        raise ValueError(message)


def _validate_duplicate_lineage(
    tracks: tuple[SubtitleTrackIdentity, ...],
) -> None:
    by_id = {track.track_id: track for track in tracks}
    for track in tracks:
        if track.duplicate_of is None:
            continue
        original = by_id.get(track.duplicate_of)
        if original is None or original.track_id == track.track_id:
            message = "subtitle duplicate_of must reference another track"
            raise ValueError(message)
        if original.sha256 != track.sha256:
            message = "subtitle duplicate lineage requires equal bytes"
            raise ValueError(message)
        if track.eligible_for_speech:
            message = "duplicate subtitle track cannot be speech-eligible"
            raise ValueError(message)


def _validate_policy_admission(
    policy_sha: str | None,
    eligible: tuple[SubtitleTrackIdentity, ...],
) -> None:
    if policy_sha is None:
        message = "policy-bound subtitle inventory requires policy digest"
        raise ValueError(message)
    if len(eligible) > 1:
        message = "policy-bound inventory admits at most one speech track"
        raise ValueError(message)
    if any(
        track.track_id != "automatic-ko-orig"
        or track.variant != "ko-orig"
        or track.source != "automatic"
        or track.duplicate_of is not None
        for track in eligible
    ):
        message = "only exact automatic-ko-orig track may be speech-eligible"
        raise ValueError(message)


class SubtitleInventory(FrozenModel):
    """Every observed track and exact policy-evidence authority admission."""

    schema_version: Literal["subtitle-inventory.v1"] = "subtitle-inventory.v1"
    era: EvidenceEra
    policy_digest: Sha256Digest | None = None
    tracks: tuple[SubtitleTrackIdentity, ...]
    evidence_authorities: tuple[EvidenceAuthorityIdentity, ...] = ()

    @property
    def eligible_tracks(self) -> tuple[SubtitleTrackIdentity, ...]:
        """Return the tracks that may contribute lexical speech evidence."""
        return tuple(track for track in self.tracks if track.eligible_for_speech)

    @model_validator(mode="after")
    def inventory_identity_and_admission_are_consistent(self) -> Self:
        """Validate duplicate lineage and fail closed for policy-bound runs."""
        _validate_unique_tracks(self.tracks)
        _validate_duplicate_lineage(self.tracks)
        kinds = tuple(item.kind for item in self.evidence_authorities)
        paths = tuple(item.path for item in self.evidence_authorities)
        if len(kinds) != len(set(kinds)) or len(paths) != len(set(paths)):
            message = "evidence authority admissions must be unique"
            raise ValueError(message)
        if self.era is EvidenceEra.POLICY_BOUND:
            _validate_policy_admission(self.policy_digest, self.eligible_tracks)
        return self


def subtitle_inventory_digest(inventory: SubtitleInventory) -> str:
    """Return the canonical SHA-256 identity of one complete inventory."""
    payload = json.dumps(
        inventory.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "EvidenceAuthorityIdentity",
    "SubtitleInventory",
    "SubtitleTrackIdentity",
    "subtitle_inventory_digest",
]
