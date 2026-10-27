# Copyright 2026 Transcription Module contributors

"""Prepared-media manifest authority follows checkpoint repair atomically."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Final, Never, override

import pytest

import transcription_module.media_manifest_authority as manifest_authority
from tests.integration.test_media_resume import CONTRACTS, SOURCE, FakeMediaAdapters
from transcription_module import stages
from transcription_module.confirmation_models import (
    ConfirmationSession,
    ConfirmationSessionId,
    ConfirmationSessionStatus,
)
from transcription_module.media import (
    AudioExtractionError,
    BrowserCookieSource,
    FrameExtractionError,
    MediaDownloadError,
    MediaToolError,
)
from transcription_module.media_checkpoint import (
    CheckpointPublicationError,
    CheckpointStorageError,
    MediaCheckpointStore,
    StageCompletion,
    StageName,
)
from transcription_module.media_manifest_authority import ManifestWithdrawalPoint
from transcription_module.models import (
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.policy_invalidation import (
    POLICY_DEPENDENT_DIRECTORY_NAMES,
    policy_dependent_authority_paths,
)
from transcription_module.storage import RunStore
from transcription_module.subtitle_inventory import SubtitleInventory
from transcription_module.visual_evidence_policy import (
    canonical_policy_bytes,
    default_evidence_policy,
)

if TYPE_CHECKING:
    from pathlib import Path

RUN_ID: Final = RunId("80000000-0000-4000-8000-000000000018")


class WithdrawalInterruptedError(OSError):
    """Injected durable manifest withdrawal interruption."""

    @override
    def __str__(self) -> str:
        return "injected withdrawal interruption"


def _prepared_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RunStore, FakeMediaAdapters]:
    store = RunStore(tmp_path / "runs")
    _ = store.create(
        RunState(
            run_id=RUN_ID,
            source_url=SOURCE,
            workflow_status=WorkflowStatus.PROCESSING,
        )
    )
    fake = FakeMediaAdapters()

    def fake_youtube(*, browser: BrowserCookieSource) -> FakeMediaAdapters:
        del browser
        return fake

    monkeypatch.setattr(stages, "YtDlpSubprocess", fake_youtube)
    monkeypatch.setattr(stages, "FfmpegSubprocess", lambda: fake)
    monkeypatch.setattr(stages, "FfprobeSubprocess", lambda: fake)
    monkeypatch.setattr(stages, "DEFAULT_MEDIA_ADAPTER_CONTRACTS", CONTRACTS)
    _ = stages.prepare_media_run(SOURCE, store, RUN_ID)
    return store, fake


def _seed_policy_dependent_authority(
    store: RunStore,
) -> dict[Path, bytes]:
    """Create one finalized state plus every exact downstream authority file."""
    paths = store.paths(RUN_ID)
    state = store.load_state(RUN_ID)
    store.save_state(
        state.model_copy(
            update={
                "workflow_status": WorkflowStatus.FINALIZED,
                "validation_status": ValidationStatus.VALID,
            }
        )
    )
    for directory_name in POLICY_DEPENDENT_DIRECTORY_NAMES:
        directory = paths.run_dir / directory_name
        nested = directory / ("requests" if "trial" in directory_name else "")
        nested.mkdir(parents=True, exist_ok=True)
        _ = (nested / "stale.json").write_bytes(b"stale-directory-authority")
    snapshots: dict[Path, bytes] = {}
    for index, path in enumerate(policy_dependent_authority_paths(paths), start=1):
        path.parent.mkdir(parents=True, exist_ok=True)
        match path.name:
            case "transcript.json":
                payload = (
                    Transcript(
                        run_id=RUN_ID,
                        workflow_status=WorkflowStatus.FINALIZED,
                        validation_status=ValidationStatus.VALID,
                        duration_ms=Milliseconds(1),
                        cues=(),
                        vision_evidence=(),
                    )
                    .model_dump_json()
                    .encode()
                )
            case "confirmation-session.json":
                payload = (
                    ConfirmationSession(
                        session_id=ConfirmationSessionId("policy-revocation-session"),
                        status=ConfirmationSessionStatus.COMPLETED,
                    )
                    .model_dump_json()
                    .encode()
                )
            case "confirmations.jsonl":
                payload = b""
            case _:
                payload = f"policy-dependent-{index}:{path.name}".encode()
        _ = path.write_bytes(payload)
        snapshots[path] = payload
    return snapshots


def _checkpoint_hashes(root: Path) -> dict[StageName, tuple[str, ...]]:
    """Return stage output hashes from one complete checkpoint."""
    checkpoint = MediaCheckpointStore(root).load()
    receipts = {
        StageName.VIDEO: checkpoint.video,
        StageName.SUBTITLES: checkpoint.subtitles,
        StageName.AUDIO: checkpoint.audio,
        StageName.PROBE: checkpoint.probe,
        StageName.FRAMES: checkpoint.frames,
    }
    hashes: dict[StageName, tuple[str, ...]] = {}
    for stage, receipt in receipts.items():
        assert receipt is not None
        hashes[stage] = tuple(output.sha256 for output in receipt.outputs)
    return hashes


def _seed_policy_independent_evidence(store: RunStore) -> dict[Path, bytes]:
    """Create exact ASR, diarization, and protected-run sentinels."""
    run_dir = store.paths(RUN_ID).run_dir
    snapshots: dict[Path, bytes] = {}
    for name in ("asr.json", "diarization.json", "protected-run.json"):
        path = run_dir / name
        payload = f"preserved:{name}".encode()
        _ = path.write_bytes(payload)
        snapshots[path] = payload
    return snapshots


@pytest.mark.parametrize(
    ("stage", "expected_error"),
    [
        (StageName.VIDEO, MediaDownloadError),
        (StageName.AUDIO, AudioExtractionError),
        (StageName.FRAMES, FrameExtractionError),
    ],
)
def test_failed_required_repair_withdraws_prior_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: StageName,
    expected_error: type[MediaToolError],
) -> None:
    """A failed required-stage repair cannot leave stale media authority."""
    # Given
    store, fake = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    _configure_failed_repair(paths.run_dir / "input", fake, stage)

    # When
    with pytest.raises(expected_error, match="failed"):
        _ = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    assert not paths.media_manifest.exists()


def _configure_failed_repair(
    input_dir: Path,
    fake: FakeMediaAdapters,
    stage: StageName,
) -> None:
    repairs = {
        StageName.VIDEO: _configure_failed_video,
        StageName.AUDIO: _configure_failed_audio,
        StageName.FRAMES: _configure_failed_frames,
    }
    repairs[stage](input_dir, fake)


def _configure_failed_video(input_dir: Path, fake: FakeMediaAdapters) -> None:
    _ = (input_dir / "media/video/source.mp4").write_bytes(b"tampered")
    fake.fail_video = True


def _configure_failed_audio(input_dir: Path, fake: FakeMediaAdapters) -> None:
    _ = (input_dir / "media/audio/audio.wav").write_bytes(b"tampered")
    fake.fail_audio = True


def _configure_failed_frames(input_dir: Path, fake: FakeMediaAdapters) -> None:
    _ = (input_dir / "media/frames/f000001.jpg").write_bytes(b"tampered")
    fake.fail_frames = True


def test_corrupt_checkpoint_withdraws_prior_manifest_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unprovable checkpoint reuse revokes prior media authority."""
    # Given
    store, fake = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    checkpoint = paths.run_dir / "input/.media-checkpoint/checkpoint.json"
    _ = checkpoint.write_bytes(b"invalid checkpoint")
    calls = fake.calls.copy()

    # When
    with pytest.raises(CheckpointStorageError, match="invalid media checkpoint"):
        _ = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    assert not paths.media_manifest.exists()
    assert fake.calls == calls


def test_subtitle_checkpoint_failure_keeps_withdrawn_manifest_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale optional output is revoked before failed checkpoint publication."""
    # Given
    store, _ = _prepared_run(tmp_path, monkeypatch)
    downstream = _seed_policy_dependent_authority(store)
    changed = CONTRACTS.model_copy(update={"subtitles": "fixture-subtitles.v2"})
    monkeypatch.setattr(stages, "DEFAULT_MEDIA_ADAPTER_CONTRACTS", changed)

    def fail_publication(
        self: MediaCheckpointStore,
        source_url: YouTubeVideoUrl,
        completion: StageCompletion,
    ) -> Never:
        del self, source_url, completion
        raise CheckpointPublicationError(StageName.SUBTITLES, "injected")

    monkeypatch.setattr(MediaCheckpointStore, "publish_subtitles", fail_publication)

    # When
    with pytest.raises(CheckpointPublicationError, match="injected"):
        _ = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    paths = store.paths(RUN_ID)
    assert not paths.media_manifest.exists()
    assert all(not path.exists() for path in downstream)
    state = store.load_state(RUN_ID)
    assert state.workflow_status is WorkflowStatus.PROCESSING
    assert state.validation_status is ValidationStatus.PENDING


@pytest.mark.parametrize(
    "point",
    [
        ManifestWithdrawalPoint.BEFORE_UNLINK,
        ManifestWithdrawalPoint.AFTER_UNLINK,
    ],
)
def test_withdrawal_interruption_aborts_before_checkpoint_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: ManifestWithdrawalPoint,
) -> None:
    """Both withdrawal edges abort repair with their durable safe state."""
    # Given
    store, fake = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    manifest_bytes = paths.media_manifest.read_bytes()
    calls = fake.calls.copy()
    changed = CONTRACTS.model_copy(update={"audio": "fixture-audio.v2"})
    monkeypatch.setattr(stages, "DEFAULT_MEDIA_ADAPTER_CONTRACTS", changed)

    def interrupt(observed: ManifestWithdrawalPoint) -> None:
        matches_injected_point = observed is point
        if matches_injected_point:
            raise WithdrawalInterruptedError

    monkeypatch.setattr(manifest_authority, "manifest_withdrawal_fault", interrupt)

    # When
    with pytest.raises(OSError, match="injected withdrawal"):
        _ = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    manifest_remains = {
        ManifestWithdrawalPoint.BEFORE_UNLINK: True,
        ManifestWithdrawalPoint.AFTER_UNLINK: False,
    }[point]
    assert paths.media_manifest.exists() is manifest_remains
    if manifest_remains:
        assert paths.media_manifest.read_bytes() == manifest_bytes
    assert fake.calls == calls
    assert MediaCheckpointStore(paths.run_dir / "input").validate(
        SOURCE, CONTRACTS
    ).valid_stages == tuple(StageName)


def test_successful_repairs_republish_and_replay_preserves_manifest_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair republishes once while a valid replay leaves exact bytes untouched."""
    # Given
    store, fake = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    prior_inode = paths.media_manifest.stat().st_ino
    _ = (paths.run_dir / "input/media/audio/audio.wav").write_bytes(b"tampered")

    # When
    repaired = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    repaired_bytes = paths.media_manifest.read_bytes()
    repaired_inode = paths.media_manifest.stat().st_ino
    assert repaired_inode != prior_inode
    assert repaired.audio_path.endswith("audio.wav")
    calls = fake.calls.copy()

    # When
    replayed = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    assert replayed == repaired
    assert paths.media_manifest.read_bytes() == repaired_bytes
    assert paths.media_manifest.stat().st_ino == repaired_inode
    assert fake.calls == calls


def test_stale_subtitle_repair_republishes_terminal_optional_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional unavailability replaces a manifest that referenced stale tracks."""
    # Given
    store, fake = _prepared_run(tmp_path, monkeypatch)
    changed = CONTRACTS.model_copy(update={"subtitles": "fixture-subtitles.v2"})
    monkeypatch.setattr(stages, "DEFAULT_MEDIA_ADAPTER_CONTRACTS", changed)
    fake.subtitles_available = False

    # When
    manifest = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    assert manifest.subtitle_tracks == ()
    assert manifest.warnings == ("no Korean subtitle was available",)
    paths = store.paths(RUN_ID)
    inventory = SubtitleInventory.model_validate_json(
        paths.subtitle_inventory_artifact.read_bytes()
    )
    assert inventory.eligible_tracks == ()
    assert paths.visual_policy_artifact.is_file()
    assert paths.media_manifest.is_file()


def test_stale_policy_sidecar_is_invalidated_without_rerunning_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale policy byte revokes descendants and republishes current inputs."""
    # Given
    store, fake = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    _ = paths.visual_policy_artifact.write_bytes(
        paths.visual_policy_artifact.read_bytes() + b" "
    )
    calls = fake.calls.copy()

    # When
    _ = stages.prepare_media_run(SOURCE, store, RUN_ID)

    # Then
    assert fake.calls == calls
    assert paths.visual_policy_artifact.read_bytes() == (
        canonical_policy_bytes(default_evidence_policy()) + b"\n"
    )
    assert paths.subtitle_inventory_artifact.is_file()


def test_policy_change_revokes_downstream_and_preserves_independent_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subtitle-policy repair withdraws descendants but reuses other media."""
    store, fake = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    downstream = _seed_policy_dependent_authority(store)
    independent = _seed_policy_independent_evidence(store)
    before_hashes = _checkpoint_hashes(paths.run_dir / "input")
    calls = fake.calls.copy()
    changed = CONTRACTS.model_copy(update={"subtitles": "fixture-subtitles.v2"})
    monkeypatch.setattr(stages, "DEFAULT_MEDIA_ADAPTER_CONTRACTS", changed)

    manifest = stages.prepare_media_run(SOURCE, store, RUN_ID)

    assert fake.calls == calls + Counter({"subtitles": 1})
    after_hashes = _checkpoint_hashes(paths.run_dir / "input")
    for stage in (
        StageName.VIDEO,
        StageName.AUDIO,
        StageName.PROBE,
        StageName.FRAMES,
    ):
        assert after_hashes[stage] == before_hashes[stage]
    recreated = {
        paths.visual_policy_artifact,
        paths.subtitle_inventory_artifact,
    }
    assert all(not path.exists() for path in downstream if path not in recreated)
    assert all(path.is_file() for path in recreated)
    assert all(path.read_bytes() == payload for path, payload in independent.items())
    assert manifest.workflow_status is WorkflowStatus.PROCESSING
    assert paths.media_manifest.is_file()
    state = store.load_state(RUN_ID)
    assert state.workflow_status is WorkflowStatus.PROCESSING
    assert state.validation_status is ValidationStatus.PENDING


def test_checkpoint_validation_read_does_not_mutate_completed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading a completed legacy checkpoint leaves every run byte untouched."""
    store, _ = _prepared_run(tmp_path, monkeypatch)
    paths = store.paths(RUN_ID)
    _ = _seed_policy_dependent_authority(store)
    _ = _seed_policy_independent_evidence(store)
    before = {
        path.relative_to(paths.run_dir).as_posix(): path.read_bytes()
        for path in paths.run_dir.rglob("*")
        if path.is_file()
    }

    validation = MediaCheckpointStore(paths.run_dir / "input").validate(
        SOURCE,
        CONTRACTS,
    )

    after = {
        path.relative_to(paths.run_dir).as_posix(): path.read_bytes()
        for path in paths.run_dir.rglob("*")
        if path.is_file()
    }
    assert validation.valid_stages == tuple(StageName)
    assert after == before
