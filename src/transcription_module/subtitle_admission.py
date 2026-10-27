# Copyright 2026 Transcription Module contributors

"""Exact policy-bound subtitle inventory construction and consumer admission."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Final

from transcription_module.subtitle_inventory import (
    SubtitleInventory,
    SubtitleTrackIdentity,
)
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    VisualEvidencePolicy,
    default_evidence_policy,
    policy_digest,
)

if TYPE_CHECKING:
    from transcription_module.media import (
        PreparedMediaManifest,
        PreparedSubtitleTrack,
    )

AUTOMATIC_KO_ORIG_FILENAME: Final = "automatic.ko.ko-orig.vtt"


class SubtitleAdmissionError(ValueError):
    """Stored subtitle evidence is stale, untrusted, or outside its run."""


def validate_prepared_subtitle_tracks(
    tracks: tuple[PreparedSubtitleTrack, ...],
) -> tuple[PreparedSubtitleTrack, ...]:
    """Require optional absence or one exact automatic ko-orig byte identity."""
    if not tracks:
        return ()
    if len(tracks) != 1:
        message = "subtitle download must contain one exact automatic ko-orig track"
        raise SubtitleAdmissionError(message)
    track = tracks[0]
    path = Path(track.path)
    if (
        path.name != AUTOMATIC_KO_ORIG_FILENAME
        or track.language != "ko"
        or track.source.value != "automatic"
        or track.track_id != "automatic-ko-orig"
        or track.variant != "ko-orig"
        or track.sha256 is None
        or not track.eligible_for_speech
        or track.duplicate_of is not None
    ):
        message = "subtitle download requires exact automatic ko-orig metadata"
        raise SubtitleAdmissionError(message)
    if not path.is_file():
        message = "exact automatic ko-orig subtitle file is missing"
        raise SubtitleAdmissionError(message)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != track.sha256:
        message = "exact automatic ko-orig subtitle digest does not match bytes"
        raise SubtitleAdmissionError(message)
    return tracks


def build_policy_bound_inventory(
    run_dir: Path,
    tracks: tuple[PreparedSubtitleTrack, ...],
    policy: VisualEvidencePolicy | None = None,
) -> SubtitleInventory:
    """Bind exact prepared tracks to run-relative identities and current policy."""
    active = default_evidence_policy() if policy is None else policy
    validated_tracks = validate_prepared_subtitle_tracks(tracks)
    identities: list[SubtitleTrackIdentity] = []
    for track in validated_tracks:
        if (
            track.track_id is None
            or track.variant is None
            or track.sha256 is None
            or not track.eligible_for_speech
        ):
            message = "policy-bound subtitle track lacks exact identity metadata"
            raise SubtitleAdmissionError(message)
        path = _resolve_confined(run_dir, Path(track.path))
        identities.append(
            SubtitleTrackIdentity(
                track_id=track.track_id,
                variant=track.variant,
                source=track.source.value,
                path=path.relative_to(run_dir.resolve()).as_posix(),
                sha256=track.sha256,
                eligible_for_speech=True,
                duplicate_of=track.duplicate_of,
            )
        )
    return SubtitleInventory(
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_digest(active),
        tracks=tuple(identities),
    )


def admitted_subtitle_tracks(
    manifest: PreparedMediaManifest,
    run_dir: Path,
    policy: VisualEvidencePolicy | None = None,
) -> tuple[PreparedSubtitleTrack, ...]:
    """Return exact eligible tracks after policy, path, identity, and byte checks."""
    inventory = manifest.subtitle_inventory
    if inventory is None or inventory.era is EvidenceEra.LEGACY:
        return manifest.subtitle_tracks
    active = default_evidence_policy() if policy is None else policy
    if inventory.policy_digest != policy_digest(active):
        message = "subtitle inventory policy digest is stale"
        raise SubtitleAdmissionError(message)
    tracks_by_id = _identified_manifest_tracks(manifest.subtitle_tracks)
    admitted = tuple(
        _admit_inventory_track(run_dir, identity, tracks_by_id)
        for identity in inventory.eligible_tracks
    )
    if len(admitted) != len(manifest.subtitle_tracks):
        message = "manifest exposes a subtitle track not admitted by inventory"
        raise SubtitleAdmissionError(message)
    return admitted


def _identified_manifest_tracks(
    tracks: tuple[PreparedSubtitleTrack, ...],
) -> dict[str, PreparedSubtitleTrack]:
    identified = {
        track.track_id: track for track in tracks if track.track_id is not None
    }
    if len(identified) != len(tracks):
        message = "policy-bound manifest has an unidentified subtitle track"
        raise SubtitleAdmissionError(message)
    return identified


def _admit_inventory_track(
    run_dir: Path,
    identity: SubtitleTrackIdentity,
    tracks_by_id: dict[str, PreparedSubtitleTrack],
) -> PreparedSubtitleTrack:
    track = tracks_by_id.get(identity.track_id)
    if track is None:
        message = "eligible subtitle identity is missing from manifest"
        raise SubtitleAdmissionError(message)
    if (
        track.variant != identity.variant
        or track.source.value != identity.source
        or track.sha256 != identity.sha256
        or not track.eligible_for_speech
        or track.duplicate_of is not None
    ):
        message = "eligible subtitle track metadata does not match inventory"
        raise SubtitleAdmissionError(message)
    inventory_path = _resolve_confined(run_dir, Path(identity.path))
    track_path = _resolve_confined(run_dir, Path(track.path))
    if inventory_path != track_path:
        message = "eligible subtitle track path does not match inventory"
        raise SubtitleAdmissionError(message)
    if not track_path.is_file():
        message = "eligible subtitle track is missing"
        raise SubtitleAdmissionError(message)
    with track_path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != identity.sha256:
        message = "eligible subtitle track digest does not match stored bytes"
        raise SubtitleAdmissionError(message)
    return track


def _resolve_confined(run_dir: Path, candidate: Path) -> Path:
    root = run_dir.resolve()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        cwd_candidate = candidate.resolve()
        rooted_candidate = (root / candidate).resolve()
        resolved = (
            cwd_candidate
            if cwd_candidate.is_relative_to(root)
            else rooted_candidate
        )
    if not resolved.is_relative_to(root):
        message = f"subtitle evidence path leaves the run: {candidate}"
        raise SubtitleAdmissionError(message)
    return resolved


__all__ = [
    "AUTOMATIC_KO_ORIG_FILENAME",
    "SubtitleAdmissionError",
    "admitted_subtitle_tracks",
    "build_policy_bound_inventory",
    "validate_prepared_subtitle_tracks",
]
