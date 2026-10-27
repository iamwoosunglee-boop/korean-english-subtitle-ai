# Copyright 2026 Transcription Module contributors

"""Public builder and persisted boundary for verified speaker turns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from transcription_module._drafting_models import DiarizationRunArtifact
from transcription_module.artifact_io import read_confined_bytes
from transcription_module.asr import AsrRunArtifact
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import write_model_atomic
from transcription_module.subtitle_admission import admitted_subtitle_tracks
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.window_evidence_errors import fail_window_evidence
from transcription_module.window_evidence_inputs import (
    WindowSourceAuthority,
    window_authority_source_digest,
)
from transcription_module.window_evidence_models import (
    LegacyVerifiedSpeakerTurnArtifact,
    VerifiedSpeakerTurnArtifact,
)
from transcription_module.window_source_validation import (
    PersistedWindowSources,
    validate_persisted_window_sources,
)
from transcription_module.window_speaker_authority_models import (
    SpeakerTurnReplayContext,
    canonical_speaker_turn_authority_bytes,
    speaker_turn_authority_digest,
)
from transcription_module.window_speaker_authority_replay import (
    expected_verified_speaker_turn_artifact,
)

if TYPE_CHECKING:
    from transcription_module.models import RunId
    from transcription_module.storage import RunStore
    from transcription_module.window_speaker_authority_models import (
        SpeakerTurnEvidenceSnapshot,
    )


def replay_verified_speaker_turn_artifact(
    context: SpeakerTurnReplayContext,
    stored_payload: bytes | None,
    evidence: SpeakerTurnEvidenceSnapshot | None = None,
) -> VerifiedSpeakerTurnArtifact | None:
    """Require exact derived bytes to replay one admitted independent authority."""
    expected = expected_verified_speaker_turn_artifact(context, evidence)
    if expected is None:
        if stored_payload is not None:
            fail_window_evidence(
                "verified speaker-turn authority artifact is unadmitted"
            )
        return None
    if stored_payload is None:
        fail_window_evidence("admitted speaker-turn authority lacks derived artifact")
    try:
        stored = VerifiedSpeakerTurnArtifact.model_validate_json(stored_payload)
    except ValidationError:
        try:
            _ = LegacyVerifiedSpeakerTurnArtifact.model_validate_json(stored_payload)
        except ValidationError:
            fail_window_evidence(
                "verified speaker-turn authority artifact is malformed"
            )
        fail_window_evidence(
            "legacy verified speaker-turn assertion grants no policy authority"
        )
    if stored != expected or stored_payload != _verified_payload(expected):
        fail_window_evidence(
            "verified speaker-turn authority artifact differs from admitted authority"
        )
    return expected


def build_verified_speaker_turn_artifact(
    store: RunStore,
    run_id: RunId,
) -> VerifiedSpeakerTurnArtifact:
    """Build only from current inventory-admitted files; accept no caller proof."""
    with store.lock_run(run_id):
        context = load_speaker_turn_replay_context(store, run_id)
        expected = expected_verified_speaker_turn_artifact(context)
        if expected is None:
            fail_window_evidence("speaker-turn authority is not admitted")
        path = store.paths(run_id).verified_speaker_turns_artifact
        try:
            existing = read_confined_bytes(path, missing_ok=True)
        except OSError:
            fail_window_evidence("verified speaker-turn authority path is unsafe")
        if existing is not None and existing != _verified_payload(expected):
            fail_window_evidence(
                "verified speaker-turn authority artifact conflicts with authority"
            )
        if existing is None:
            write_model_atomic(path, expected)
        return expected


def load_speaker_turn_replay_context(
    store: RunStore,
    run_id: RunId,
) -> SpeakerTurnReplayContext:
    """Load and validate every source used by authority admission and replay."""
    paths = store.paths(run_id)
    try:
        media = PreparedMediaManifest.model_validate_json(
            paths.media_manifest.read_bytes()
        )
        asr = AsrRunArtifact.model_validate_json(paths.asr_transcript.read_bytes())
        vad = VadEvidenceArtifact.model_validate_json(paths.vad_artifact.read_bytes())
        diarization = (
            DiarizationRunArtifact.model_validate_json(
                paths.diarization_artifact.read_bytes()
            )
            if paths.diarization_artifact.exists()
            else None
        )
    except (OSError, ValidationError):
        fail_window_evidence("speaker-turn builder source is malformed")
    inventory = media.subtitle_inventory
    current_policy = policy_digest(default_evidence_policy())
    if (
        inventory is None
        or inventory.policy_digest != current_policy
        or asr.era is not EvidenceEra.POLICY_BOUND
        or asr.policy_digest != current_policy
        or vad.policy_digest != current_policy
    ):
        fail_window_evidence("speaker-turn builder lacks current policy authority")
    _ = admitted_subtitle_tracks(media, paths.run_dir)
    validate_persisted_window_sources(
        PersistedWindowSources(run_id, paths.run_dir, media, asr, vad, diarization)
    )
    source_digest = window_authority_source_digest(
        WindowSourceAuthority(media, asr, vad, diarization, None)
    )
    return SpeakerTurnReplayContext(
        paths.run_dir,
        run_id,
        current_policy,
        source_digest,
        media.duration_ms,
        tuple((str(item.frame_id), item.pts_ms) for item in media.frames),
        tuple(
            (f"vad:{index}", int(item.start_tc_ms), int(item.end_tc_ms))
            for index, item in enumerate(vad.speech_intervals, start=1)
        ),
        inventory,
        diarization,
    )


def _verified_payload(artifact: VerifiedSpeakerTurnArtifact) -> bytes:
    return f"{artifact.model_dump_json(indent=2)}\n".encode()


__all__ = [
    "build_verified_speaker_turn_artifact",
    "canonical_speaker_turn_authority_bytes",
    "load_speaker_turn_replay_context",
    "replay_verified_speaker_turn_artifact",
    "speaker_turn_authority_digest",
]
