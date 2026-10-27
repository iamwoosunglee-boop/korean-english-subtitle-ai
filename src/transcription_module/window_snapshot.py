# Copyright 2026 Transcription Module contributors

"""Manifest-driven immutable input snapshot for bounded window drafting."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from transcription_module._drafting_models import (
    DiarizationRunArtifact,
    DiarizationStatus,
)
from transcription_module._drafting_window_evidence import WindowEvidenceSnapshot
from transcription_module.asr import AsrRunArtifact
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import RunState
from transcription_module.subtitle_inventory import subtitle_inventory_digest
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.window_evidence_errors import fail_window_evidence
from transcription_module.window_evidence_inputs import (
    WindowSourceAuthority,
    parse_window_subtitle_cues,
    window_authority_source_digest,
    window_source_digest,
    window_vad_intervals,
    window_verified_turns,
)
from transcription_module.window_snapshot_fs import SnapshotBuilder
from transcription_module.window_snapshot_identity import (
    capture_policy_identity,
    capture_subtitle_tracks,
)
from transcription_module.window_snapshot_speaker import capture_speaker_files
from transcription_module.window_speaker_authority import (
    replay_verified_speaker_turn_artifact,
)
from transcription_module.window_speaker_authority_models import (
    SpeakerTurnReplayContext,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from transcription_module.models import RunId
    from transcription_module.storage import RunStore
    from transcription_module.storage_paths import RunPaths
    from transcription_module.window_snapshot_models import FileSnapshotSet


@dataclass(frozen=True, slots=True)
class OpenedWindowEvidence:
    """Parsed immutable evidence plus every still-open source descriptor."""

    evidence: WindowEvidenceSnapshot
    files: FileSnapshotSet


@dataclass(frozen=True, slots=True)
class _BaseSources:
    """Persisted model bytes common to legacy and policy-bound windows."""

    run_id: RunId
    media: PreparedMediaManifest
    asr: AsrRunArtifact
    diarization: DiarizationRunArtifact | None
    vision_artifact_path: str | None
    state: RunState


@contextmanager
def opened_window_evidence(
    store: RunStore,
    run_id: RunId,
) -> Generator[OpenedWindowEvidence]:
    """Open every drafting input once and hold it through publication."""
    paths = store.paths(run_id)
    with ExitStack() as stack:
        builder = SnapshotBuilder(paths.run_dir, stack)
        base = _capture_base(builder, paths, run_id)
        inventory = base.media.subtitle_inventory
        evidence = (
            _legacy_evidence(base)
            if inventory is None or inventory.era is EvidenceEra.LEGACY
            else _policy_evidence(builder, paths, base)
        )
        yield OpenedWindowEvidence(evidence, builder.freeze())


def _capture_base(
    builder: SnapshotBuilder,
    paths: RunPaths,
    run_id: RunId,
) -> _BaseSources:
    media = PreparedMediaManifest.model_validate_json(
        builder.required("media manifest", paths.media_manifest).require_payload()
    )
    for frame in media.frames:
        _ = builder.required(
            f"frame:{frame.frame_id}",
            Path(frame.path),
            retain_payload=False,
        )
    state = RunState.model_validate_json(
        builder.required("run state", paths.state).require_payload()
    )
    asr = AsrRunArtifact.model_validate_json(
        builder.required("ASR artifact", paths.asr_transcript).require_payload()
    )
    diarization_file = builder.optional(
        "diarization derived artifact",
        paths.diarization_artifact,
    )
    diarization = (
        None
        if diarization_file is None
        else DiarizationRunArtifact.model_validate_json(
            diarization_file.require_payload()
        )
    )
    vision = builder.optional("vision derived artifact", paths.vision_artifact)
    return _BaseSources(
        run_id=run_id,
        media=media,
        asr=asr,
        diarization=diarization,
        vision_artifact_path=None if vision is None else str(vision.path),
        state=state,
    )


def _legacy_evidence(base: _BaseSources) -> WindowEvidenceSnapshot:
    diarization = base.diarization
    speaker_segments = (
        diarization.segments
        if diarization is not None and diarization.status is DiarizationStatus.AVAILABLE
        else ()
    )
    return WindowEvidenceSnapshot(
        media=base.media,
        asr_segments=base.asr.transcript.segments,
        subtitle_tracks=base.media.subtitle_tracks,
        speaker_segments=speaker_segments,
        vision_artifact_path=base.vision_artifact_path,
        episode_context=base.state.context,
    )


def _policy_evidence(
    builder: SnapshotBuilder,
    paths: RunPaths,
    base: _BaseSources,
) -> WindowEvidenceSnapshot:
    inventory = capture_policy_identity(builder, paths, base.media)
    captured_tracks = capture_subtitle_tracks(builder, base.media, inventory)
    vad_file = builder.required("VAD artifact", paths.vad_artifact)
    _ = builder.required(
        "source video",
        Path(base.media.video_path),
        retain_payload=False,
    )
    audio = builder.required(
        "source audio",
        Path(base.media.audio_path),
        retain_payload=False,
    )
    speaker_files = capture_speaker_files(builder, paths, inventory)
    active_policy = default_evidence_policy()
    policy_sha = policy_digest(active_policy)
    asr = base.asr
    if (
        inventory.policy_digest != policy_sha
        or asr.era is not EvidenceEra.POLICY_BOUND
        or asr.policy_digest != policy_sha
    ):
        fail_window_evidence("policy-bound window evidence has stale policy authority")
    vad = VadEvidenceArtifact.model_validate_json(vad_file.require_payload())
    _validate_policy_sources(base, vad, audio.sha256)
    source_authority = WindowSourceAuthority(
        base.media,
        asr,
        vad,
        base.diarization,
        None,
    )
    source_sha = window_authority_source_digest(source_authority)
    verified = replay_verified_speaker_turn_artifact(
        SpeakerTurnReplayContext(
            paths.run_dir,
            base.run_id,
            policy_sha,
            source_sha,
            base.media.duration_ms,
            tuple((str(item.frame_id), item.pts_ms) for item in base.media.frames),
            tuple(
                (f"vad:{index}", int(item.start_tc_ms), int(item.end_tc_ms))
                for index, item in enumerate(vad.speech_intervals, start=1)
            ),
            inventory,
            base.diarization,
        ),
        speaker_files.derived_payload,
        speaker_files.evidence,
    )
    diarization = base.diarization
    return WindowEvidenceSnapshot(
        media=base.media,
        asr_segments=asr.transcript.segments,
        subtitle_tracks=captured_tracks.tracks,
        speaker_segments=(
            diarization.segments
            if diarization is not None
            and diarization.status is DiarizationStatus.AVAILABLE
            else ()
        ),
        vision_artifact_path=base.vision_artifact_path,
        episode_context=base.state.context,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_sha,
        inventory_digest=subtitle_inventory_digest(inventory),
        source_digest=window_source_digest(
            WindowSourceAuthority(base.media, asr, vad, diarization, verified)
        ),
        subtitle_cues=parse_window_subtitle_cues(
            captured_tracks.tracks,
            captured_tracks.payloads,
        ),
        vad_intervals=window_vad_intervals(vad),
        verified_turns=window_verified_turns(verified),
    )


def _validate_policy_sources(
    base: _BaseSources,
    vad: VadEvidenceArtifact,
    audio_sha256: str,
) -> None:
    run_ids = (
        base.state.run_id,
        base.media.run_id,
        base.asr.run_id,
        vad.run_id,
        *(() if base.diarization is None else (base.diarization.run_id,)),
    )
    if any(item != base.run_id for item in run_ids):
        fail_window_evidence("policy-bound window source has a foreign run identity")
    if base.asr.audio_sha256 != audio_sha256 or vad.audio_sha256 != audio_sha256:
        fail_window_evidence("policy-bound window audio bytes are stale")
    if vad.duration_ms != base.media.duration_ms:
        fail_window_evidence("policy-bound window VAD authority is stale")


__all__ = ["OpenedWindowEvidence", "opened_window_evidence"]
