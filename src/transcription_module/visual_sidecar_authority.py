# Copyright 2026 Transcription Module contributors

"""Atomic publication authority for deterministic visual evidence sidecars."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import ValidationError

from transcription_module.artifact_io import (
    durable_replace_bytes,
    durable_restore,
    read_confined_bytes,
)
from transcription_module.asr import AsrRunArtifact
from transcription_module.cue_provenance import (
    CueProvenanceError,
    CueProvenanceValidationInput,
    validate_cue_provenance,
)
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    canonical_policy_bytes,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_sidecar_models import (
    VisualSidecarArtifactReceipt,
    VisualSidecarBundle,
    VisualSidecarPublicationReceipt,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

    from transcription_module.models import RunId
    from transcription_module.storage import RunPaths, RunStore
    from transcription_module.subtitle_inventory import SubtitleInventory


class VisualSidecarPublicationError(ValueError):
    """A complete sidecar bundle is stale, incomplete, or conflicts with authority."""


def publish_policy_sidecars(
    store: RunStore,
    run_id: RunId,
    inventory: SubtitleInventory,
) -> None:
    """Publish current policy and exact subtitle inventory as one rollback unit."""
    policy = default_evidence_policy()
    if (
        inventory.era is not EvidenceEra.POLICY_BOUND
        or inventory.policy_digest != policy_digest(policy)
    ):
        message = "policy sidecar inventory is stale or legacy"
        raise VisualSidecarPublicationError(message)
    paths = store.paths(run_id)
    payloads = {
        paths.visual_policy_artifact: canonical_policy_bytes(policy) + b"\n",
        paths.subtitle_inventory_artifact: _model_payload(inventory),
    }
    with store.lock_run(run_id):
        snapshots = {
            path: read_confined_bytes(path, missing_ok=True) for path in payloads
        }
        _assert_no_conflicting_authority(payloads, snapshots)
        try:
            for path, payload in payloads.items():
                if snapshots[path] is None:
                    write_visual_sidecar(path, payload)
        except BaseException:
            for path, prior in snapshots.items():
                durable_restore(path, prior)
            raise


def publish_visual_sidecars(
    store: RunStore,
    bundle: VisualSidecarBundle,
) -> VisualSidecarPublicationReceipt:
    """Validate every reference, then publish all fixed sidecars or none."""
    validated = _validate_bundle(store, bundle)
    with store.lock_run(validated.run_id):
        paths = store.paths(validated.run_id)
        payloads = _sidecar_payloads(paths, validated)
        snapshots = {
            path: read_confined_bytes(path, missing_ok=True) for path in payloads
        }
        _assert_no_conflicting_authority(payloads, snapshots)
        try:
            for path, payload in payloads.items():
                if snapshots[path] is None:
                    write_visual_sidecar(path, payload)
        except BaseException:
            for path, prior in snapshots.items():
                durable_restore(path, prior)
            raise
    return VisualSidecarPublicationReceipt(
        run_id=validated.run_id,
        policy_digest=policy_digest(validated.policy),
        source_digest=validated.source_digest,
        artifacts=tuple(
            VisualSidecarArtifactReceipt(
                path=path.relative_to(paths.run_dir).as_posix(),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            for path, payload in payloads.items()
        ),
    )


def write_visual_sidecar(path: Path, payload: bytes) -> None:
    """Publish one sidecar through the replaceable durable write seam."""
    durable_replace_bytes(path, payload)


def _validate_bundle(
    store: RunStore,
    bundle: VisualSidecarBundle,
) -> VisualSidecarBundle:
    try:
        validated = VisualSidecarBundle.model_validate(
            bundle.model_dump(mode="json")
        )
        transcript = store.load_transcript(validated.run_id)
        validate_cue_provenance(
            CueProvenanceValidationInput(
                transcript=transcript,
                provenance=validated.provenance,
                corrections=validated.corrections,
                observations=validated.observations,
                source_digest=validated.source_digest,
                policy_digest=policy_digest(validated.policy),
            )
        )
        _validate_track_references(validated)
        _validate_asr_references(store, validated)
    except (
        CueProvenanceError,
        FileNotFoundError,
        OSError,
        UnicodeError,
        ValidationError,
        ValueError,
    ) as error:
        message = f"visual sidecar bundle is invalid: {error}"
        raise VisualSidecarPublicationError(message) from error
    return validated


def _validate_track_references(bundle: VisualSidecarBundle) -> None:
    eligible = {
        track.track_id for track in bundle.inventory.tracks if track.eligible_for_speech
    }
    referenced = {
        track_id
        for record in bundle.provenance.records
        for track_id in record.subtitle_track_ids
    }
    if not referenced <= eligible:
        message = "cue provenance references an unknown or ineligible subtitle track"
        raise CueProvenanceError(message)


def _validate_asr_references(store: RunStore, bundle: VisualSidecarBundle) -> None:
    referenced = {
        ordinal
        for record in bundle.provenance.records
        for ordinal in record.asr_ordinals
    }
    if not referenced:
        return
    path = store.paths(bundle.run_id).asr_transcript
    asr = AsrRunArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    if (
        asr.era is not EvidenceEra.POLICY_BOUND
        or asr.policy_digest != policy_digest(bundle.policy)
    ):
        message = "cue provenance references stale ASR authority"
        raise CueProvenanceError(message)
    known = {
        segment.source_ordinal
        for segment in asr.transcript.segments
        if segment.source_ordinal is not None
    }
    if not referenced <= known:
        message = "cue provenance references an unknown ASR ordinal"
        raise CueProvenanceError(message)


def _sidecar_payloads(
    paths: RunPaths,
    bundle: VisualSidecarBundle,
) -> dict[Path, bytes]:
    return {
        paths.visual_policy_artifact: canonical_policy_bytes(bundle.policy) + b"\n",
        paths.subtitle_inventory_artifact: _model_payload(bundle.inventory),
        paths.visual_observations_artifact: _model_payload(bundle.observations),
        paths.correction_receipts_artifact: _model_payload(bundle.corrections),
        paths.cue_provenance_artifact: _model_payload(bundle.provenance),
    }


def _model_payload(model: BaseModel) -> bytes:
    return f"{model.model_dump_json(indent=2)}\n".encode()


def _assert_no_conflicting_authority(
    payloads: dict[Path, bytes],
    snapshots: dict[Path, bytes | None],
) -> None:
    conflicting = next(
        (
            path
            for path, payload in payloads.items()
            if snapshots[path] is not None and snapshots[path] != payload
        ),
        None,
    )
    if conflicting is not None:
        message = f"visual sidecar authority conflicts at {conflicting.name}"
        raise VisualSidecarPublicationError(message)


__all__ = [
    "VisualSidecarBundle",
    "VisualSidecarPublicationError",
    "VisualSidecarPublicationReceipt",
    "publish_policy_sidecars",
    "publish_visual_sidecars",
    "write_visual_sidecar",
]
