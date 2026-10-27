# Copyright 2026 Transcription Module contributors

"""Canonical target line breaks remain consistent through subtitle delivery."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from tests.translation.canonical_preflight_support import (
    checker_evidence,
    preflight_context,
)
from translation_module.canonical._delivery_formats import parse_sbv, render_sbv
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.canonical.preflight import build_preflight_report
from translation_module.canonical.translation import (
    LineageStatus,
    TargetCueV2,
)
from translation_module.common import ContentKind


def test_target_rejects_literal_ass_line_separator() -> None:
    """An ASS separator cannot masquerade as one canonical utterance line."""
    with pytest.raises(ValidationError, match="actual LF"):
        _ = TargetCueV2(
            sequence=1,
            cue_id="two-turns",
            source_cue_ids=("source-one",),
            lineage_status=LineageStatus.CONFIRMED,
            start_ms=0,
            end_ms=5_000,
            text="- First turn.\\N- Second turn.",
            speakers=("speaker-a", "speaker-b"),
            content_kind=ContentKind.DIALOGUE,
            context_ids=(),
            evidence_ids=(),
        )


def test_actual_line_breaks_round_trip_preflight_and_sbv() -> None:
    """Two canonical display lines stay two lines in JSON and YouTube SBV."""
    original = preflight_context()
    text = "A" * 25 + "\n" + "B" * 25
    cue = original.candidate.cues[0].model_copy(update={"text": text})
    candidate = bind_canonical_digests(
        original.candidate.model_copy(
            update={"cues": (cue, *original.candidate.cues[1:])}
        )
    )
    context = replace(original, candidate=candidate)

    restored = TargetCueV2.model_validate_json(cue.model_dump_json())
    report = build_preflight_report(context, checker_evidence(context))
    sbv = render_sbv(candidate)
    parsed = parse_sbv(sbv)

    assert restored.text == text
    assert not report.over_line_count_findings
    assert not report.over_line_width_findings
    assert parsed is not None
    assert parsed[0] == (cue.start_ms, cue.end_ms, text)
    assert b"\\N" not in sbv
