# Copyright 2026 Transcription Module contributors

"""Real persisted policy-window fixture shared by drafting integration tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from transcription_module._drafting_models import (
    DiarizationRunArtifact,
    DiarizationStatus,
    SpeakerEvidenceSegment,
    WindowDraft,
    WindowDraftInput,
)
from transcription_module.asr import AsrRunArtifact, AsrSegment, AsrTranscript
from transcription_module.media import (
    PreparedFrameManifest,
    PreparedMediaManifest,
    PreparedSubtitleTrack,
    SubtitleSource,
)
from transcription_module.models import (
    EpisodeContext,
    FrameId,
    Milliseconds,
    RunId,
    RunState,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.recovery import VadSpeechInterval
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.subtitle_inventory import (
    SubtitleInventory,
    SubtitleTrackIdentity,
)
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    default_evidence_policy,
    policy_digest,
)

RUN_ID: Final = RunId("policy-window-run")
SOURCE_URL: Final = YouTubeVideoUrl("https://youtube.test/policy-window")
POLICY_SHA: Final = policy_digest(default_evidence_policy())

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class PolicyWindowFixture:
    """One complete policy-bound source surface and its expected identities."""

    store: RunStore
    run_id: RunId
    vtt: Path


def create_policy_window_fixture(
    root: Path,
    duration_ms: int = 5_000,
) -> PolicyWindowFixture:
    """Persist exact media, ASR, VAD, subtitle, and frame evidence."""
    store = RunStore(root / "runs")
    paths = store.create(
        RunState(
            run_id=RUN_ID,
            source_url=SOURCE_URL,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    input_dir = paths.run_dir / "input"
    frames_dir = input_dir / "frames"
    frames_dir.mkdir(parents=True)
    video = input_dir / "source.mp4"
    audio = input_dir / "audio.wav"
    vtt = input_dir / "automatic.ko.ko-orig.vtt"
    _ = video.write_bytes(b"video")
    _ = audio.write_bytes(b"audio")
    vtt_payload = """WEBVTT

00:01.000 --> 00:01.800
서울에서 다시 만났어요

00:03.000 --> 00:03.800
동시에 대답해요

00:03.000 --> 00:03.800
동시에 대답해요
"""
    _ = vtt.write_text(vtt_payload, encoding="utf-8")
    frame_specs = (("frame-1", 1_200), ("frame-2", 3_200), ("frame-3", 3_400))
    frames = tuple(
        _frame(paths.run_dir, frames_dir, frame_id, index, pts_ms)
        for index, (frame_id, pts_ms) in enumerate(frame_specs)
    )
    subtitle_sha = hashlib.sha256(vtt.read_bytes()).hexdigest()
    audio_sha = hashlib.sha256(audio.read_bytes()).hexdigest()
    inventory = SubtitleInventory(
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        tracks=(
            SubtitleTrackIdentity(
                track_id="automatic-ko-orig",
                variant="ko-orig",
                source="automatic",
                path=vtt.relative_to(paths.run_dir).as_posix(),
                sha256=subtitle_sha,
                eligible_for_speech=True,
            ),
        ),
    )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=RUN_ID,
            source_url=SOURCE_URL,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(video),
            audio_path=str(audio),
            subtitle_tracks=(
                PreparedSubtitleTrack(
                    path=str(vtt),
                    language="ko",
                    source=SubtitleSource.AUTOMATIC,
                    track_id="automatic-ko-orig",
                    variant="ko-orig",
                    sha256=subtitle_sha,
                    eligible_for_speech=True,
                ),
            ),
            subtitle_inventory=inventory,
            duration_ms=duration_ms,
            frame_count=len(frames),
            frames=frames,
        ),
    )
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=RUN_ID,
            era=EvidenceEra.POLICY_BOUND,
            policy_digest=POLICY_SHA,
            audio_sha256=audio_sha,
            transcript=AsrTranscript(
                text="서울에서 다시 만났어 동시에 대답해요",
                language="ko",
                segments=(
                    AsrSegment(
                        source_ordinal=1,
                        start_ms=1_000,
                        end_ms=1_800,
                        speech="서울에서 다시 만났어",
                    ),
                    AsrSegment(
                        source_ordinal=2,
                        start_ms=1_000,
                        end_ms=1_800,
                        speech="시간만 겹치는 무관한 말",
                    ),
                    AsrSegment(
                        source_ordinal=3,
                        start_ms=3_000,
                        end_ms=3_800,
                        speech="동시에 대답해요",
                    ),
                    AsrSegment(
                        source_ordinal=4,
                        start_ms=3_000,
                        end_ms=3_800,
                        speech="동시에 대답해요",
                    ),
                ),
            ),
        ),
    )
    write_model_atomic(
        paths.vad_artifact,
        VadEvidenceArtifact(
            run_id=RUN_ID,
            policy_digest=POLICY_SHA,
            audio_sha256=audio_sha,
            ffmpeg_filter="silencedetect=noise=-35dB:d=0.20",
            duration_ms=duration_ms,
            speech_intervals=(
                VadSpeechInterval(
                    start_tc_ms=Milliseconds(900),
                    end_tc_ms=Milliseconds(1_900),
                ),
                VadSpeechInterval(
                    start_tc_ms=Milliseconds(2_900),
                    end_tc_ms=Milliseconds(3_900),
                ),
            ),
        ),
    )
    diarization = DiarizationRunArtifact(
        run_id=RUN_ID,
        status=DiarizationStatus.AVAILABLE,
        segments=(
            SpeakerEvidenceSegment(
                start_tc_ms=1_000,
                end_tc_ms=1_800,
                cluster="SPEAKER_00",
            ),
            SpeakerEvidenceSegment(
                start_tc_ms=3_000,
                end_tc_ms=3_800,
                cluster="SPEAKER_00",
            ),
            SpeakerEvidenceSegment(
                start_tc_ms=3_000,
                end_tc_ms=3_800,
                cluster="SPEAKER_01",
            ),
        ),
    )
    write_model_atomic(paths.diarization_artifact, diarization)
    return PolicyWindowFixture(store=store, run_id=RUN_ID, vtt=vtt)


def empty_policy_draft(data: WindowDraftInput) -> WindowDraft:
    """Return an empty result carrying exactly the supplied authority identity."""
    return WindowDraft(
        window_index=data.window_index,
        cues=(),
        unresolved=(),
        era=data.era,
        policy_digest=data.policy_digest,
        inventory_digest=data.inventory_digest,
        source_digest=data.source_digest,
    )


def _frame(
    run_dir: Path,
    frames_dir: Path,
    frame_id: str,
    index: int,
    pts_ms: int,
) -> PreparedFrameManifest:
    path = frames_dir / f"{frame_id}.png"
    _ = path.write_bytes(frame_id.encode())
    relative = path.relative_to(run_dir).as_posix()
    return PreparedFrameManifest(
        frame_id=FrameId(frame_id),
        frame_index=index,
        raw_pts=pts_ms,
        raw_pts_time=f"{pts_ms / 1_000:.3f}",
        best_effort_timestamp=pts_ms,
        best_effort_timestamp_time=f"{pts_ms / 1_000:.3f}",
        pts_ms=pts_ms,
        path=relative,
    )


__all__ = [
    "POLICY_SHA",
    "PolicyWindowFixture",
    "create_policy_window_fixture",
    "empty_policy_draft",
]
