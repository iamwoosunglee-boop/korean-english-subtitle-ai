# Copyright 2026 Transcription Module contributors

"""Policy, inventory, and exact subtitle identity snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from transcription_module.subtitle_admission import SubtitleAdmissionError
from transcription_module.subtitle_inventory import SubtitleInventory
from transcription_module.visual_evidence_policy import (
    VisualEvidencePolicy,
    canonical_policy_bytes,
    default_evidence_policy,
)
from transcription_module.window_evidence_errors import fail_window_evidence

if TYPE_CHECKING:
    from transcription_module.media import (
        PreparedMediaManifest,
        PreparedSubtitleTrack,
    )
    from transcription_module.storage_paths import RunPaths
    from transcription_module.window_snapshot_fs import SnapshotBuilder


@dataclass(frozen=True, slots=True)
class CapturedTracks:
    """Admitted subtitle models and their exact captured VTT payloads."""

    tracks: tuple[PreparedSubtitleTrack, ...]
    payloads: tuple[bytes, ...]


def capture_policy_identity(
    builder: SnapshotBuilder,
    paths: RunPaths,
    media: PreparedMediaManifest,
) -> SubtitleInventory:
    """Snapshot optional sidecars and require equality with media authority."""
    inventory = media.subtitle_inventory
    if inventory is None:
        fail_window_evidence("policy-bound window inventory is missing")
    policy_file = builder.optional(
        "visual evidence policy",
        paths.visual_policy_artifact,
    )
    inventory_file = builder.optional(
        "subtitle inventory",
        paths.subtitle_inventory_artifact,
    )
    if (policy_file is None) != (inventory_file is None):
        fail_window_evidence(
            "policy and inventory sidecars must be snapshotted together"
        )
    if policy_file is None or inventory_file is None:
        return inventory
    policy = VisualEvidencePolicy.model_validate_json(policy_file.require_payload())
    stored_inventory = SubtitleInventory.model_validate_json(
        inventory_file.require_payload()
    )
    expected_policy = canonical_policy_bytes(policy) + b"\n"
    if (
        policy_file.require_payload() != expected_policy
        or policy != default_evidence_policy()
        or stored_inventory != inventory
    ):
        fail_window_evidence(
            "policy or inventory snapshot differs from media authority"
        )
    return stored_inventory


def capture_subtitle_tracks(
    builder: SnapshotBuilder,
    media: PreparedMediaManifest,
    inventory: SubtitleInventory,
) -> CapturedTracks:
    """Capture each exact eligible VTT once and replay its stored identity."""
    identities = {item.track_id: item for item in inventory.eligible_tracks}
    payloads: list[bytes] = []
    for track in media.subtitle_tracks:
        identity = identities.get(track.track_id or "")
        snapshot = builder.required(
            f"subtitle:{track.track_id or 'missing'}",
            Path(track.path),
        )
        relative = snapshot.path.relative_to(builder.root).as_posix()
        if (
            identity is None
            or identity.path != relative
            or snapshot.sha256 != identity.sha256
            or track.sha256 != identity.sha256
        ):
            message = "eligible subtitle track digest does not match stored bytes"
            raise SubtitleAdmissionError(message)
        payloads.append(snapshot.require_payload())
    if len(payloads) != len(identities):
        message = "manifest exposes a subtitle track not admitted by inventory"
        raise SubtitleAdmissionError(message)
    return CapturedTracks(media.subtitle_tracks, tuple(payloads))


__all__ = [
    "CapturedTracks",
    "capture_policy_identity",
    "capture_subtitle_tracks",
]
