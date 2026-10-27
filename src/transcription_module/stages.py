# Copyright 2026 Transcription Module contributors

"""Executable media, ASR, and vision stage services."""

from __future__ import annotations

import warnings
from pathlib import Path

from transcription_module.asr import (
    DEFAULT_ASR_MODEL,
    AsrBackendError,
    AsrRunArtifact,
    load_mlx_whisper,
)
from transcription_module.media import (
    BrowserCookieSource,
    MediaAdapters,
    MediaProbeError,
    MediaStateError,
    MediaToolError,
    PreparedFrameManifest,
    PreparedMediaManifest,
)
from transcription_module.media_checkpoint import (
    CheckpointRecoveryError,
    CheckpointStorageError,
    MediaCheckpointStore,
    StageName,
)
from transcription_module.media_manifest_authority import (
    publish_media_manifest,
    withdraw_media_manifest,
)
from transcription_module.media_resume import (
    DEFAULT_MEDIA_ADAPTER_CONTRACTS,
    prepare_media_with_checkpoint,
)
from transcription_module.media_runtime import CheckpointedMediaAdapters
from transcription_module.media_subprocess import (
    FfmpegSubprocess,
    FfprobeSubprocess,
    YtDlpSubprocess,
)
from transcription_module.models import (
    EpisodeContext,
    FrameId,
    RunId,
    RunState,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.policy_invalidation import revoke_policy_dependents
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.subtitle_admission import (
    SubtitleAdmissionError,
    build_policy_bound_inventory,
)
from transcription_module.vad_evidence import ensure_vad_evidence
from transcription_module.vision_application import (
    VisionAppliedArtifact,
    VisionDecisionBundle,
    apply_vision_decisions,
)
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.visual_sidecar_authority import (
    VisualSidecarPublicationError,
    publish_policy_sidecars,
)
from transcription_module.workflow import start_run


def prepare_media_run(
    source_url: YouTubeVideoUrl,
    store: RunStore,
    existing_run_id: RunId | None = None,
    youtube_browser: BrowserCookieSource = BrowserCookieSource.CHROME,
) -> PreparedMediaManifest:
    """Prepare media for a direct stage or an existing contextual run."""
    if existing_run_id is None:
        started = start_run(source_url, store)
        run_id = started.run_id
        store.save_state(
            RunState(
                run_id=run_id,
                source_url=source_url,
                workflow_status=WorkflowStatus.PROCESSING,
                context=EpisodeContext(raw_text="없음"),
            )
        )
    else:
        run_id = existing_run_id
    prepared_manifest: PreparedMediaManifest | None = None
    failure: MediaToolError | None = None
    with store.lock_run(run_id):
        try:
            prepared_manifest = _prepare_media_run_locked(
                source_url,
                store,
                run_id,
                youtube_browser,
            )
        except MediaToolError as error:
            failure = error
    if failure is not None:
        raise failure
    if prepared_manifest is None:
        raise MediaStateError(
            argv=("transcription-cli", "prepare"),
            stderr="media preparation completed without a manifest",
        )
    return prepared_manifest


def _prepare_media_run_locked(
    source_url: YouTubeVideoUrl,
    store: RunStore,
    run_id: RunId,
    youtube_browser: BrowserCookieSource,
) -> PreparedMediaManifest:
    state = store.load_state(run_id)
    if state.source_url != source_url:
        raise MediaStateError(
            argv=("transcription-cli", "prepare"),
            stderr="prepared URL must match the existing run source URL",
        )
    paths = store.paths(run_id)
    input_dir = paths.run_dir / "input"
    input_dir.mkdir(exist_ok=True)
    checkpoint = MediaCheckpointStore(input_dir)
    try:
        validation = checkpoint.validate(source_url, DEFAULT_MEDIA_ADAPTER_CONTRACTS)
    except (CheckpointRecoveryError, CheckpointStorageError, OSError):
        withdraw_media_manifest(paths.media_manifest, repair_required=True)
        raise
    if StageName.SUBTITLES in validation.invalid_stages:
        _ = revoke_policy_dependents(store, run_id)
        state = store.load_state(run_id)
    withdraw_media_manifest(
        paths.media_manifest,
        repair_required=bool(validation.invalid_stages),
    )
    adapters = MediaAdapters(
        youtube=YtDlpSubprocess(browser=youtube_browser),
        ffmpeg=FfmpegSubprocess(),
        ffprobe=FfprobeSubprocess(),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prepared = prepare_media_with_checkpoint(
            source_url,
            input_dir,
            CheckpointedMediaAdapters(
                adapters=adapters,
                contracts=DEFAULT_MEDIA_ADAPTER_CONTRACTS,
            ),
        )
    if len(prepared.frame_paths) != len(prepared.probe.frames):
        raise MediaProbeError(
            argv=("ffprobe", "-show_frames"),
            stderr="saved frame count does not match decoded PTS count",
        )
    frames = tuple(
        PreparedFrameManifest(
            frame_id=FrameId(frame_path.stem),
            frame_index=index,
            raw_pts=probe_frame.pts,
            raw_pts_time=probe_frame.pts_time,
            best_effort_timestamp=probe_frame.best_effort_timestamp,
            best_effort_timestamp_time=probe_frame.best_effort_timestamp_time,
            pts_ms=probe_frame.pts_ms,
            path=str(frame_path.relative_to(paths.run_dir)),
        )
        for index, (frame_path, probe_frame) in enumerate(
            zip(prepared.frame_paths, prepared.probe.frames, strict=True)
        )
    )
    try:
        subtitle_inventory = build_policy_bound_inventory(
            paths.run_dir,
            prepared.subtitle_tracks,
        )
    except SubtitleAdmissionError as error:
        raise MediaProbeError(
            argv=("transcription-cli", "prepare"),
            stderr=str(error),
        ) from error
    manifest = PreparedMediaManifest(
        run_id=run_id,
        source_url=source_url,
        workflow_status=state.workflow_status,
        video_path=str(prepared.video_path),
        audio_path=str(prepared.audio_path),
        subtitle_tracks=prepared.subtitle_tracks,
        subtitle_inventory=subtitle_inventory,
        duration_ms=prepared.probe.duration_ms,
        frame_count=len(frames),
        frames=frames,
        warnings=tuple(str(item.message) for item in caught),
    )
    published = publish_media_manifest(paths.media_manifest, manifest)
    try:
        publish_policy_sidecars(store, run_id, subtitle_inventory)
    except VisualSidecarPublicationError:
        _ = revoke_policy_dependents(store, run_id)
        publish_policy_sidecars(store, run_id, subtitle_inventory)
    return published


def transcribe_media_run(
    run_id: RunId,
    store: RunStore,
) -> AsrRunArtifact:
    """Run the pinned local ASR model against prepared audio."""
    paths = store.paths(run_id)
    state = store.load_state(run_id)
    if state.context is None:
        detail = "episode context is required before local ASR"
        raise AsrBackendError(detail=detail)
    manifest = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    vad_artifact = ensure_vad_evidence(store, run_id)
    transcript = load_mlx_whisper().transcribe(
        Path(manifest.audio_path),
        DEFAULT_ASR_MODEL,
    )
    artifact = AsrRunArtifact(
        run_id=run_id,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=vad_artifact.policy_digest,
        audio_sha256=vad_artifact.audio_sha256,
        transcript=transcript,
    )
    write_model_atomic(paths.asr_transcript, artifact)
    store.save_state(
        RunState(
            run_id=state.run_id,
            source_url=state.source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            validation_status=ValidationStatus.PENDING,
            context=state.context,
        )
    )
    return artifact


def apply_vision_run(
    run_id: RunId,
    decisions_path: Path,
    store: RunStore,
) -> VisionAppliedArtifact:
    """Apply manifest-backed agent vision decisions to one transcript."""
    bundle = VisionDecisionBundle.model_validate_json(
        decisions_path.read_text(encoding="utf-8")
    )
    transcript = store.load_transcript(run_id)
    updated, artifact = apply_vision_decisions(transcript, bundle)
    store.save_transcript(updated)
    write_model_atomic(store.paths(run_id).vision_artifact, artifact)
    return artifact
