# Copyright 2026 Transcription Module contributors

"""Policy windows load exact overlap-only persisted source authority."""

from pathlib import Path

import anyio
import pytest
from pydantic import ValidationError

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    POLICY_SHA,
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.asr import AsrRunArtifact
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import RunId
from transcription_module.storage import write_model_atomic
from transcription_module.visual_evidence_policy import EvidenceEra
from transcription_module.window_evidence import WindowEvidenceError


def test_persisted_policy_authority_enters_one_real_drafter_call(
    tmp_path: Path,
) -> None:
    """Given complete sidecars, when drafted, then local identities are exact."""
    fixture = create_policy_window_fixture(tmp_path)
    captured: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert len(captured) == 1
    window = captured[0]
    assert window.run_id == fixture.run_id
    assert window.era.value == "policy_bound"
    assert window.policy_digest == POLICY_SHA
    assert window.inventory_digest is not None
    assert window.source_digest is not None
    assert tuple(cue.cue_id for cue in window.subtitle_cues) == (
        "automatic-ko-orig:1",
        "automatic-ko-orig:2",
        "automatic-ko-orig:3",
    )
    assert tuple(item.interval_id for item in window.vad_intervals) == (
        "vad:1",
        "vad:2",
    )
    assert window.verified_turns == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "asr_segments",
            [{"source_ordinal": 1, "start_ms": 0, "end_ms": 500, "speech": "밖"}],
        ),
        (
            "subtitle_cues",
            [
                {
                    "cue_id": "automatic-ko-orig:outside",
                    "track_id": "automatic-ko-orig",
                    "start_tc_ms": 0,
                    "end_tc_ms": 500,
                    "text": "밖",
                }
            ],
        ),
        (
            "vad_intervals",
            [{"interval_id": "vad:outside", "start_tc_ms": 0, "end_tc_ms": 500}],
        ),
        (
            "verified_turns",
            [
                {
                    "turn_id": "turn:outside",
                    "start_tc_ms": 0,
                    "end_tc_ms": 500,
                    "cluster": "SPEAKER_OUTSIDE",
                    "speaker": "화자X",
                    "authority_digest": "d" * 64,
                    "verification_evidence_ids": ["speaker-observation:outside"],
                    "verification_decision_ids": ["speaker-confirmation:outside"],
                }
            ],
        ),
        (
            "frames",
            [
                {
                    "frame_id": "outside-frame",
                    "frame_index": 0,
                    "raw_pts": 0,
                    "raw_pts_time": "0.000",
                    "best_effort_timestamp": 0,
                    "best_effort_timestamp_time": "0.000",
                    "pts_ms": 0,
                    "path": "input/frames/outside.png",
                }
            ],
        ),
    ],
)
def test_nonoverlapping_local_authority_fails_when_input_is_parsed(
    field: str,
    value: list[dict[str, int | str]],
) -> None:
    """Given foreign-time evidence, when parsed, then it cannot enter a window."""
    payload: dict[str, int | str | list[dict[str, int | str]]] = {
        "window_index": 0,
        "run_id": "policy-input-run",
        "core_start_ms": 1_000,
        "core_end_ms": 2_000,
        "context_start_ms": 1_000,
        "context_end_ms": 2_000,
        "asr_segments": [],
        "subtitle_tracks": [],
        "frames": [],
        "subtitle_cues": [],
        "vad_intervals": [],
        "verified_turns": [],
        "era": "policy_bound",
        "policy_digest": "a" * 64,
        "inventory_digest": "b" * 64,
        "source_digest": "c" * 64,
    }
    payload[field] = value

    with pytest.raises(ValidationError, match="overlap its context"):
        _ = WindowDraftInput.model_validate(payload)


def test_foreign_asr_run_identity_fails_before_drafter(tmp_path: Path) -> None:
    """Given another run's ASR, when loaded, then no callback receives it."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    asr = AsrRunArtifact.model_validate_json(
        paths.asr_transcript.read_text(encoding="utf-8")
    )
    write_model_atomic(
        paths.asr_transcript,
        asr.model_copy(update={"run_id": RunId("foreign-run")}),
    )

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        del data
        raise AssertionError

    with pytest.raises(WindowEvidenceError, match="run identity"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )


def test_legacy_asr_parses_but_never_grants_policy_window_authority(
    tmp_path: Path,
) -> None:
    """Given empty legacy ASR, when policy input loads, then authority is denied."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    asr = AsrRunArtifact.model_validate_json(
        paths.asr_transcript.read_text(encoding="utf-8")
    )
    legacy = asr.model_copy(
        update={
            "era": EvidenceEra.LEGACY,
            "policy_digest": None,
            "transcript": asr.transcript.model_copy(
                update={"text": "", "segments": ()}
            ),
        }
    )
    write_model_atomic(paths.asr_transcript, legacy)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        del data
        raise AssertionError

    with pytest.raises(WindowEvidenceError, match="policy authority"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )


def test_changed_audio_bytes_fail_before_drafter(tmp_path: Path) -> None:
    """Given stale source bytes, when loaded, then declared hashes grant nothing."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    _ = Path(media.audio_path).write_bytes(b"changed after evidence")

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        del data
        raise AssertionError

    with pytest.raises(WindowEvidenceError, match="audio bytes"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )




def test_missing_persisted_frame_fails_before_drafter(tmp_path: Path) -> None:
    """Given a missing manifest frame, when loaded, then frame IDs are revoked."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    (paths.run_dir / media.frames[0].path).unlink()

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        del data
        raise AssertionError

    with pytest.raises(WindowEvidenceError, match="frame"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )
