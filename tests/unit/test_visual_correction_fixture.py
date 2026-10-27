# Copyright 2026 Transcription Module contributors

"""Frozen truth and metric-population contracts for visual correction."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.visual_correction_fixture_support import (
    MetricPopulationStatus,
    VisualCorrectionFixture,
    load_visual_correction_fixture,
)

FIXTURE_PATH = Path("tests/fixtures/visual-correction-regressions.v1.json")
REQUIRED_CASE_IDS = {
    "condensed-genuine-caption",
    "verbatim-editorial-telop",
    "local-span-repair",
    "full-cue-repair",
    "strong-visual-only-insertion",
    "vad-negative-conflict",
    "static-editorial-overlay",
    "cut-style-change",
    "cumulative-reveal",
    "derived-ko-en-track",
    "duplicate-subtitle-track",
    "repeated-subtitle-two-times",
    "overlap-two-bound-turns",
    "lower-third-non-speaker",
    "window-duplicate-conflict",
    "confirmed-field-conflict",
    "same-time-same-text-two-speakers",
}
SAME_TIME_START_MS = 9_000
SAME_TIME_END_MS = 10_000
SAME_TIME_INTERVAL_COUNT = 2
REQUIRED_METRICS = {
    "meaning_accuracy",
    "mandatory_omission",
    "visual_correction",
    "visual_gap_insertion",
    "editorial_leakage_safety",
    "character_traceability",
    "lyrics_recovery_not_applicable",
}


def _fixture() -> VisualCorrectionFixture:
    return load_visual_correction_fixture(FIXTURE_PATH)


def test_fixture_freezes_all_required_visual_correction_shapes() -> None:
    """The compact fixture retains every approved positive and safety case."""
    fixture = _fixture()

    assert fixture.schema_version == "visual-correction-regressions.v1"
    assert {case.case_id for case in fixture.cases} == REQUIRED_CASE_IDS
    assert {
        metric.metric_id for metric in fixture.metric_populations
    } == REQUIRED_METRICS
    assert fixture.reference_only.alignment_basis == "source_identity_time_speaker"
    assert fixture.reference_only.ass_path.suffix == ".ass"
    assert all(case.expected_role is not None for case in fixture.cases)
    assert all(case.source_identity.source_cue_ids for case in fixture.cases)


def test_metric_denominators_are_frozen_and_zero_is_na() -> None:
    """Metric populations use named cases, not raw cue-count alignment."""
    fixture = _fixture()
    by_id = {metric.metric_id: metric for metric in fixture.metric_populations}

    for metric in by_id.values():
        assert metric.expected_denominator == len(metric.case_ids)
        assert metric.status is (
            MetricPopulationStatus.NOT_APPLICABLE
            if metric.expected_denominator == 0
            else MetricPopulationStatus.COUNTED
        )
    assert by_id["lyrics_recovery_not_applicable"].expected_denominator == 0
    assert by_id["lyrics_recovery_not_applicable"].status.value == "N/A"


def test_missing_expected_role_is_rejected() -> None:
    """A case cannot enter the benchmark without a frozen visual role."""
    payload = deepcopy(_fixture().model_dump(mode="json"))
    del payload["cases"][0]["expected_role"]

    with pytest.raises(ValidationError):
        _ = VisualCorrectionFixture.model_validate(payload)


def test_reused_interval_without_source_identity_is_rejected() -> None:
    """Equal time ranges remain distinguishable only by source ownership."""
    payload = deepcopy(_fixture().model_dump(mode="json"))
    payload["reference_only"]["comparable_intervals"][0]["source_identity"][
        "source_cue_ids"
    ] = []

    with pytest.raises(ValidationError):
        _ = VisualCorrectionFixture.model_validate(payload)


def test_ass_cannot_enter_generation_inputs() -> None:
    """The corrected ASS is reference-only and never a generation input."""
    payload = deepcopy(_fixture().model_dump(mode="json"))
    payload["generation_inputs"]["ass_path"] = payload["reference_only"][
        "ass_path"
    ]

    with pytest.raises(ValidationError):
        _ = VisualCorrectionFixture.model_validate(payload)


def test_all_fixture_references_are_closed_and_source_distinct() -> None:
    """Cases reference known evidence and same-time identities stay distinct."""
    fixture = _fixture()
    frame_ids = {frame.frame_id for frame in fixture.generation_inputs.frames}
    asr_ids = {segment.ordinal for segment in fixture.generation_inputs.asr_segments}
    vad_ids = {
        interval.interval_id for interval in fixture.generation_inputs.vad_intervals
    }
    track_ids = {track.track_id for track in fixture.generation_inputs.subtitle_tracks}
    interval_ids = {
        interval.interval_id
        for interval in fixture.reference_only.comparable_intervals
    }

    for case in fixture.cases:
        assert set(case.frame_ids) <= frame_ids
        assert set(case.asr_ordinals) <= asr_ids
        assert set(case.vad_interval_ids) <= vad_ids
        assert set(case.subtitle_track_ids) <= track_ids
        assert set(case.comparable_interval_ids) <= interval_ids

    same_time = [
        interval
        for interval in fixture.reference_only.comparable_intervals
        if interval.start_ms == SAME_TIME_START_MS
        and interval.end_ms == SAME_TIME_END_MS
    ]
    assert len(same_time) == SAME_TIME_INTERVAL_COUNT
    identities = {
        (
            interval.source_identity.source_cue_ids,
            interval.source_identity.speaker,
            interval.source_identity.turn_id,
        )
        for interval in same_time
    }
    assert len(identities) == SAME_TIME_INTERVAL_COUNT
