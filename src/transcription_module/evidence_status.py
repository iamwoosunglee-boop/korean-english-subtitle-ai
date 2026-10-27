# Copyright 2026 Transcription Module contributors

"""Read-only operator inventory for policy-bound transcription authority."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from transcription_module._drafting_models import (
    WholeTranscriptArtifact,
    WindowDraftArtifact,
)
from transcription_module.asr import AsrRunArtifact
from transcription_module.correction_receipts import CorrectionReceiptSet
from transcription_module.cue_provenance_models import CueProvenanceSet
from transcription_module.evidence_status_models import (
    EvidenceArtifactState,
    EvidenceArtifactStatus,
    EvidenceStatusReport,
    SubtitleInventoryStatus,
)
from transcription_module.final_review_models import (
    FinalReviewAssignment,
    FinalReviewReceipt,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    FrozenModel,
    RunId,
    Transcript,
    ValidationRunArtifact,
    ValidationStatus,
)
from transcription_module.protected import ProtectedRunArtifact
from transcription_module.subtitle_inventory import SubtitleInventory
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    canonical_policy_bytes,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_observations import VisualObservationSet
from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.source import SourceTranscriptV2

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.storage import RunStore


def inspect_evidence_status(store: RunStore, run_id: RunId) -> EvidenceStatusReport:
    """Inspect current persisted authority without creating or rewriting files."""
    paths = store.paths(run_id)
    _ = store.load_state(run_id)
    current_policy = default_evidence_policy()
    policy_sha = policy_digest(current_policy)
    protected = _model_status(
        paths.run_dir / "protected-run.json",
        ProtectedRunArtifact,
    )
    media_status = _run_model_status(
        paths.media_manifest,
        PreparedMediaManifest,
        run_id,
    )
    asr_status = _run_model_status(paths.asr_transcript, AsrRunArtifact, run_id)
    policy = _policy_status(paths.visual_policy_artifact)
    media = _read_optional(paths.media_manifest, PreparedMediaManifest)
    inventory = _inventory_status(
        paths.subtitle_inventory_artifact,
        media,
        _ExpectedBinding(run_id=run_id, policy_digest=policy_sha),
    )
    expected = _ExpectedBinding(run_id=run_id, policy_digest=policy_sha)
    vad = _bound_status(paths.vad_artifact, VadEvidenceArtifact, expected)
    observations = _bound_status(
        paths.visual_observations_artifact,
        VisualObservationSet,
        expected,
    )
    corrections = _bound_status(
        paths.correction_receipts_artifact,
        CorrectionReceiptSet,
        expected,
    )
    provenance = _bound_status(
        paths.cue_provenance_artifact,
        CueProvenanceSet,
        expected,
    )
    windows = _window_status(paths.window_drafts, expected)
    assembly = _assembly_status(
        paths.whole_transcript_artifact,
        paths.transcript,
        expected,
    )
    anchor = _anchor_status(paths.final_review_assignment, run_id)
    final_review = _run_model_status(
        paths.final_review_artifact,
        FinalReviewReceipt,
        run_id,
    )
    validation = _validation_status(paths.validation_artifact, run_id)
    context = _model_status(paths.input_context, ContentContextAsset)
    source = _model_status(paths.canonical_source, SourceTranscriptV2)
    stale_states = {EvidenceArtifactState.STALE, EvidenceArtifactState.INVALID}
    policy_inputs = (policy, inventory, vad)
    policy_dependents = (
        policy,
        inventory,
        vad,
        observations,
        corrections,
        provenance,
        windows,
        assembly,
        anchor,
        final_review,
        validation,
        context,
        source,
    )
    invalidation_required = any(
        item.state in stale_states for item in policy_dependents
    )
    downstream_required = (
        (observations, corrections)
        if windows.state is EvidenceArtifactState.CURRENT
        else ()
    )
    provenance_required = (
        (provenance,) if assembly.state is EvidenceArtifactState.CURRENT else ()
    )
    anchor_required = (
        (anchor,) if final_review.state is EvidenceArtifactState.CURRENT else ()
    )
    canonical_required = (
        (final_review, anchor, context, source)
        if validation.state is EvidenceArtifactState.CURRENT
        else ()
    )
    present_artifacts_are_valid = all(
        item.state not in stale_states for item in policy_dependents
    )
    reusable = present_artifacts_are_valid and all(
        item.state is EvidenceArtifactState.CURRENT
        for item in (
            *policy_inputs,
            *downstream_required,
            *provenance_required,
            *anchor_required,
            *canonical_required,
        )
    )
    return EvidenceStatusReport(
        run_id=run_id,
        era=EvidenceEra.POLICY_BOUND,
        protected_run=protected,
        media=media_status,
        asr=asr_status,
        policy=policy,
        subtitle_inventory=inventory,
        vad=vad,
        visual_observations=observations,
        correction_receipts=corrections,
        cue_provenance=provenance,
        window_evidence=windows,
        deterministic_assembly=assembly,
        confirmation_anchor=anchor,
        final_review=final_review,
        validation=validation,
        context_asset=context,
        canonical_source=source,
        invalidation_required=invalidation_required,
        reusable=reusable,
    )


class _PolicyBinding(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    run_id: RunId
    policy_digest: str


@dataclass(frozen=True, slots=True)
class _ExpectedBinding:
    run_id: RunId
    policy_digest: str


def _policy_status(path: Path) -> EvidenceArtifactStatus:
    if not path.is_file():
        return _absent(path)
    payload = path.read_bytes()
    expected = canonical_policy_bytes(default_evidence_policy()) + b"\n"
    return EvidenceArtifactStatus(
        path=path.name,
        state=(
            EvidenceArtifactState.CURRENT
            if payload == expected
            else EvidenceArtifactState.STALE
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _inventory_status(
    path: Path,
    media: PreparedMediaManifest | None,
    expected: _ExpectedBinding,
) -> SubtitleInventoryStatus:
    if (
        media is None
        or media.run_id != expected.run_id
        or media.subtitle_inventory is None
    ):
        return SubtitleInventoryStatus(
            path=path.name,
            state=EvidenceArtifactState.ABSENT,
            availability="unknown",
            exact_ko_orig_count=0,
        )
    embedded = media.subtitle_inventory
    eligible = len(embedded.eligible_tracks)
    if not path.is_file():
        state = EvidenceArtifactState.ABSENT
        digest = None
    else:
        payload = path.read_bytes()
        try:
            stored = SubtitleInventory.model_validate_json(payload)
        except ValidationError:
            state = EvidenceArtifactState.INVALID
        else:
            state = (
                EvidenceArtifactState.CURRENT
                if stored == embedded
                and stored.policy_digest == expected.policy_digest
                else EvidenceArtifactState.STALE
            )
        digest = hashlib.sha256(payload).hexdigest()
    return SubtitleInventoryStatus(
        path=path.name,
        state=state,
        sha256=digest,
        availability="available" if eligible == 1 else "optional_unavailable",
        exact_ko_orig_count=eligible,
    )


def _bound_status[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
    expected: _ExpectedBinding,
) -> EvidenceArtifactStatus:
    if not path.is_file():
        return _absent(path)
    payload = path.read_bytes()
    try:
        _ = model.model_validate_json(payload)
        binding = _PolicyBinding.model_validate_json(payload)
    except ValidationError:
        state = EvidenceArtifactState.INVALID
    else:
        state = (
            EvidenceArtifactState.CURRENT
            if binding.run_id == expected.run_id
            and binding.policy_digest == expected.policy_digest
            else EvidenceArtifactState.STALE
        )
    return EvidenceArtifactStatus(
        path=path.name,
        state=state,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _window_status(
    path: Path,
    expected: _ExpectedBinding,
) -> EvidenceArtifactStatus:
    status = _model_status(path, WindowDraftArtifact)
    if status.state is not EvidenceArtifactState.CURRENT:
        return status
    artifact = WindowDraftArtifact.model_validate_json(path.read_bytes())
    bindings = (
        *((item.run_id, item.policy_digest) for item in artifact.inputs),
        *((artifact.run_id, item.policy_digest) for item in artifact.drafts),
    )
    if any(
        run_id != expected.run_id or digest != expected.policy_digest
        for run_id, digest in bindings
    ):
        return status.model_copy(update={"state": EvidenceArtifactState.STALE})
    return status


def _assembly_status(
    path: Path,
    transcript_path: Path,
    expected: _ExpectedBinding,
) -> EvidenceArtifactStatus:
    status = _model_status(path, WholeTranscriptArtifact)
    if status.state is not EvidenceArtifactState.CURRENT:
        return status
    artifact = WholeTranscriptArtifact.model_validate_json(path.read_bytes())
    transcript = _read_optional(transcript_path, Transcript)
    if (
        artifact.run_id != expected.run_id
        or artifact.policy_digest != expected.policy_digest
        or transcript is None
        or transcript.run_id != expected.run_id
    ):
        return status.model_copy(update={"state": EvidenceArtifactState.STALE})
    return status


def _anchor_status(path: Path, run_id: RunId) -> EvidenceArtifactStatus:
    status = _model_status(path, FinalReviewAssignment)
    if status.state is not EvidenceArtifactState.CURRENT:
        return status
    assignment = FinalReviewAssignment.model_validate_json(path.read_bytes())
    if assignment.run_id != run_id:
        return status.model_copy(update={"state": EvidenceArtifactState.STALE})
    return status


def _validation_status(path: Path, run_id: RunId) -> EvidenceArtifactStatus:
    status = _model_status(path, ValidationRunArtifact)
    if status.state is not EvidenceArtifactState.CURRENT:
        return status
    artifact = ValidationRunArtifact.model_validate_json(path.read_bytes())
    if (
        artifact.run_id != run_id
        or artifact.validation_status is not ValidationStatus.VALID
    ):
        return status.model_copy(update={"state": EvidenceArtifactState.STALE})
    return status


def _run_model_status[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
    run_id: RunId,
) -> EvidenceArtifactStatus:
    status = _model_status(path, model)
    if status.state is not EvidenceArtifactState.CURRENT:
        return status
    binding = _RunBinding.model_validate_json(path.read_bytes())
    if binding.run_id != run_id:
        return status.model_copy(update={"state": EvidenceArtifactState.STALE})
    return status


class _RunBinding(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    run_id: RunId


def _model_status[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
) -> EvidenceArtifactStatus:
    if not path.is_file():
        return _absent(path)
    payload = path.read_bytes()
    try:
        _ = model.model_validate_json(payload)
    except ValidationError:
        state = EvidenceArtifactState.INVALID
    else:
        state = EvidenceArtifactState.CURRENT
    return EvidenceArtifactStatus(
        path=path.name,
        state=state,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _read_optional[ModelT: BaseModel](
    path: Path,
    model: type[ModelT],
) -> ModelT | None:
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_bytes())
    except ValidationError:
        return None


def _absent(path: Path) -> EvidenceArtifactStatus:
    return EvidenceArtifactStatus(path=path.name, state=EvidenceArtifactState.ABSENT)


__all__ = ["EvidenceStatusReport", "inspect_evidence_status"]
