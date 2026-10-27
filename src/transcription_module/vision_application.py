# Copyright 2026 Transcription Module contributors

"""Persistable vision decisions applied to canonical transcripts."""

from __future__ import annotations

from transcription_module.models import (
    FrameId,
    FrozenModel,
    RunId,
    SubtitleTrust,
    Transcript,
)
from transcription_module.timing import (
    FrameManifest,
    SubtitleInterval,
    subtitle_interval_from_pts,
)
from transcription_module.vision import (
    AuditSegment,
    ScreenEvidence,
    VisionAuditRequest,
    VisionContractError,
    VisionFinding,
    VisionResolution,
    VisionRouteResult,
    apply_screen_evidence,
    assert_full_vision_coverage,
    resolve_vision,
)


class VisionDecisionBundle(FrozenModel):
    """Agent-supplied findings plus manifest-backed application policy."""

    manifest: FrameManifest
    audit_segments: tuple[AuditSegment, ...]
    request: VisionAuditRequest
    sol: VisionFinding
    terra: VisionFinding
    ocr: VisionFinding
    cue_sequence: int
    estimated_trust: SubtitleTrust
    trust_override: SubtitleTrust | None = None
    meaning_matches: bool
    audio_boundary_uncertain: bool


class VisionAppliedArtifact(FrozenModel):
    """Auditable result of applying one resolved lower-third decision."""

    run_id: RunId
    cue_sequence: int
    route: tuple[str, ...]
    scene: str
    rough_lower_third: str
    interval: SubtitleInterval
    original_speech: str
    screen_text: str


def apply_vision_decisions(
    transcript: Transcript,
    decisions: VisionDecisionBundle,
) -> tuple[Transcript, VisionAppliedArtifact]:
    """Validate full coverage and apply one manifest-backed visual decision."""
    if decisions.manifest.duration_ms != transcript.duration_ms:
        msg = "vision manifest duration must match transcript duration"
        raise VisionContractError(msg)
    assert_full_vision_coverage(
        decisions.audit_segments,
        duration_ms=transcript.duration_ms,
        cues=transcript.cues,
    )
    _assert_manifest_references(transcript, decisions)
    route_result = resolve_vision(
        decisions.request,
        sol=lambda _request: decisions.sol,
        terra=lambda _request: decisions.terra,
        ocr=lambda _request: decisions.ocr,
    )
    finding, lower_third, first_visible_frame_id = (
        _require_resolved_lower_third(route_result)
    )
    interval = subtitle_interval_from_pts(
        decisions.manifest,
        first_visible_frame_id=first_visible_frame_id,
        first_not_visible_frame_id=finding.first_not_visible_frame_id,
    )
    cue = next(
        (
            item
            for item in transcript.cues
            if item.sequence == decisions.cue_sequence
        ),
        None,
    )
    if cue is None:
        msg = "vision decision references an unknown cue"
        raise VisionContractError(msg)
    updated_cue = apply_screen_evidence(
        cue,
        ScreenEvidence(
            screen_text=lower_third,
            interval=interval,
            estimated_trust=decisions.estimated_trust,
            trust_override=decisions.trust_override,
            meaning_matches=decisions.meaning_matches,
            audio_boundary_uncertain=decisions.audio_boundary_uncertain,
        ),
    )
    updated = transcript.model_copy(
        update={
            "cues": tuple(
                updated_cue if item.sequence == cue.sequence else item
                for item in transcript.cues
            )
        }
    )
    artifact = VisionAppliedArtifact(
        run_id=transcript.run_id,
        cue_sequence=cue.sequence,
        route=route_result.route,
        scene=finding.scene,
        rough_lower_third=lower_third,
        interval=interval,
        original_speech=cue.speech,
        screen_text=updated_cue.screen_text or "",
    )
    return updated, artifact


def _assert_manifest_references(
    transcript: Transcript,
    decisions: VisionDecisionBundle,
) -> None:
    for frame_id in decisions.request.frame_ids:
        _ = decisions.manifest.entry(frame_id)
    for evidence in transcript.vision_evidence:
        for frame_id in evidence.frame_ids:
            _ = decisions.manifest.entry(frame_id)


def _require_resolved_lower_third(
    result: VisionRouteResult,
) -> tuple[VisionFinding, str, FrameId]:
    finding = result.finding
    if finding.resolution is not VisionResolution.RESOLVED:
        msg = "Sol, Terra, and OCR left the visual decision unresolved"
        raise VisionContractError(msg)
    lower_third = finding.rough_lower_third
    first_visible_frame_id = finding.first_visible_frame_id
    if lower_third is None or first_visible_frame_id is None:
        msg = "resolved lower-third evidence requires text and a visible frame"
        raise VisionContractError(msg)
    return finding, lower_third, first_visible_frame_id
