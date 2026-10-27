# Copyright 2026 Transcription Module contributors

"""Canonical source speaker turn validation."""

import pytest
from pydantic import ValidationError

from translation_module.canonical import SourceCueV2
from translation_module.common import ContentKind
from translation_module.source import SourceCue


def test_source_cue_v1_preserves_repeated_speaker_turns() -> None:
    """Ordered speaker ownership may return to an earlier speaker."""
    cue = SourceCue.model_validate_json(
        """
        {
          "sequence": 1,
          "cue_id": "repeated-speaker",
          "start_ms": 0,
          "end_ms": 1000,
          "text": "그 고통 같은 게 즐거워요.\\n네?\\n너무 즐거워요.",
          "speakers": ["김민석", "한지아", "김민석"],
          "content_kind": "dialogue"
        }
        """
    )

    assert cue.speakers == ("김민석", "한지아", "김민석")


def test_source_cue_v2_preserves_repeated_speaker_turns() -> None:
    """Canonical v2 keeps the same ordered speaker ownership."""
    cue = SourceCueV2(
        sequence=1,
        cue_id="repeated-speaker",
        start_ms=0,
        end_ms=1_000,
        text="그 고통 같은 게 즐거워요.\n네?\n너무 즐거워요.",
        speakers=("김민석", "한지아", "김민석"),
        content_kind=ContentKind.DIALOGUE,
        evidence_ids=(),
    )

    assert cue.speakers == ("김민석", "한지아", "김민석")


def test_source_cue_v2_rejects_speaker_utterance_count_mismatch() -> None:
    """Each ordered speaker owns exactly one displayed utterance line."""
    with pytest.raises(ValidationError, match="speaker count"):
        _ = SourceCueV2(
            sequence=1,
            cue_id="mismatched-speakers",
            start_ms=0,
            end_ms=1_000,
            text="첫 발화\n둘째 발화",
            speakers=("한지아", "루미", "박도현"),
            content_kind=ContentKind.DIALOGUE,
            evidence_ids=(),
        )


def test_source_cue_v2_counts_literal_ass_line_separator() -> None:
    """Migrated ASS line separators retain source turn cardinality."""
    with pytest.raises(ValidationError, match="speaker count"):
        _ = SourceCueV2(
            sequence=1,
            cue_id="mismatched-literal-ass-speakers",
            start_ms=0,
            end_ms=1_000,
            text="첫 발화\\N둘째 발화",
            speakers=("한지아", "루미", "박도현"),
            content_kind=ContentKind.DIALOGUE,
            evidence_ids=(),
        )


def test_source_cue_v2_preserves_joint_speakers_for_one_utterance() -> None:
    """One displayed utterance may be jointly owned by multiple speakers."""
    cue = SourceCueV2(
        sequence=1,
        cue_id="joint-speakers",
        start_ms=0,
        end_ms=1_000,
        text="가우림!",
        speakers=("한지아", "루미"),
        content_kind=ContentKind.DIALOGUE,
        evidence_ids=(),
    )

    assert cue.speakers == ("한지아", "루미")
