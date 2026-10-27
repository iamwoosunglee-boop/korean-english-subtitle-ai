# Copyright 2026 Transcription Module contributors

"""End-to-end tests for untrusted CLI boundaries."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from transcription_module.ass import parse_ass_dialogues
from transcription_module.errors import ErrorCode, ErrorEnvelope
from transcription_module.media import PreparedFrameManifest, PreparedMediaManifest
from transcription_module.models import (
    Cue,
    CueId,
    EpisodeContext,
    FrameId,
    Milliseconds,
    RunId,
    RunState,
    StartResult,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.translation_export_evidence import ContextObservations
from translation_module.contracts import SourceSnapshot
from translation_module.evidence import FileEvidenceResolver
from translation_module.preparation import ChunkPlan, prepare_request, source_digest

PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "walking"
ASS_RUN_ID = "286a0bb7-21aa-40a2-9562-f7bb8d306562"
ASS_SHA256 = "26d50a0560b866934cf9f2bda598a9c123000e4a8cee14b125a27143181bace0"
ASS_DURATION_MS = 1_920_174
ASS_CUE_COUNT = 552
ASS_EFFECT_COUNT = 52
ASS_MULTI_SPEAKER_COUNT = 95
FULLWIDTH_PIPE = "\uFF5C"


def _run_cli(
    *arguments: str,
    runs_dir: Path,
    use_fixture: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if use_fixture:
        environment["TRANSCRIPTION_FIXTURE_DIR"] = str(FIXTURE_DIR)
    return subprocess.run(
        ["uv", "run", "transcription-cli", *arguments, "--runs-dir", str(runs_dir)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _create_draft(
    runs_dir: Path,
    *,
    accept_confirmation: bool = True,
) -> tuple[str, Path]:
    started = _run_cli(
        "start",
        "https://www.youtube.com/watch?v=fixture",
        runs_dir=runs_dir,
        use_fixture=True,
    )
    assert started.returncode == 0, started.stderr
    run_id = str(StartResult.model_validate_json(started.stdout).run_id)
    completed = _run_cli(
        "context",
        run_id,
        "--text",
        "없음",
        runs_dir=runs_dir,
        use_fixture=True,
    )
    assert completed.returncode == 0, completed.stderr
    if accept_confirmation:
        consented = _run_cli(
            "confirmation-consent",
            run_id,
            "--accept",
            runs_dir=runs_dir,
        )
        assert consented.returncode == 0, consented.stderr
    store = RunStore(runs_dir)
    paths = store.paths(RunId(run_id))
    state = store.load_state(RunId(run_id))
    transcript = store.load_transcript(RunId(run_id))
    frame_ids = tuple(
        dict.fromkeys(
            frame_id
            for evidence in transcript.vision_evidence
            for frame_id in evidence.frame_ids
        )
    ) or (FrameId("frame-000"),)
    frames_dir = paths.run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for frame_id in frame_ids:
        _ = (frames_dir / f"{frame_id}.png").write_bytes(b"fixture frame bytes")
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=RunId(run_id),
            source_url=state.source_url,
            workflow_status=state.workflow_status,
            video_path="video.mp4",
            audio_path="audio.wav",
            duration_ms=transcript.duration_ms,
            frame_count=len(frame_ids),
            frames=tuple(
                PreparedFrameManifest(
                    frame_id=frame_id,
                    frame_index=index,
                    raw_pts=index,
                    raw_pts_time=f"{index}.000000",
                    best_effort_timestamp=index,
                    best_effort_timestamp_time=f"{index}.000000",
                    pts_ms=index * 1_000,
                    path=f"frames/{frame_id}.png",
                )
                for index, frame_id in enumerate(frame_ids)
            ),
        ),
    )
    return run_id, runs_dir / run_id


def test_start_rejects_non_https_youtube_url_without_creating_run(
    tmp_path: Path,
) -> None:
    """A local-file URL fails without creating a run directory."""
    runs_dir = tmp_path / "runs"

    result = _run_cli(
        "start",
        "file:///etc/passwd",
        runs_dir=runs_dir,
    )

    assert result.returncode != 0
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.INVALID_URL
    assert error.error.message == "source URL must be an HTTPS YouTube video URL"
    assert not runs_dir.exists()


def test_corrupt_confirmation_log_preserves_draft_and_state(tmp_path: Path) -> None:
    """Malformed history cannot alter a valid draft or workflow state."""
    runs_dir = tmp_path / "runs"
    run_id, run_dir = _create_draft(runs_dir)
    transcript_before = (run_dir / "transcript.json").read_bytes()
    state_before = (run_dir / "state.json").read_bytes()
    _ = (run_dir / "confirmations.jsonl").write_text(
        "{broken\n",
        encoding="utf-8",
    )

    result = _run_cli(
        "confirm",
        run_id,
        "cue-2-speech",
        "--answer",
        "x",
        runs_dir=runs_dir,
    )

    assert result.returncode != 0
    error = ErrorEnvelope.model_validate_json(result.stderr)
    assert error.error.code is ErrorCode.CORRUPT_CONFIRMATION_LOG
    assert (run_dir / "transcript.json").read_bytes() == transcript_before
    assert (run_dir / "state.json").read_bytes() == state_before


def _assert_authoritative_ass_source(
    source: SourceSnapshot,
    ass_payload: bytes,
    run_dir: Path,
    runs_dir: Path,
) -> None:
    assert source.schema_version == "source-transcript.v1"
    assert source.maturity.value == "finalized"
    assert source.content_digest == source_digest(source)
    assert source.duration_ms == ASS_DURATION_MS
    assert (
        len(source.cues)
        == len({cue.cue_id for cue in source.cues})
        == ASS_CUE_COUNT
    )
    assert sum(cue.context_note is not None for cue in source.cues) == ASS_EFFECT_COUNT
    event_by_id = {
        f"{ASS_RUN_ID}:ass-dialogue:{event.event_index:06d}": event
        for event in parse_ass_dialogues(ass_payload)
    }
    assert all(cue.text == event_by_id[str(cue.cue_id)].text for cue in source.cues)
    assert all(
        cue.context_note == event_by_id[str(cue.cue_id)].effect
        for cue in source.cues
    )
    assert (
        sum(len(cue.speakers) > 1 for cue in source.cues)
        == ASS_MULTI_SPEAKER_COUNT
    )
    assert all(
        not any(delimiter in speaker for delimiter in ("|", FULLWIDTH_PIPE, "·"))
        for cue in source.cues
        for speaker in cue.speakers
    )
    ass_evidence = next(
        entry for entry in source.evidence.entries if entry.evidence_id == "source-ass"
    )
    assert ass_evidence.resource_digest == hashlib.sha256(ass_payload).hexdigest()
    assert ass_evidence.resource_digest == ASS_SHA256
    observations = ContextObservations.model_validate_json(
        (run_dir / "context-observations.json").read_bytes()
    )
    assert observations.episode is not None
    assert observations.structure is not None
    assert observations.identity_rule is not None
    state = RunStore(runs_dir).load_state(RunId(ASS_RUN_ID))
    expected_summary = "\n".join(
        (
            f"회차: {observations.episode}",
            f"구성: {observations.structure}",
            f"신원 규칙: {observations.identity_rule}",
            *(
                f"인물: {person.name} | {person.role} | {person.appearance}"
                for person in observations.people
            ),
            f"보충: {state.context.raw_text if state.context else ''}",
        )
    )
    assert source.context.summary == expected_summary
    assert tuple(person.name for person in source.context.participants) == tuple(
        person.name for person in observations.people
    )
    plans = tuple(
        ChunkPlan(
            chunk_id=f"chunk-{start // 64 + 1:02d}",
            owned_source_cue_ids=tuple(
                cue.cue_id for cue in source.cues[start : start + 64]
            ),
        )
        for start in range(0, len(source.cues), 64)
    )
    requests = tuple(
        prepare_request(source, plan, FileEvidenceResolver(run_dir))
        for plan in plans
    )
    owned = tuple(cue for request in requests for cue in request.owned_cues)
    assert tuple(cue.cue_id for cue in owned) == tuple(
        cue.cue_id for cue in source.cues
    )
    assert sum(cue.context_note is not None for cue in owned) == ASS_EFFECT_COUNT


def test_cli_exports_authoritative_ass_deterministically(tmp_path: Path) -> None:
    """The explicit ASS command exports the real finalized source and nothing stale."""
    runs_dir = PROJECT_ROOT / "runs"
    run_dir = runs_dir / ASS_RUN_ID
    ass_path = run_dir / "transcript.ass"
    state_path = run_dir / "state.json"
    first_output = tmp_path / "source-a.json"
    second_output = tmp_path / "source-b.json"
    ass_before = ass_path.read_bytes()
    state_before = state_path.read_bytes()

    first = _run_cli(
        "export-ass-translation-source",
        ASS_RUN_ID,
        "--expected-ass-sha256",
        ASS_SHA256,
        "--output",
        str(first_output),
        runs_dir=runs_dir,
    )
    second = _run_cli(
        "export-ass-translation-source",
        ASS_RUN_ID,
        "--expected-ass-sha256",
        ASS_SHA256,
        "--output",
        str(second_output),
        runs_dir=runs_dir,
    )

    assert first.returncode == second.returncode == 0, first.stderr or second.stderr
    assert first_output.read_bytes() == second_output.read_bytes()
    source = SourceSnapshot.model_validate_json(first_output.read_bytes())
    _assert_authoritative_ass_source(source, ass_before, run_dir, runs_dir)
    assert ass_path.read_bytes() == ass_before
    assert state_path.read_bytes() == state_before

    bad_output = tmp_path / "bad.json"
    bad = _run_cli(
        "export-ass-translation-source",
        ASS_RUN_ID,
        "--expected-ass-sha256",
        "0" * 64,
        "--output",
        str(bad_output),
        runs_dir=runs_dir,
    )
    omitted = _run_cli(
        "export-ass-translation-source",
        ASS_RUN_ID,
        "--output",
        str(tmp_path / "omitted.json"),
        runs_dir=runs_dir,
    )

    assert bad.returncode != 0
    assert (
        ErrorEnvelope.model_validate_json(bad.stderr).error.code
        is ErrorCode.VALIDATION_FAILED
    )
    assert omitted.returncode != 0
    assert not bad_output.exists()
    assert not (tmp_path / "omitted.json").exists()


def test_cli_exports_selected_draft_source_maturity(tmp_path: Path) -> None:
    """The CLI passes an explicit draft maturity into the source snapshot."""
    runs_dir = tmp_path / "runs"
    store = RunStore(runs_dir)
    run_id = RunId("42e41202-aa18-4933-8240-dd600baba758")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=cli-export-draft"
            ),
            workflow_status=WorkflowStatus.FINALIZED,
            validation_status=ValidationStatus.VALID,
            context=EpisodeContext(raw_text="확정된 전사, 초안 번역 소스"),
        )
    )
    write_model_atomic(
        paths.transcript,
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.FINALIZED,
            validation_status=ValidationStatus.VALID,
            duration_ms=Milliseconds(2_000),
            cues=(
                Cue(
                    sequence=1,
                    cue_id=CueId("cue-1"),
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="초안으로 번역해 주세요.",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
            vision_evidence=(),
        ),
    )
    output = tmp_path / "draft-source.json"

    result = _run_cli(
        "export-translation-source",
        str(run_id),
        "--output",
        str(output),
        "--source-maturity",
        "draft",
        runs_dir=runs_dir,
    )

    assert result.returncode == 0, result.stderr
    source = SourceSnapshot.model_validate_json(output.read_text(encoding="utf-8"))
    assert source.maturity.value == "draft"
    assert source.content_digest == source_digest(source)


def test_cli_exports_current_finalized_run(tmp_path: Path) -> None:
    """The omitted maturity option exports finalized without changing the run."""
    runs_dir = tmp_path / "runs"
    store = RunStore(runs_dir)
    run_id = RunId("32e41202-aa18-4933-8240-dd600baba757")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl(
                "https://www.youtube.com/watch?v=cli-export"
            ),
            workflow_status=WorkflowStatus.FINALIZED,
            validation_status=ValidationStatus.VALID,
            context=EpisodeContext(raw_text="확정된 공연 회차"),
        )
    )
    write_model_atomic(
        paths.transcript,
        Transcript(
            run_id=run_id,
            workflow_status=WorkflowStatus.FINALIZED,
            validation_status=ValidationStatus.VALID,
            duration_ms=Milliseconds(2_000),
            cues=(
                Cue(
                    sequence=1,
                    cue_id=CueId("cue-1"),
                    start_tc_ms=Milliseconds(0),
                    end_tc_ms=Milliseconds(1_000),
                    speech="안녕하세요.",
                    uncertainty=Uncertainty.CERTAIN,
                ),
            ),
            vision_evidence=(),
        ),
    )
    output = tmp_path / "source.json"
    state_before = paths.state.read_bytes()
    transcript_before = paths.transcript.read_bytes()

    result = _run_cli(
        "export-translation-source",
        str(run_id),
        "--output",
        str(output),
        runs_dir=runs_dir,
    )

    assert result.returncode == 0, result.stderr
    source = SourceSnapshot.model_validate_json(output.read_text(encoding="utf-8"))
    assert source.document_id == str(run_id)
    assert source.maturity.value == "finalized"
    assert source.content_digest == source_digest(source)
    assert source.cues[0].speakers == ("unknown",)
    assert paths.state.read_bytes() == state_before
    assert paths.transcript.read_bytes() == transcript_before
