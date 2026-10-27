# Copyright 2026 Transcription Module contributors

"""Canonical translation JSON boundary tests."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from tests.translation.type_builders import milliseconds
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    FrozenModel,
    SourceCueId,
    canonical_json_bytes,
)
from translation_module.preparation import source_digest
from translation_module.source import (
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)


def test_baseline_source_digest_is_canonical_and_stable() -> None:
    """Current source serialization has a stable canonical digest."""
    source = SourceSnapshot(
        document_id="source-1",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(1_000),
        context=EpisodeContext(summary="인사"),
        evidence=EvidenceManifest(base_uri="memory://source/"),
        cues=(
            SourceCue(
                sequence=1,
                cue_id=SourceCueId("source-1"),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="안녕하세요",
                speakers=("speaker-1",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
    )

    assert source_digest(source) == source_digest(source)


class _FinitePayload(FrozenModel):
    value: float


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_strict_boundary_rejects_nonfinite_numbers(value: float) -> None:
    """Nonfinite JSON numbers have no canonical public representation."""
    with pytest.raises(ValidationError):
        _ = _FinitePayload.model_validate({"value": value})


def test_canonical_bytes_are_identical_after_repeated_public_round_trips() -> None:
    """Public source bytes are unchanged by a repeated JSON round trip."""
    source = SourceSnapshot(
        document_id="source-1",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(1_000),
        context=EpisodeContext(summary="인사"),
        evidence=EvidenceManifest(base_uri="memory://source/"),
        cues=(
            SourceCue(
                sequence=1,
                cue_id=SourceCueId("source-1"),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="안녕하세요",
                speakers=("speaker-1",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
    )
    first = canonical_json_bytes(source)
    restored = SourceSnapshot.model_validate_json(first)

    assert first == canonical_json_bytes(restored)
    assert math.isfinite(float(source.duration_ms))
