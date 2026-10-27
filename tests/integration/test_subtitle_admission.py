# Copyright 2026 Transcription Module contributors

"""Exact ko-orig admission is rechecked at every speech-evidence boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest
from pydantic import ValidationError

from tests.integration.final_review_execution_support import (
    create_run,
    execute,
    semantic_result,
)
from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.asr import AsrRunArtifact, AsrTranscript
from transcription_module.correction_receipts import CorrectionReceiptSet
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.final_review_execution_models import (
    FinalReviewExecutionError,
)
from transcription_module.media import (
    PreparedMediaManifest,
    PreparedSubtitleTrack,
    SubtitleSource,
)
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.subtitle_admission import (
    SubtitleAdmissionError,
    admitted_subtitle_tracks,
    validate_prepared_subtitle_tracks,
)
from transcription_module.subtitle_inventory import (
    SubtitleInventory,
    SubtitleTrackIdentity,
)
from transcription_module.visual_evidence_policy import (
    EvidenceEra,
    canonical_policy_bytes,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_observations import VisualObservationSet

if TYPE_CHECKING:
    from transcription_module.final_review_execution_models import (
        FinalReviewSegmentInput,
    )
    from transcription_module.final_review_models import ReviewSegmentResult

POLICY_SHA = policy_digest(default_evidence_policy())
STALE_POLICY_SHA = "b" * 64


def _write_empty_final_review_sidecars(
    store: RunStore,
    run_id: RunId,
    inventory: SubtitleInventory | None,
) -> None:
    if inventory is None:
        message = "policy-bound fixture lacks subtitle inventory"
        raise AssertionError(message)
    paths = store.paths(run_id)
    _ = paths.visual_policy_artifact.write_bytes(
        canonical_policy_bytes(default_evidence_policy()) + b"\n"
    )
    write_model_atomic(paths.subtitle_inventory_artifact, inventory)
    write_model_atomic(
        paths.visual_observations_artifact,
        VisualObservationSet(
            run_id=run_id,
            era=EvidenceEra.POLICY_BOUND,
            policy_digest=POLICY_SHA,
            observations=(),
        ),
    )
    write_model_atomic(
        paths.correction_receipts_artifact,
        CorrectionReceiptSet(
            run_id=run_id,
            era=EvidenceEra.POLICY_BOUND,
            policy_digest=POLICY_SHA,
            receipts=(),
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _track(path: Path, digest: str) -> PreparedSubtitleTrack:
    return PreparedSubtitleTrack(
        path=str(path),
        language="ko",
        source=SubtitleSource.AUTOMATIC,
        track_id="automatic-ko-orig",
        variant="ko-orig",
        sha256=digest,
        eligible_for_speech=True,
    )


def _inventory(
    relative_path: str,
    digest: str,
    *,
    policy_sha: str = POLICY_SHA,
    with_duplicate: bool = False,
) -> SubtitleInventory:
    tracks = [
        SubtitleTrackIdentity(
            track_id="automatic-ko-orig",
            variant="ko-orig",
            source="automatic",
            path=relative_path,
            sha256=digest,
            eligible_for_speech=True,
        )
    ]
    if with_duplicate:
        tracks.append(
            SubtitleTrackIdentity(
                track_id="automatic-ko-en-duplicate",
                variant="ko-en",
                source="automatic",
                path="input/media/subtitles/automatic.ko-en.vtt",
                sha256=digest,
                eligible_for_speech=False,
                duplicate_of="automatic-ko-orig",
            )
        )
    return SubtitleInventory(
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=policy_sha,
        tracks=tuple(tracks),
    )


def _manifest(
    run_id: RunId,
    source_url: YouTubeVideoUrl,
    run_dir: Path,
    *,
    with_duplicate: bool = False,
    policy_sha: str = POLICY_SHA,
) -> tuple[PreparedMediaManifest, Path]:
    relative = Path("input/media/subtitles/automatic.ko.ko-orig.vtt")
    vtt = run_dir / relative
    vtt.parent.mkdir(parents=True, exist_ok=True)
    _ = vtt.write_text("WEBVTT\n\n00:00.000 --> 00:00.500\n정확한 자막\n")
    digest = _sha256(vtt)
    if with_duplicate:
        duplicate = run_dir / "input/media/subtitles/automatic.ko-en.vtt"
        _ = duplicate.write_bytes(vtt.read_bytes())
    return (
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path=str(run_dir / "input/media/video/source.mp4"),
            audio_path=str(run_dir / "input/media/audio/audio.wav"),
            subtitle_tracks=(_track(vtt, digest),),
            subtitle_inventory=_inventory(
                relative.as_posix(),
                digest,
                policy_sha=policy_sha,
                with_duplicate=with_duplicate,
            ),
            duration_ms=1_000,
            frame_count=0,
        ),
        vtt,
    )


def test_admission_rejects_every_non_exact_or_untrusted_track(
    tmp_path: Path,
) -> None:
    """Derived, manual, unknown, spoofed, and duplicate tracks stay rejected."""
    cases = (
        (
            "automatic.ko.ko.vtt",
            "ko",
            SubtitleSource.AUTOMATIC,
            "automatic-ko-orig",
            None,
        ),
        (
            "automatic.ko.ko-en.vtt",
            "ko-en",
            SubtitleSource.AUTOMATIC,
            "automatic-ko-orig",
            None,
        ),
        (
            "automatic.ko.ko-orig.vtt",
            "ko-orig",
            SubtitleSource.MANUAL,
            "automatic-ko-orig",
            None,
        ),
        (
            "automatic.ko.ko-orig.vtt",
            "ko-en",
            SubtitleSource.AUTOMATIC,
            "automatic-ko-orig",
            None,
        ),
        (
            "automatic.ko.ko-orig.vtt",
            "unknown",
            SubtitleSource.AUTOMATIC,
            "automatic-ko-orig",
            None,
        ),
        (
            "spoofed.ko.ko-orig.vtt",
            "ko-orig",
            SubtitleSource.AUTOMATIC,
            "automatic-ko-orig",
            None,
        ),
        (
            "automatic.ko.ko-orig.vtt",
            "ko-orig",
            SubtitleSource.AUTOMATIC,
            "automatic-ko-orig",
            "automatic-ko-orig",
        ),
    )
    for index, (filename, variant, source, track_id, duplicate_of) in enumerate(cases):
        path = tmp_path / str(index) / filename
        path.parent.mkdir()
        _ = path.write_text("WEBVTT\n", encoding="utf-8")
        track = PreparedSubtitleTrack(
            path=str(path),
            language="ko",
            source=source,
            track_id=track_id,
            variant=variant,
            sha256=_sha256(path),
            eligible_for_speech=True,
            duplicate_of=duplicate_of,
        )

        with pytest.raises(SubtitleAdmissionError):
            _ = validate_prepared_subtitle_tracks((track,))


def test_admission_accepts_genuine_yt_dlp_automatic_ko_orig_filename(
    tmp_path: Path,
) -> None:
    """The exact genuine yt-dlp ko-orig basename crosses admission."""
    path = tmp_path / "automatic.ko.ko-orig.vtt"
    _ = path.write_text("WEBVTT\n", encoding="utf-8")
    expected_sha256 = _sha256(path)
    track = PreparedSubtitleTrack(
        path=str(path),
        language="ko",
        source=SubtitleSource.AUTOMATIC,
        track_id="automatic-ko-orig",
        variant="ko-orig",
        sha256=expected_sha256,
        eligible_for_speech=True,
    )

    admitted = validate_prepared_subtitle_tracks((track,))

    assert admitted[0].variant == "ko-orig"
    assert admitted[0].sha256 == expected_sha256


def test_policy_bound_manifest_rejects_unadmitted_speech_track(tmp_path: Path) -> None:
    """A manual or derived track cannot occupy the manifest speech-track list."""
    run_id = RunId("subtitle-manifest-rejection")
    source_url = YouTubeVideoUrl("https://youtube.test/subtitle-manifest")
    run_dir = tmp_path / "run"
    manifest, vtt = _manifest(run_id, source_url, run_dir)
    rejected = manifest.subtitle_tracks[0].model_copy(
        update={
            "path": str(run_dir / "input/media/subtitles/automatic.ko-en.vtt"),
            "source": SubtitleSource.MANUAL,
            "variant": "ko-en",
        }
    )

    payload = manifest.model_dump(mode="json")
    payload["subtitle_tracks"] = [rejected.model_dump(mode="json")]
    with pytest.raises(ValidationError, match="eligible subtitle track"):
        _ = PreparedMediaManifest.model_validate(payload)

    assert vtt.is_file()


def test_policy_bound_manifest_rejects_alternate_track_id_before_consumers(
    tmp_path: Path,
) -> None:
    """Mutual ID agreement cannot replace the canonical automatic track ID."""
    manifest, _ = _manifest(
        RunId("subtitle-alternate-id"),
        YouTubeVideoUrl("https://youtube.test/subtitle-alternate-id"),
        tmp_path / "run",
    )
    payload = manifest.model_dump(mode="json")
    payload["subtitle_tracks"][0]["track_id"] = "alternate-id"
    payload["subtitle_inventory"]["tracks"][0]["track_id"] = "alternate-id"

    with pytest.raises(ValidationError, match="automatic-ko-orig"):
        _ = PreparedMediaManifest.model_validate(payload)


def test_admission_rechecks_policy_path_and_exact_bytes(tmp_path: Path) -> None:
    """Current policy, confined identity path, and SHA-256 all gate admission."""
    run_id = RunId("subtitle-admission-integrity")
    source_url = YouTubeVideoUrl("https://youtube.test/subtitle-integrity")
    run_dir = tmp_path / "run"
    manifest, vtt = _manifest(run_id, source_url, run_dir)

    assert admitted_subtitle_tracks(manifest, run_dir) == manifest.subtitle_tracks

    _ = vtt.write_text("WEBVTT\ntampered\n")
    with pytest.raises(SubtitleAdmissionError, match="digest"):
        _ = admitted_subtitle_tracks(manifest, run_dir)

    stale, _ = _manifest(
        run_id,
        source_url,
        run_dir,
        policy_sha=STALE_POLICY_SHA,
    )
    with pytest.raises(SubtitleAdmissionError, match="policy"):
        _ = admitted_subtitle_tracks(stale, run_dir)


def test_window_drafting_receives_exact_track_once_and_blocks_duplicate(
    tmp_path: Path,
) -> None:
    """The window boundary exposes only the one eligible ko-orig identity."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    manifest = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    relative = fixture.vtt.relative_to(paths.run_dir).as_posix()
    duplicate = paths.run_dir / "input/media/subtitles/automatic.ko-en.vtt"
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    _ = duplicate.write_bytes(fixture.vtt.read_bytes())
    write_model_atomic(
        paths.media_manifest,
        manifest.model_copy(
            update={
                "subtitle_inventory": _inventory(
                    relative,
                    _sha256(fixture.vtt),
                    with_duplicate=True,
                )
            }
        ),
    )
    observed: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        observed.append(data)
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert len(observed) == 1
    assert tuple(track.variant for track in observed[0].subtitle_tracks) == ("ko-orig",)
    assert tuple(track.track_id for track in observed[0].subtitle_tracks) == (
        "automatic-ko-orig",
    )


def test_window_drafting_rejects_tampered_admitted_track(tmp_path: Path) -> None:
    """A changed VTT never reaches a drafter through stale manifest metadata."""
    store = RunStore(tmp_path / "runs")
    run_id = RunId("subtitle-window-tamper")
    source_url = YouTubeVideoUrl("https://youtube.test/subtitle-window-tamper")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    manifest, vtt = _manifest(run_id, source_url, paths.run_dir)
    write_model_atomic(paths.media_manifest, manifest)
    write_model_atomic(
        paths.asr_transcript,
        AsrRunArtifact(
            run_id=run_id,
            transcript=AsrTranscript(text="", language="ko", segments=()),
        ),
    )
    _ = vtt.write_text("WEBVTT\nstale bytes\n")

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        del data
        raise AssertionError

    with pytest.raises(SubtitleAdmissionError, match="digest"):
        _ = anyio.run(
            create_window_drafts,
            store,
            run_id,
            EmptyVisualReviewer(),
            drafter,
        )


def test_final_review_receives_only_exact_admitted_ko_orig(tmp_path: Path) -> None:
    """Final review excludes a recorded ko-en duplicate from semantic evidence."""
    fixture = create_run(tmp_path, segment_count=1)
    paths = fixture.store.paths(fixture.run_id)
    current = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    manifest, _ = _manifest(
        fixture.run_id,
        current.source_url,
        paths.run_dir,
        with_duplicate=True,
    )
    write_model_atomic(
        paths.media_manifest,
        manifest.model_copy(
            update={
                "workflow_status": current.workflow_status,
                "video_path": current.video_path,
                "audio_path": current.audio_path,
                "duration_ms": current.duration_ms,
                "frame_count": current.frame_count,
                "frames": current.frames,
            }
        ),
    )
    _write_empty_final_review_sidecars(
        fixture.store, fixture.run_id, manifest.subtitle_inventory
    )

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        assert len(data.korean_vtt_evidence) == 1
        assert data.korean_vtt_evidence[0].path.endswith("automatic.ko.ko-orig.vtt")
        return semantic_result(data)

    receipt = execute(fixture, reviewer)

    assert receipt.run_id == fixture.run_id


def test_final_review_rejects_stale_policy_before_reviewer(tmp_path: Path) -> None:
    """A stale policy-bound inventory cannot enter final-review evidence."""
    fixture = create_run(tmp_path, segment_count=1)
    paths = fixture.store.paths(fixture.run_id)
    current = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    manifest, _ = _manifest(
        fixture.run_id,
        current.source_url,
        paths.run_dir,
        policy_sha=STALE_POLICY_SHA,
    )
    write_model_atomic(
        paths.media_manifest,
        manifest.model_copy(
            update={
                "workflow_status": current.workflow_status,
                "video_path": current.video_path,
                "audio_path": current.audio_path,
                "duration_ms": current.duration_ms,
                "frame_count": current.frame_count,
                "frames": current.frames,
            }
        ),
    )
    _write_empty_final_review_sidecars(
        fixture.store, fixture.run_id, manifest.subtitle_inventory
    )

    async def reviewer(data: FinalReviewSegmentInput) -> ReviewSegmentResult:
        del data
        raise AssertionError

    with pytest.raises(FinalReviewExecutionError, match=r"stale|foreign"):
        _ = execute(fixture, reviewer)
