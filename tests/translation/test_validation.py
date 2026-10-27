# Copyright 2026 Transcription Module contributors

"""Deterministic translation document validation tests."""

from __future__ import annotations

import json

import pytest

from tests.translation.type_builders import context_id, evidence_id, milliseconds
from translation_module.common import (
    SourceCueId,
    TargetCueId,
    canonical_json_bytes,
)
from translation_module.contracts import (
    ContentKind,
    DocumentStatus,
    EpisodeContext,
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    RetainedContext,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
    TargetCue,
    TranslationDocument,
    UnresolvedItem,
)
from translation_module.document import translation_digest
from translation_module.preparation import bind_source_digest
from translation_module.validation import (
    TranslationValidationError,
    bind_translation_digest,
    validate_translation,
)


def _source() -> SourceSnapshot:
    source = SourceSnapshot(
        document_id="source-1",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(5_000),
        context=EpisodeContext(summary="게스트 환영 인사"),
        evidence=EvidenceManifest(base_uri="memory://source/"),
        cues=tuple(
            SourceCue(
                sequence=sequence,
                cue_id=SourceCueId(f"source-{sequence}"),
                start_ms=milliseconds((sequence - 1) * 1_000),
                end_ms=milliseconds(sequence * 1_000),
                text=f"원문 {sequence}",
                speakers=("한지아",),
                content_kind=ContentKind.DIALOGUE,
            )
            for sequence in range(1, 4)
        ),
    )
    return bind_source_digest(source)


def _document(source: SourceSnapshot | None = None) -> TranslationDocument:
    source = source or _source()
    document = TranslationDocument(
        document_id="translation-1",
        content_digest="0" * 64,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_locale="ko-KR",
        target_locale="en-US",
        context=source.context,
        retained_contexts=(),
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
            ),
        ),
        status=DocumentStatus.FINALIZED,
    )
    return bind_translation_digest(document)


def test_valid_document_source_fixture_passes_validation() -> None:
    """The established valid source/document fixture passes validation."""
    validate_translation(_source(), _document())


def test_valid_many_to_many_resegmentation_passes() -> None:
    """Several target cues may share the same complete source lineage."""
    validate_translation(_source(), _document())


def test_validation_accepts_unpublished_draft_candidate() -> None:
    """Draft maturity limits publication, not candidate validation."""
    source = bind_source_digest(
        _source().model_copy(update={"maturity": SourceMaturity.DRAFT})
    )

    validate_translation(source, _document(source))


def test_validation_accepts_source_context_evidence_without_cue_duplication() -> None:
    """Episode evidence is not required on every translated cue."""
    source = _source()
    entry = EvidenceEntry(
        evidence_id=evidence_id("context-frame"),
        kind=EvidenceKind.FRAME,
        media_type="image/jpeg",
        resource_digest="a" * 64,
        locator="context-frame.jpg",
        pts_ms=milliseconds(500),
    )
    source = bind_source_digest(
        source.model_copy(
            update={
                "context_evidence_ids": (evidence_id("context-frame"),),
                "evidence": EvidenceManifest(
                    base_uri="memory://source/",
                    entries=(entry,),
                ),
            }
        )
    )

    validate_translation(source, _document(source))


def test_validation_rejects_target_missing_referenced_source_speaker() -> None:
    """A target spanning different speakers must retain the full speaker union."""
    source = _source().model_copy(
        update={
            "cues": (
                _source().cues[0],
                _source().cues[1].model_copy(update={"speakers": ("박도현",)}),
                _source().cues[2],
            )
        }
    )
    source = bind_source_digest(source)

    with pytest.raises(TranslationValidationError, match="speaker"):
        validate_translation(source, _document(source))


def test_validation_accepts_unknown_and_multiple_source_speakers() -> None:
    """Unknown and multi-speaker lineage remain valid speaker provenance."""
    source = _source()
    cues = (
        source.cues[0].model_copy(update={"speakers": ("unknown",)}),
        source.cues[1].model_copy(update={"speakers": ("한지아", "박도현")}),
        source.cues[2],
    )
    source = bind_source_digest(source.model_copy(update={"cues": cues}))
    document = _document(source)
    target_cues = tuple(
        cue.model_copy(update={"speakers": ("unknown", "한지아", "박도현")})
        for cue in document.cues
    )
    document = bind_translation_digest(
        document.model_copy(update={"cues": target_cues})
    )

    validate_translation(source, document)


def test_validation_accepts_overlapping_target_cues() -> None:
    """Targets with distinct lineage may overlap during resegmentation."""
    source = _source()
    source = bind_source_digest(
        source.model_copy(
            update={
                "cues": (
                    source.cues[0].model_copy(update={"end_ms": 2_000}),
                    *source.cues[1:],
                )
            }
        )
    )
    document = _document(source)
    cues = (
        document.cues[0].model_copy(update={"source_cue_ids": ("source-1",)}),
        document.cues[1].model_copy(
            update={
                "source_cue_ids": ("source-2", "source-3"),
                "start_ms": 1_000,
            }
        ),
    )
    document = bind_translation_digest(document.model_copy(update={"cues": cues}))

    validate_translation(source, document)


def test_validation_rejects_unreferenced_source_cue() -> None:
    """Every source cue must appear in target lineage."""
    document = _document()
    cues = tuple(
        cue.model_copy(update={"source_cue_ids": ("source-1", "source-2")})
        for cue in document.cues
    )
    changed = bind_translation_digest(document.model_copy(update={"cues": cues}))

    with pytest.raises(
        TranslationValidationError,
        match="unreferenced source cue",
    ):
        validate_translation(_source(), changed)


def test_validation_rejects_unknown_or_reversed_source_lineage() -> None:
    """Target lineage must exist and preserve source order."""
    document = _document()
    cue = document.cues[0].model_copy(
        update={"source_cue_ids": ("source-2", "source-1", "missing")}
    )
    changed = bind_translation_digest(
        document.model_copy(update={"cues": (cue, document.cues[1])})
    )

    with pytest.raises(
        TranslationValidationError,
        match=r"unknown source cue.*source order",
    ):
        validate_translation(_source(), changed)


def test_validation_rejects_target_outside_source_envelope() -> None:
    """Target timing cannot extend outside its source envelope."""
    document = _document()
    cue = document.cues[0].model_copy(
        update={
            "source_cue_ids": ("source-2",),
            "start_ms": 500,
            "end_ms": 2_000,
        }
    )
    changed = bind_translation_digest(
        document.model_copy(update={"cues": (cue, document.cues[1])})
    )

    with pytest.raises(
        TranslationValidationError,
        match="outside source envelope",
    ):
        validate_translation(_source(), changed)


def test_validation_rejects_wrong_speaker_and_unreadable_speed() -> None:
    """Wrong speakers and unreadable cue speed both fail validation."""
    document = _document()
    cue = document.cues[0].model_copy(
        update={
            "speakers": ("문하린",),
            "start_ms": 0,
            "end_ms": 100,
            "text": "This subtitle is far too long for one tenth of a second.",
        }
    )
    changed = bind_translation_digest(
        document.model_copy(update={"cues": (cue, document.cues[1])})
    )

    with pytest.raises(
        TranslationValidationError,
        match=r"speaker.*readability",
    ):
        validate_translation(_source(), changed)


def test_validation_rejects_forged_episode_context() -> None:
    """Digest-valid output cannot replace source episode context."""
    document = _document().model_copy(
        update={"context": EpisodeContext(summary="위조된 맥락")}
    )
    document = bind_translation_digest(document)

    with pytest.raises(TranslationValidationError, match="context mismatch"):
        _ = validate_translation(_source(), document)


def test_validation_rejects_relabeled_content_kind() -> None:
    """A homogeneous source kind cannot be relabeled in target output."""
    source = _source()
    cues = tuple(
        cue.model_copy(update={"content_kind": ContentKind.LYRICS})
        for cue in source.cues
    )
    source = bind_source_digest(source.model_copy(update={"cues": cues}))

    with pytest.raises(TranslationValidationError, match="wrong content kind"):
        _ = validate_translation(source, _document(source))


def test_validation_rejects_mixed_source_content_kinds() -> None:
    """One target cue cannot combine source cues with different kinds."""
    source = _source()
    cues = (
        source.cues[0].model_copy(update={"content_kind": ContentKind.LYRICS}),
        *source.cues[1:],
    )
    source = bind_source_digest(source.model_copy(update={"cues": cues}))

    with pytest.raises(TranslationValidationError, match="mixed source content kinds"):
        _ = validate_translation(source, _document(source))


def test_validation_rejects_omitted_source_evidence() -> None:
    """Target cues preserve all evidence attached to their source lineage."""
    source = _source()
    evidence = EvidenceManifest(
        base_uri="memory://source/",
        entries=(
            EvidenceEntry(
                evidence_id=evidence_id("frame-1"),
                kind=EvidenceKind.FRAME,
                media_type="image/jpeg",
                resource_digest="a" * 64,
                locator="frame-1.jpg",
                pts_ms=milliseconds(500),
            ),
        ),
    )
    cues = (
        source.cues[0].model_copy(update={"evidence_ids": ("frame-1",)}),
        *source.cues[1:],
    )
    source = bind_source_digest(
        source.model_copy(update={"evidence": evidence, "cues": cues})
    )

    with pytest.raises(TranslationValidationError, match="omits source evidence"):
        _ = validate_translation(source, _document(source))


def test_canonical_document_digest_ignores_public_key_order() -> None:
    """Equivalent public JSON has identical canonical bytes and digest."""
    document = _document()
    payload = document.model_dump(mode="json")
    reordered = json.dumps(dict(reversed(tuple(payload.items()))), ensure_ascii=False)
    restored = TranslationDocument.model_validate_json(reordered)

    assert canonical_json_bytes(document) == canonical_json_bytes(restored)
    assert translation_digest(document) == translation_digest(restored)


def test_validation_rejects_target_overlapping_none_of_its_source_lineage() -> None:
    """A target interval must intersect at least one referenced source interval."""
    document = _document()
    cue = document.cues[0].model_copy(
        update={
            "source_cue_ids": ("source-1", "source-3"),
            "start_ms": 1_400,
            "end_ms": 1_600,
        }
    )
    changed = bind_translation_digest(
        document.model_copy(update={"cues": (cue, document.cues[1])})
    )
    with pytest.raises(TranslationValidationError, match="overlap"):
        validate_translation(_source(), changed)


@pytest.mark.parametrize(
    "kind", ["empty evidence", "unknown evidence", "unordered source"]
)
def test_validation_rejects_invalid_retained_context(kind: str) -> None:
    """Retained context is ordered and grounded in known evidence."""
    context = RetainedContext(
        context_id=context_id("context-1"),
        summary="Opening context",
        source_cue_ids=(
            (SourceCueId("source-2"), SourceCueId("source-1"))
            if kind == "unordered source"
            else (SourceCueId("source-1"),)
        ),
        evidence_ids=(evidence_id("missing"),) if kind == "unknown evidence" else (),
    )
    document = _document().model_copy(update={"retained_contexts": (context,)})
    document = bind_translation_digest(document)
    with pytest.raises(TranslationValidationError, match="context"):
        validate_translation(_source(), document)


def test_validation_rejects_duplicate_unresolved_ids() -> None:
    """Validation rejects repeated unresolved identities even on malformed input."""
    item = UnresolvedItem.model_construct(
        unresolved_id="duplicate",
        source_cue_ids=(SourceCueId("source-1"),),
        target_cue_ids=(TargetCueId("target-1"),),
        evidence_ids=(),
    )
    document = bind_translation_digest(
        _document().model_copy(update={"unresolved": (item, item)})
    )
    with pytest.raises(TranslationValidationError, match=r"unresolved.*unique"):
        validate_translation(_source(), document)


def _two_speaker_split() -> tuple[SourceSnapshot, TranslationDocument]:
    source = _source()
    cues = (
        source.cues[0].model_copy(update={"speakers": ("A",)}),
        source.cues[1].model_copy(update={"speakers": ("B",)}),
        source.cues[2].model_copy(update={"speakers": ("A",)}),
    )
    source = bind_source_digest(source.model_copy(update={"cues": cues}))
    document = _document(source)
    targets = (
        document.cues[0].model_copy(update={"speakers": ("A",)}),
        document.cues[1].model_copy(update={"speakers": ("B",)}),
    )
    return source, bind_translation_digest(
        document.model_copy(update={"cues": targets})
    )


def test_validation_accepts_per_target_speaker_subset_with_aggregate_coverage() -> None:
    """Crosstalk may be split into separate A/B target cues."""
    source, document = _two_speaker_split()
    validate_translation(source, document)


@pytest.mark.parametrize(
    ("speakers", "match"), [(("C",), "unknown speaker"), (("A",), "speaker coverage")]
)
def test_validation_rejects_unknown_or_aggregate_omitted_speaker(
    speakers: tuple[str, ...], match: str
) -> None:
    """Targets cannot invent speakers or omit a source speaker in aggregate."""
    source, document = _two_speaker_split()
    cues = tuple(cue.model_copy(update={"speakers": speakers}) for cue in document.cues)
    document = bind_translation_digest(document.model_copy(update={"cues": cues}))
    with pytest.raises(TranslationValidationError, match=match):
        validate_translation(source, document)


def test_validation_rejects_stale_source_or_document_digest() -> None:
    """Stale source identity and document bytes cannot validate."""
    document = _document().model_copy(update={"source_digest": "f" * 64})

    with pytest.raises(
        TranslationValidationError,
        match=r"source digest.*document digest",
    ):
        validate_translation(_source(), document)
