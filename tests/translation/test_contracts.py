# Copyright 2026 Transcription Module contributors

"""Public JSON contract tests for subtitle translation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel, ValidationError

from tests.translation.type_builders import context_id, evidence_id, milliseconds
from translation_module.answers import TranslationAnswer
from translation_module.common import SourceCueId, TargetCueId
from translation_module.contracts import (
    ContentKind,
    DocumentStatus,
    EpisodeContext,
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    Participant,
    RetainedContext,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
    TargetCue,
    TranslationAlternative,
    TranslationDocument,
    UnresolvedItem,
)
from translation_module.workflow import (
    AffectedKind,
    AffectedReference,
    FindingSeverity,
    ReviewDomain,
    ReviewFinding,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FRAME_PTS_MS = 500


def source_snapshot() -> SourceSnapshot:
    """Build a valid source snapshot fixture."""
    return SourceSnapshot(
        document_id="source-fixture",
        content_digest="a" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(10_000),
        context=EpisodeContext(
            summary="은하섬과 노바노바의 토크쇼와 공연",
            program="토크 라운지",
            participants=(
                Participant(
                    participant_id="kim-yuna",
                    name="한지아",
                    english_name="Han Jia",
                ),
            ),
        ),
        evidence=EvidenceManifest(
            base_uri="file:///translation-source/",
            entries=(
                EvidenceEntry(
                    evidence_id=evidence_id("frame-1"),
                    kind=EvidenceKind.FRAME,
                    media_type="image/jpeg",
                    resource_digest="b" * 64,
                    locator="frames/frame-1.jpg",
                    pts_ms=milliseconds(500),
                ),
            ),
        ),
        cues=(
            SourceCue(
                sequence=1,
                cue_id=SourceCueId("source-1"),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="토크 라운지에 오신",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
                evidence_ids=(evidence_id("frame-1"),),
            ),
            SourceCue(
                sequence=2,
                cue_id=SourceCueId("source-2"),
                start_ms=milliseconds(1_000),
                end_ms=milliseconds(2_000),
                text="노바 노바 여러분들,",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            ),
            SourceCue(
                sequence=3,
                cue_id=SourceCueId("source-3"),
                start_ms=milliseconds(2_000),
                end_ms=milliseconds(3_000),
                text="환영합니다.",
                speakers=("한지아", "박도현"),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
    )


def finalized_translation() -> TranslationDocument:
    """Build a minimal valid finalized translation fixture."""
    source = source_snapshot()
    return TranslationDocument(
        document_id="translation-fixture",
        content_digest="c" * 64,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_locale="ko-KR",
        target_locale="en-US",
        context=source.context,
        retained_contexts=(),
        cues=(),
        status=DocumentStatus.FINALIZED,
    )


def unresolved_item() -> UnresolvedItem:
    """Build one complete unresolved translation fixture."""
    alternatives = (
        TranslationAlternative(
            option_id="option-a",
            text="That was wild.",
            meaning="강렬했다는 의미",
            strength="미국 영어 구어체로 자연스럽다.",
            risk="원문의 감탄보다 강할 수 있다.",
        ),
        TranslationAlternative(
            option_id="option-b",
            text="That was amazing.",
            meaning="감탄을 전달한다.",
            strength="폭넓은 상황에 안전하다.",
            risk="화자의 강한 반응이 약해질 수 있다.",
        ),
    )
    return UnresolvedItem(
        unresolved_id="question-1",
        source_cue_ids=(SourceCueId("source-1"),),
        target_cue_ids=(TargetCueId("target-1"),),
        current_translation="That was amazing.",
        why_difficult="표정과 말투가 두 해석을 모두 지지한다.",
        evidence_ids=(evidence_id("frame-1"),),
        alternatives=alternatives,
        recommendation_id="option-a",
        recommendation_reason="강한 놀람에 조금 더 가깝다.",
    )


def test_source_snapshot_round_trips_context_speakers_and_evidence() -> None:
    """Source JSON preserves rich context, speakers and frame PTS."""
    source = source_snapshot()

    restored = SourceSnapshot.model_validate_json(source.model_dump_json())

    assert restored.maturity is SourceMaturity.FINALIZED
    assert restored.context.participants[0].name == "한지아"
    assert restored.cues[2].speakers == ("한지아", "박도현")
    assert restored.evidence.entries[0].pts_ms == FRAME_PTS_MS


@pytest.mark.parametrize(
    ("field", "unsupported"),
    [
        ("schema_version", "source-transcript.v2"),
        ("maturity", "reviewed"),
        ("source_locale", "ja-JP"),
    ],
)
def test_source_snapshot_json_rejects_unsupported_v1_profile(
    field: str,
    unsupported: str,
) -> None:
    """Source JSON accepts only the exact v1 Korean profile."""
    payload = source_snapshot().model_dump(mode="json")
    payload[field] = unsupported

    with pytest.raises(ValidationError):
        _ = SourceSnapshot.model_validate_json(json.dumps(payload))


def test_evidence_manifest_json_rejects_unsupported_version() -> None:
    """Evidence JSON accepts only the exact v1 schema version."""
    payload = source_snapshot().evidence.model_dump(mode="json")
    payload["schema_version"] = "translation-evidence.v2"

    with pytest.raises(ValidationError):
        _ = EvidenceManifest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("field", "unsupported"),
    [
        ("schema_version", "translation-document.v2"),
        ("source_locale", "ko"),
        ("target_locale", "en-GB"),
    ],
)
def test_translation_document_json_rejects_unsupported_v1_profile(
    field: str,
    unsupported: str,
) -> None:
    """Translation JSON accepts only the exact v1 Korean-to-US profile."""
    payload = finalized_translation().model_dump(mode="json")
    payload[field] = unsupported

    with pytest.raises(ValidationError):
        _ = TranslationDocument.model_validate_json(json.dumps(payload))


def test_source_snapshot_round_trips_context_evidence_references() -> None:
    """Episode-level evidence remains distinct from cue evidence."""
    source = source_snapshot()
    source = SourceSnapshot.model_validate(
        {
            **source.model_dump(),
            "context_evidence_ids": (evidence_id("frame-1"),),
        }
    )

    restored = SourceSnapshot.model_validate_json(source.model_dump_json())

    assert restored.context_evidence_ids == ("frame-1",)
    assert restored.cues[1].evidence_ids == ()


def test_source_snapshot_rejects_unknown_context_evidence_reference() -> None:
    """Episode context cannot name evidence absent from its manifest."""
    source = source_snapshot()

    with pytest.raises(ValidationError, match=r"context.*unknown evidence"):
        _ = SourceSnapshot.model_validate(
            {
                **source.model_dump(),
                "context_evidence_ids": (evidence_id("missing-frame"),),
            }
        )


def test_source_snapshot_rejects_unknown_evidence_reference() -> None:
    """Source cues cannot reference absent evidence resources."""
    source = source_snapshot()
    cue = source.cues[0].model_copy(update={"evidence_ids": ("missing-frame",)})

    with pytest.raises(ValidationError, match="unknown evidence"):
        _ = SourceSnapshot.model_validate(
            {
                **source.model_dump(exclude={"cues"}),
                "cues": (cue, *source.cues[1:]),
            }
        )


def test_finalized_translation_round_trips_speaker_and_retained_context() -> None:
    """Final translation JSON preserves speaker and retained context."""
    document = TranslationDocument(
        document_id="translation-fixture",
        content_digest="c" * 64,
        source_document_id="source-fixture",
        source_digest="a" * 64,
        source_locale="ko-KR",
        target_locale="en-US",
        context=source_snapshot().context,
        retained_contexts=(
            RetainedContext(
                context_id=context_id("opening"),
                summary="한지아가 게스트를 환영한다.",
                source_cue_ids=(
                    SourceCueId("source-1"),
                    SourceCueId("source-2"),
                    SourceCueId("source-3"),
                ),
                evidence_ids=(evidence_id("frame-1"),),
            ),
        ),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(
                    SourceCueId("source-1"),
                    SourceCueId("source-2"),
                    SourceCueId("source-3"),
                ),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_500),
                text="Welcome to Talk Lounge,",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
                context_ids=(context_id("opening"),),
            ),
            TargetCue(
                sequence=2,
                cue_id=TargetCueId("target-2"),
                source_cue_ids=(
                    SourceCueId("source-1"),
                    SourceCueId("source-2"),
                    SourceCueId("source-3"),
                ),
                start_ms=milliseconds(1_500),
                end_ms=milliseconds(3_000),
                text="NOVA NOVA!",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
                context_ids=(context_id("opening"),),
            ),
        ),
        status=DocumentStatus.FINALIZED,
    )

    restored = TranslationDocument.model_validate_json(document.model_dump_json())

    assert restored.context.summary.startswith("은하섬")
    assert restored.cues[0].speakers == ("한지아",)
    assert restored.retained_contexts[0].evidence_ids == ("frame-1",)


def test_finalized_translation_rejects_unresolved_items() -> None:
    """A finalized translation cannot retain user questions."""
    with pytest.raises(ValidationError, match="finalized"):
        _ = TranslationDocument(
            document_id="translation-fixture",
            content_digest="c" * 64,
            source_document_id="source-fixture",
            source_digest="a" * 64,
            source_locale="ko-KR",
            target_locale="en-US",
            context=source_snapshot().context,
            retained_contexts=(),
            cues=(),
            unresolved=(unresolved_item(),),
            status=DocumentStatus.FINALIZED,
        )


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (source_snapshot, "document_id"),
        (lambda: source_snapshot().cues[0], "text"),
        (lambda: source_snapshot().cues[0], "speakers"),
        (lambda: unresolved_item().alternatives[0], "text"),
    ],
)
def test_public_contracts_reject_blank_or_whitespace_values(
    model: Callable[[], BaseModel], field: str
) -> None:
    """Public IDs, prose, and speaker values cannot be whitespace-only."""
    value = model()
    replacement: object = ("  ",) if field == "speakers" else "  "

    with pytest.raises(ValidationError, match="nonblank"):
        _ = type(value).model_validate({**value.model_dump(), field: replacement})


@pytest.mark.parametrize("field", ["problem", "required_change"])
def test_review_finding_and_change_reject_whitespace(field: str) -> None:
    """Machine-consumed finding and required-change prose must be nonblank."""
    finding = {
        "finding_id": "finding-1",
        "candidate_digest": "a" * 64,
        "affected": (AffectedReference(kind=AffectedKind.TARGET_CUE, item_id="t1"),),
        "severity": FindingSeverity.MAJOR,
        "domain": ReviewDomain.FIDELITY,
        "defect_class": "accuracy",
        "blocking": True,
        "problem": "Incorrect translation",
        "required_change": "Correct the translation",
        field: "  ",
    }
    with pytest.raises(ValidationError, match="nonblank"):
        _ = ReviewFinding.model_validate(finding)


def test_translation_answer_rejects_whitespace() -> None:
    """A direct user answer must contain a nonblank value."""
    with pytest.raises(ValidationError, match="nonblank"):
        _ = TranslationAnswer(
            document_id="document-1",
            document_digest="a" * 64,
            unresolved_id="question-1",
            direct_text="  ",
        )


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (lambda: source_snapshot().cues[0], "evidence_ids"),
        (
            lambda: TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="Hello",
                speakers=("A",),
                content_kind=ContentKind.DIALOGUE,
                context_ids=(context_id("context-1"),),
                evidence_ids=(evidence_id("frame-1"),),
            ),
            "context_ids",
        ),
        (
            lambda: TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="Hello",
                speakers=("A",),
                content_kind=ContentKind.DIALOGUE,
            ),
            "source_cue_ids",
        ),
    ],
)
def test_public_contracts_reject_duplicate_list_values(
    model: Callable[[], BaseModel], field: str
) -> None:
    """Set-like public lists reject repeated values instead of normalizing them."""
    value = model()
    items = cast("tuple[object, ...]", getattr(value, field))
    item = items[0]
    with pytest.raises(ValidationError, match="unique"):
        _ = type(value).model_validate({**value.model_dump(), field: (item, item)})


def test_source_snapshot_rejects_reversed_timing_order_but_accepts_overlap() -> None:
    """Source starts remain ordered while legitimate overlap is accepted."""
    source = source_snapshot()
    overlap = source.cues[1].model_copy(update={"start_ms": 500})
    _ = SourceSnapshot.model_validate(
        {
            **source.model_dump(exclude={"cues"}),
            "cues": (source.cues[0], overlap, source.cues[2]),
        }
    )
    reversed_cue = source.cues[1].model_copy(update={"start_ms": 0, "end_ms": 500})
    with pytest.raises(ValidationError, match="timing order"):
        _ = SourceSnapshot.model_validate(
            {
                **source.model_dump(exclude={"cues"}),
                "cues": (source.cues[0], reversed_cue, source.cues[2]),
            }
        )


def test_needs_confirmation_requires_unresolved_items() -> None:
    """A confirmation document must contain at least one question."""
    with pytest.raises(ValidationError, match="needs_confirmation"):
        _ = TranslationDocument(
            document_id="translation-fixture",
            content_digest="c" * 64,
            source_document_id="source-fixture",
            source_digest="a" * 64,
            source_locale="ko-KR",
            target_locale="en-US",
            context=source_snapshot().context,
            retained_contexts=(),
            cues=(),
            status=DocumentStatus.NEEDS_CONFIRMATION,
        )
