# Copyright 2026 Transcription Module contributors

"""Transient chunk candidate and whole-document assembly tests."""

from __future__ import annotations

import json

import pytest

import translation_module.candidate as candidate_module
from tests.translation.type_builders import context_id, evidence_id, milliseconds
from translation_module.candidate import (
    CandidateAssemblyError,
    ChunkCandidate,
    assemble_candidate,
)
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    SourceCueId,
    TargetCueId,
    canonical_json_bytes,
)
from translation_module.document import (
    DocumentStatus,
    RetainedContext,
    TargetCue,
    TranslationDocument,
    translation_digest,
)
from translation_module.preparation import (
    ChunkPlan,
    TranslationRequest,
    bind_source_digest,
)
from translation_module.source import (
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)


def _source() -> SourceSnapshot:
    source = SourceSnapshot(
        document_id="source-document",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(4_000),
        context=EpisodeContext(summary="경계를 넘는 한 문장"),
        evidence=EvidenceManifest(
            base_uri="memory://source/",
            entries=(
                EvidenceEntry(
                    evidence_id=evidence_id("evidence-1"),
                    kind=EvidenceKind.TEXT,
                    media_type="text/plain",
                    resource_digest="e" * 64,
                    locator="context.txt",
                ),
            ),
        ),
        cues=tuple(
            SourceCue(
                sequence=index,
                cue_id=SourceCueId(f"source-{index}"),
                start_ms=milliseconds((index - 1) * 1_000),
                end_ms=milliseconds(index * 1_000),
                text=f"이어지는 문장 {index}",
                speakers=("speaker-1",),
                content_kind=ContentKind.DIALOGUE,
            )
            for index in range(1, 5)
        ),
    )
    return bind_source_digest(source)


def _plans() -> tuple[ChunkPlan, ...]:
    return (
        ChunkPlan(
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
            context_after_source_cue_ids=(SourceCueId("source-3"),),
        ),
        ChunkPlan(
            chunk_id="chunk-2",
            owned_source_cue_ids=(SourceCueId("source-3"), SourceCueId("source-4")),
            context_before_source_cue_ids=(SourceCueId("source-2"),),
        ),
    )


def _target(source_index: int, target_id: str, *, sequence: int = 99) -> TargetCue:
    return TargetCue(
        sequence=sequence,
        cue_id=TargetCueId(target_id),
        source_cue_ids=(SourceCueId(f"source-{source_index}"),),
        start_ms=milliseconds((source_index - 1) * 1_000),
        end_ms=milliseconds(source_index * 1_000),
        text=f"Sentence part {source_index}.",
        speakers=("speaker-1",),
        content_kind=ContentKind.DIALOGUE,
    )


def _context() -> RetainedContext:
    return RetainedContext(
        context_id=context_id("stable-context"),
        summary="The sentence continues across the chunk boundary.",
        source_cue_ids=(SourceCueId("source-2"), SourceCueId("source-3")),
        evidence_ids=(evidence_id("evidence-1"),),
    )


def _candidates() -> tuple[ChunkCandidate, ...]:
    source = _source()
    return (
        ChunkCandidate(
            source_digest=source.content_digest,
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
            cues=(_target(2, "agent-target-2"), _target(1, "agent-target-1")),
            retained_contexts=(_context(),),
        ),
        ChunkCandidate(
            source_digest=source.content_digest,
            chunk_id="chunk-2",
            owned_source_cue_ids=(SourceCueId("source-3"), SourceCueId("source-4")),
            cues=(_target(4, "agent-target-4"), _target(3, "agent-target-3")),
            retained_contexts=(_context(),),
        ),
    )


def _assemble(
    candidates: tuple[ChunkCandidate, ...] | None = None,
) -> TranslationDocument:
    return assemble_candidate(
        _source(),
        _plans(),
        candidates or _candidates(),
        document_id="translation-document",
    )


def test_translation_request_and_document_round_trip_current_contracts() -> None:
    """The existing public handoff endpoints retain strict JSON round trips."""
    source = _source()
    request = TranslationRequest(
        chunk_id="chunk-1",
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_maturity=source.maturity,
        source_locale=source.source_locale,
        context=source.context,
        context_evidence_ids=(),
        owned_cues=(source.cues[0],),
    )
    document = TranslationDocument(
        document_id="translation-document",
        content_digest="b" * 64,
        source_document_id=source.document_id,
        source_digest=source.content_digest,
        source_locale="ko-KR",
        target_locale="en-US",
        context=request.context,
        retained_contexts=(),
        cues=(_target(1, "target-1", sequence=1),),
        status=DocumentStatus.FINALIZED,
    )

    assert TranslationRequest.model_validate_json(request.model_dump_json()) == request
    assert (
        TranslationDocument.model_validate_json(document.model_dump_json()) == document
    )


def test_assembly_is_deterministic_and_preserves_transient_handoff_data() -> None:
    """Plan/source order wins while agent target IDs and stable contexts survive."""
    first = _assemble()
    second = _assemble(tuple(reversed(_candidates())))

    assert first == second
    assert tuple(cue.cue_id for cue in first.cues) == (
        "agent-target-1",
        "agent-target-2",
        "agent-target-3",
        "agent-target-4",
    )
    assert tuple(cue.sequence for cue in first.cues) == (1, 2, 3, 4)
    assert first.retained_contexts == (_context(),)
    assert first.content_digest == translation_digest(first)
    assert first.schema_version == "translation-document.v1"
    assert TranslationDocument.model_validate_json(first.model_dump_json()) == first
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert "chunk_id" not in json.loads(first.model_dump_json())


def test_assembly_accepts_ordered_display_split_within_one_source_lineage() -> None:
    """One owned lyric cue can span multiple readable display cues."""
    source_id = SourceCueId("99e64c84-3e48-4ffb-a27e-765dbcaa6434")
    source_text = (
        "Oh, oh, oh, oh, oh, oh, oh, oh It could never be too much "
        "We'll take it all We'll carry on, carry on, carry on"
    )
    source = bind_source_digest(
        _source().model_copy(
            update={
                "duration_ms": milliseconds(1_845_801),
                "cues": (
                    SourceCue(
                        sequence=1,
                        cue_id=source_id,
                        start_ms=milliseconds(1_838_600),
                        end_ms=milliseconds(1_845_801),
                        text=source_text,
                        speakers=("speaker-1",),
                        content_kind=ContentKind.LYRICS,
                    ),
                ),
            }
        )
    )
    plan = ChunkPlan(
        chunk_id="carry-on",
        owned_source_cue_ids=(source_id,),
    )
    split_texts = (
        "Oh, oh, oh, oh, oh, oh, oh, oh\nIt could never be too much",
        "We'll take it all\nWe'll carry on, carry on, carry on",
    )
    split_timings = ((1_838_600, 1_842_201), (1_842_201, 1_845_801))
    cues = tuple(
        TargetCue(
            sequence=sequence,
            cue_id=TargetCueId(f"carry-on-{sequence}"),
            source_cue_ids=(source_id,),
            start_ms=milliseconds(start_ms),
            end_ms=milliseconds(end_ms),
            text=text,
            speakers=("speaker-1",),
            content_kind=ContentKind.LYRICS,
        )
        for sequence, (text, (start_ms, end_ms)) in enumerate(
            zip(split_texts, split_timings, strict=True),
            start=1,
        )
    )
    candidate = ChunkCandidate(
        source_digest=source.content_digest,
        chunk_id=plan.chunk_id,
        owned_source_cue_ids=plan.owned_source_cue_ids,
        cues=cues,
    )

    document = assemble_candidate(
        source,
        (plan,),
        (candidate,),
        document_id="carry-on-translation",
    )
    repeated = assemble_candidate(
        source,
        (plan,),
        (candidate.model_copy(update={"cues": tuple(reversed(cues))}),),
        document_id="carry-on-translation",
    )

    assert tuple(cue.source_cue_ids for cue in document.cues) == (
        (source_id,),
        (source_id,),
    )
    assert tuple((cue.start_ms, cue.end_ms) for cue in document.cues) == split_timings
    assert " ".join(cue.text.replace("\n", " ") for cue in document.cues) == source_text
    assert canonical_json_bytes(document) == canonical_json_bytes(repeated)
    assert document.content_digest == translation_digest(document)


_BINDING_MUTATIONS = frozenset(
    {
        "missing-chunk",
        "foreign-chunk",
        "stale-digest",
        "missing-ownership",
        "duplicate-ownership",
    }
)


def _invalid_candidates(mutation: str) -> tuple[ChunkCandidate, ...]:
    candidates = list(_candidates())
    if mutation in _BINDING_MUTATIONS:
        _mutate_candidate_binding(candidates, mutation)
    else:
        _mutate_candidate_cues(candidates, mutation)
    return tuple(candidates)


def _mutate_candidate_binding(
    candidates: list[ChunkCandidate],
    mutation: str,
) -> None:
    first, second = candidates
    if mutation == "missing-chunk":
        _ = candidates.pop()
    elif mutation == "foreign-chunk":
        candidates[1] = second.model_copy(update={"chunk_id": "foreign"})
    elif mutation == "stale-digest":
        candidates[1] = second.model_copy(update={"source_digest": "f" * 64})
    elif mutation == "missing-ownership":
        candidates[0] = first.model_copy(
            update={"owned_source_cue_ids": (SourceCueId("source-1"),)}
        )
    elif mutation == "duplicate-ownership":
        candidates[1] = second.model_copy(
            update={
                "owned_source_cue_ids": (
                    SourceCueId("source-2"),
                    SourceCueId("source-3"),
                    SourceCueId("source-4"),
                )
            }
        )


def _mutate_candidate_cues(
    candidates: list[ChunkCandidate],
    mutation: str,
) -> None:
    first, second = candidates
    if mutation in {"missing-owned-output", "missing-seam-cue"}:
        candidates[1] = second.model_copy(update={"cues": (second.cues[0],)})
    elif mutation == "foreign-source":
        candidates[0] = first.model_copy(
            update={
                "cues": (
                    first.cues[0].model_copy(
                        update={"source_cue_ids": (SourceCueId("foreign"),)}
                    ),
                    first.cues[1],
                )
            }
        )
    elif mutation == "context-only-output":
        candidates[1] = second.model_copy(
            update={"cues": (*second.cues, _target(2, "context-only"))}
        )
    elif mutation == "duplicate-source-output":
        candidates[1] = second.model_copy(
            update={
                "cues": (
                    *second.cues,
                    _target(3, "duplicate-source-3"),
                )
            }
        )
    elif mutation == "duplicate-target-id":
        candidates[1] = second.model_copy(
            update={
                "cues": (
                    second.cues[0].model_copy(update={"cue_id": first.cues[0].cue_id}),
                    second.cues[1],
                )
            }
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("missing-chunk", "missing chunk"),
        ("foreign-chunk", "foreign chunk"),
        ("stale-digest", "source digest"),
        ("missing-ownership", "ownership"),
        ("duplicate-ownership", "ownership"),
        ("missing-owned-output", "missing owned output"),
        ("missing-seam-cue", "missing owned output"),
        ("foreign-source", "foreign source"),
        ("context-only-output", "context-only"),
        ("duplicate-source-output", "duplicate source output"),
        ("duplicate-target-id", "target IDs"),
    ],
)
def test_assembly_rejects_invalid_chunk_handoff(mutation: str, match: str) -> None:
    """Malformed handoffs fail before a canonical document can escape."""
    with pytest.raises(CandidateAssemblyError, match=match):
        _ = _assemble(_invalid_candidates(mutation))


def test_assembly_rejects_conflicting_context_with_same_stable_id() -> None:
    """Stable context IDs dedupe only byte-identical values."""
    candidates = list(_candidates())
    changed = _context().model_copy(update={"summary": "Conflicting summary."})
    candidates[1] = candidates[1].model_copy(update={"retained_contexts": (changed,)})

    with pytest.raises(CandidateAssemblyError, match="retained context"):
        _ = _assemble(tuple(candidates))


def test_invalid_handoff_does_not_construct_a_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-assembly defects are failure-atomic at the document boundary."""
    candidates = _candidates()
    duplicate = candidates[1].model_copy(
        update={
            "cues": (
                candidates[1]
                .cues[0]
                .model_copy(update={"cue_id": candidates[0].cues[0].cue_id}),
                candidates[1].cues[1],
            )
        }
    )
    constructed = False

    def record_construction(**_kwargs: object) -> None:
        nonlocal constructed
        constructed = True

    monkeypatch.setattr(candidate_module, "TranslationDocument", record_construction)
    with pytest.raises(CandidateAssemblyError, match="target IDs"):
        _ = _assemble((candidates[0], duplicate))
    assert not constructed


def test_assembly_rejects_digest_rebound_source_with_invalid_sequences() -> None:
    """A rebound digest cannot bypass source-model validation."""
    source = _source()
    cues = tuple(
        source.cues[min(index, len(source.cues) - 1)].model_copy(
            update={
                "sequence": sequence,
                "cue_id": SourceCueId(f"source-{index + 1}"),
                "start_ms": milliseconds(index * 1_000),
                "end_ms": milliseconds((index + 1) * 1_000),
            }
        )
        for index, sequence in enumerate((1, 99, 2, 3, 4))
    )
    malformed = bind_source_digest(
        source.model_copy(update={"duration_ms": milliseconds(5_000), "cues": cues})
    )
    plans = (
        ChunkPlan(
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-1"), SourceCueId("source-2")),
        ),
        ChunkPlan(
            chunk_id="chunk-2",
            owned_source_cue_ids=(
                SourceCueId("source-3"),
                SourceCueId("source-4"),
                SourceCueId("source-5"),
            ),
        ),
    )
    candidates = tuple(
        ChunkCandidate(
            source_digest=malformed.content_digest,
            chunk_id=plan.chunk_id,
            owned_source_cue_ids=plan.owned_source_cue_ids,
            cues=tuple(
                _target(
                    int(str(source_id).removeprefix("source-")),
                    f"target-{source_id}",
                )
                for source_id in plan.owned_source_cue_ids
            ),
        )
        for plan in plans
    )
    document: TranslationDocument | None = None

    with pytest.raises(CandidateAssemblyError, match="source"):
        document = assemble_candidate(
            malformed,
            plans,
            candidates,
            document_id="must-not-exist",
        )

    assert document is None
