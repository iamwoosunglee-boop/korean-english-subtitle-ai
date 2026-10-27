# Copyright 2026 Transcription Module contributors

"""Working-source finalization and historical regression contracts."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

import transcription_module.validation as validation_module
from transcription_module.models import (
    CueId,
    EpisodeContext,
    EvidenceId,
    FrameId,
    RunId,
    RunState,
    Transcript,
    ValidationStatus,
    VisionEvidence,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore
from transcription_module.validation import ValidationContractError

FIXTURE_PATH = Path("tests/fixtures/canonical-workflow-regressions.v1.json")
EXPECTED_CASES = {
    "unresolved_speech",
    "empty_speaker",
    "incomplete_confirmation_closure",
    "distinct_speaker_simultaneous_repetition",
    "stale_confirmation_after_source_edit",
    "unapproved_review_non_mutation",
}


class RegressionCase(BaseModel):
    """One minimal historical shape with optional source expectations."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    transcript: Transcript | None = None
    expected_codes: tuple[str, ...] = ()
    expected_cue_ids: tuple[str, ...] = ()


class RegressionFixture(BaseModel):
    """Versioned six-shape historical regression fixture."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["canonical-workflow-regressions.v1"]
    cases: dict[str, RegressionCase]


def _fixture_cases() -> dict[str, RegressionCase]:
    fixture = RegressionFixture.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    return fixture.cases


def _fixture_transcript(case_id: str) -> Transcript:
    transcript = _fixture_cases()[case_id].transcript
    assert transcript is not None
    return transcript


def _finding_projection(
    transcript: Transcript,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    findings = validation_module.validation_findings(transcript)
    return (
        tuple(finding.code.value for finding in findings),
        tuple(str(cue_id) for finding in findings for cue_id in finding.cue_ids),
    )


def test_fixture_locks_six_anonymized_regression_shapes() -> None:
    """Given the versioned fixture, all six accepted historical shapes remain."""
    assert set(_fixture_cases()) == EXPECTED_CASES


@pytest.mark.parametrize("case_id", ["unresolved_speech", "empty_speaker"])
def test_source_findings_return_stable_codes_and_cue_ids(case_id: str) -> None:
    """Given an unsafe source, findings identify the exact blocking cue."""
    case = _fixture_cases()[case_id]
    assert case.transcript is not None

    codes, cue_ids = _finding_projection(case.transcript)

    assert codes == case.expected_codes
    assert cue_ids == case.expected_cue_ids


def test_malformed_regression_fixture_is_rejected() -> None:
    """Given a malformed fixture boundary, parsing fails before test use."""
    with pytest.raises(ValidationError):
        _ = RegressionFixture.model_validate_json(
            '{"schema_version":"canonical-workflow-regressions.v1"}'
        )


def test_distinct_speaker_simultaneous_repetition_is_preserved() -> None:
    """Given simultaneous repeated text, distinct owners remain ordered cues."""
    transcript = _fixture_transcript("distinct_speaker_simultaneous_repetition")

    findings = validation_module.validation_findings(transcript)

    assert findings == ()
    assert tuple(cue.cue_id for cue in transcript.cues) == (
        CueId("cue-speaker-a"),
        CueId("cue-speaker-b"),
    )


def test_unparseable_speaker_is_invalid_but_unknown_is_only_nonblank() -> None:
    """Given speaker labels, syntax gates finalization without proving identity."""
    transcript = _fixture_transcript("distinct_speaker_simultaneous_repetition")
    malformed = transcript.model_copy(
        update={
            "cues": (
                transcript.cues[0].model_copy(update={"speaker": "화자-가| "}),
                transcript.cues[1],
            )
        }
    )
    unknown = transcript.model_copy(
        update={
            "cues": (
                transcript.cues[0].model_copy(update={"speaker": "unknown"}),
                transcript.cues[1],
            )
        }
    )

    assert _finding_projection(malformed)[0] == ("invalid_speaker",)
    assert validation_module.validation_findings(unknown) == ()


def test_true_same_speaker_overlap_reports_both_cue_ids() -> None:
    """Given a true duplicate overlap, the finding binds both affected cues."""
    transcript = _fixture_transcript("distinct_speaker_simultaneous_repetition")
    duplicate = transcript.model_copy(
        update={
            "cues": (
                transcript.cues[0],
                transcript.cues[1].model_copy(update={"speaker": "화자-가"}),
            )
        }
    )

    codes, cue_ids = _finding_projection(duplicate)

    assert codes == ("same_speaker_duplicate",)
    assert cue_ids == ("cue-speaker-a", "cue-speaker-b")


def test_working_digest_ignores_only_lifecycle_fields() -> None:
    """Given lifecycle and semantic edits, only semantic edits change the digest."""
    transcript = _fixture_transcript("distinct_speaker_simultaneous_repetition")
    lifecycle_only = transcript.model_copy(
        update={
            "workflow_status": WorkflowStatus.FINALIZED,
            "validation_status": ValidationStatus.VALID,
        }
    )
    semantic_mutation = transcript.model_copy(
        update={
            "cues": (
                transcript.cues[0].model_copy(update={"speech": "변경된 발화"}),
                transcript.cues[1],
            )
        }
    )
    evidence_mutation = transcript.model_copy(
        update={
            "vision_evidence": (
                VisionEvidence(
                    evidence_id=EvidenceId("vision-added"),
                    frame_ids=(FrameId("frame-added"),),
                    scene="추가된 시각 근거",
                ),
            )
        }
    )

    original = validation_module.working_transcript_digest(transcript)

    assert validation_module.working_transcript_digest(transcript) == original
    assert validation_module.working_transcript_digest(lifecycle_only) == original
    assert validation_module.working_transcript_digest(semantic_mutation) != original
    assert validation_module.working_transcript_digest(evidence_mutation) != original


@pytest.mark.parametrize("case_id", ["unresolved_speech", "empty_speaker"])
def test_failed_source_finalization_preserves_authoritative_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
) -> None:
    """Given a blocker, validation publishes no lifecycle or source mutation."""
    transcript = _fixture_transcript(case_id)
    store = RunStore(tmp_path / "runs")
    run_id = RunId("fixture-run")
    store.save_state(
        RunState(
            run_id=run_id,
            source_url=YouTubeVideoUrl("https://youtu.be/source-integrity"),
            workflow_status=WorkflowStatus.READY_FOR_VALIDATION,
            context=EpisodeContext(raw_text="익명 fixture"),
        )
    )
    store.save_transcript(transcript)
    paths = store.paths(run_id)
    _ = paths.canonical_source.write_bytes(b"existing canonical source")
    before = {
        "transcript": paths.transcript.read_bytes(),
        "state": paths.state.read_bytes(),
        "source": paths.canonical_source.read_bytes(),
    }

    def accept_review(_: RunStore, _run_id: RunId) -> None:
        return None

    monkeypatch.setattr(
        validation_module,
        "assert_current_final_review",
        accept_review,
    )

    for _ in range(2):
        with pytest.raises(ValidationContractError):
            _ = validation_module.validate_run(store, run_id)

        assert paths.transcript.read_bytes() == before["transcript"]
        assert paths.state.read_bytes() == before["state"]
        assert paths.canonical_source.read_bytes() == before["source"]
