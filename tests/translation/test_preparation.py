# Copyright 2026 Transcription Module contributors

"""Source publication, evidence preflight and request preparation tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from tests.translation.type_builders import evidence_id, milliseconds
from translation_module import preparation as preparation_module
from translation_module.common import SourceCueId
from translation_module.contracts import (
    ContentKind,
    EpisodeContext,
    EvidenceEntry,
    EvidenceKind,
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)
from translation_module.evidence import FileEvidenceResolver
from translation_module.evidence_preparation import preflight_evidence
from translation_module.preparation import (
    ChunkPlan,
    bind_source_digest,
    prepare_request,
    source_digest,
)
from translation_module.preparation_errors import PreparationError

FRAME_BYTES = b"frame evidence"
EVIDENCE_CUE_SEQUENCE = 2


def _source() -> SourceSnapshot:
    evidence = (
        EvidenceEntry(
            evidence_id=evidence_id("frame-1"),
            kind=EvidenceKind.FRAME,
            media_type="image/jpeg",
            resource_digest=(
                "b527167ab24240d9f2688d6ae8846898397edc87a2a0d75fa003163847cd7a2d"
            ),
            locator="frames/frame-1.jpg",
            pts_ms=milliseconds(500),
        ),
        EvidenceEntry(
            evidence_id=evidence_id("unused-frame"),
            kind=EvidenceKind.FRAME,
            media_type="image/jpeg",
            resource_digest="d" * 64,
            locator="frames/missing.jpg",
            pts_ms=milliseconds(4_000),
        ),
    )
    cues = tuple(
        SourceCue(
            sequence=index,
            cue_id=SourceCueId(f"source-{index}"),
            start_ms=milliseconds((index - 1) * 1_000),
            end_ms=milliseconds(index * 1_000),
            text=f"원문 {index}",
            speakers=("한지아",),
            content_kind=ContentKind.DIALOGUE,
            context_note=(f"cue context {index}" if index in (1, 2, 4) else None),
            evidence_ids=(
                (evidence_id("frame-1"),) if index == EVIDENCE_CUE_SEQUENCE else ()
            ),
        )
        for index in range(1, 6)
    )
    source = SourceSnapshot(
        document_id="source-fixture",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(6_000),
        context=EpisodeContext(summary="토크쇼와 공연"),
        evidence=EvidenceManifest(
            base_uri="memory://fixture/",
            entries=evidence,
        ),
        cues=cues,
    )
    return bind_source_digest(source)


@dataclass(frozen=True, slots=True)
class MemoryResolver:
    """In-memory evidence resolver used by preflight tests."""

    resources: dict[str, bytes]

    def read(self, base_uri: str, locator: str) -> bytes:
        """Return one resource from its complete URI."""
        return self.resources[f"{base_uri}{locator}"]


def _source_with_cue_count(cue_count: int) -> SourceSnapshot:
    source = _source()
    cues = tuple(
        source.cues[0].model_copy(
            update={
                "sequence": index,
                "cue_id": SourceCueId(f"source-{index}"),
                "start_ms": milliseconds((index - 1) * 1_000),
                "end_ms": milliseconds(index * 1_000),
                "text": f"이어지는 독백 {index}",
                "evidence_ids": (),
            }
        )
        for index in range(1, cue_count + 1)
    )
    return bind_source_digest(
        source.model_copy(
            update={
                "duration_ms": milliseconds((cue_count + 1) * 1_000),
                "cues": cues,
            }
        )
    )


def _cue_ids(*indexes: int) -> tuple[SourceCueId, ...]:
    return tuple(SourceCueId(f"source-{index}") for index in indexes)


def _plan(
    chunk_id: str,
    owned: tuple[int, ...],
    *,
    before: tuple[int, ...] = (),
    after: tuple[int, ...] = (),
) -> ChunkPlan:
    return ChunkPlan(
        chunk_id=chunk_id,
        owned_source_cue_ids=_cue_ids(*owned),
        context_before_source_cue_ids=_cue_ids(*before),
        context_after_source_cue_ids=_cue_ids(*after),
    )


def _source_with_context_evidence() -> SourceSnapshot:
    source = _source()
    context_entry = source.evidence.entries[0].model_copy(
        update={"evidence_id": evidence_id("context-frame")}
    )
    return bind_source_digest(
        source.model_copy(
            update={
                "context_evidence_ids": (evidence_id("context-frame"),),
                "evidence": source.evidence.model_copy(
                    update={
                        "entries": (*source.evidence.entries, context_entry),
                    }
                ),
            }
        )
    )


def test_source_digest_changes_when_source_text_changes() -> None:
    """Canonical source digest changes with source text."""
    source = _source()
    changed_cue = source.cues[0].model_copy(update={"text": "수정된 원문"})
    changed = source.model_copy(update={"cues": (changed_cue, *source.cues[1:])})

    assert source.content_digest == source_digest(source)
    assert source_digest(changed) != source.content_digest


def test_preflight_checks_only_referenced_evidence() -> None:
    """Preflight resolves referenced evidence and ignores unused entries."""
    source = _source()
    resolver = MemoryResolver(
        resources={"memory://fixture/frames/frame-1.jpg": FRAME_BYTES}
    )

    resolved = preflight_evidence(source, resolver)

    assert [entry.evidence_id for entry in resolved] == ["frame-1"]


def test_preflight_includes_source_context_evidence() -> None:
    """Episode-level evidence is verified even without a cue reference."""
    source = _source_with_context_evidence()
    resolver = MemoryResolver(
        resources={"memory://fixture/frames/frame-1.jpg": FRAME_BYTES}
    )

    resolved = preflight_evidence(source, resolver)

    assert tuple(entry.evidence_id for entry in resolved) == (
        "frame-1",
        "context-frame",
    )


def test_preflight_rejects_unknown_requested_evidence() -> None:
    """Explicit evidence requests cannot silently drop unknown IDs."""
    with pytest.raises(PreparationError, match="unknown evidence"):
        _ = preflight_evidence(
            _source(),
            MemoryResolver(resources={}),
            ("missing-frame",),
        )


def test_preflight_rejects_digest_mismatch() -> None:
    """Preflight rejects evidence whose bytes do not match its digest."""
    resolver = MemoryResolver(
        resources={"memory://fixture/frames/frame-1.jpg": b"wrong"}
    )

    with pytest.raises(PreparationError, match="digest mismatch"):
        _ = preflight_evidence(_source(), resolver)


def test_file_resolver_reads_regular_file_under_exact_root(tmp_path: Path) -> None:
    """File evidence is resolved relative to its declared root only."""
    frame = tmp_path / "frames" / "frame.jpg"
    frame.parent.mkdir()
    _ = frame.write_bytes(FRAME_BYTES)

    assert (
        FileEvidenceResolver(tmp_path).read(tmp_path.as_uri(), "frames/frame.jpg")
        == FRAME_BYTES
    )


@pytest.mark.parametrize(
    ("base_uri", "locator"),
    [
        ("https://example.com/evidence", "frame.jpg"),
        ("memory://fixture", "frame.jpg"),
        ("configured-root", "/etc/passwd"),
        ("configured-root", "../etc/passwd"),
    ],
)
def test_file_resolver_rejects_untrusted_location_forms(
    tmp_path: Path,
    base_uri: str,
    locator: str,
) -> None:
    """Only relative file locators beneath the configured root are accepted."""
    if base_uri == "configured-root":
        base_uri = tmp_path.as_uri()
    with pytest.raises(OSError, match=r"."):
        _ = FileEvidenceResolver(tmp_path).read(base_uri, locator)


def test_file_resolver_rejects_symlink_escape(tmp_path: Path) -> None:
    """Evidence cannot escape its root through a symlinked path component."""
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    _ = (outside / "frame.jpg").write_bytes(FRAME_BYTES)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match=r"."):
        _ = FileEvidenceResolver(tmp_path).read(tmp_path.as_uri(), "escape/frame.jpg")


def test_file_resolver_rejects_non_regular_resource(tmp_path: Path) -> None:
    """Directories are not evidence resources."""
    (tmp_path / "frames").mkdir()

    with pytest.raises(OSError, match="regular file"):
        _ = FileEvidenceResolver(tmp_path).read(tmp_path.as_uri(), "frames")


def test_single_chunk_plan_preserves_owned_and_read_only_context_roles() -> None:
    """A standalone plan keeps agent-selected ownership and context unchanged."""
    plan = ChunkPlan(
        chunk_id="semantic-boundary",
        owned_source_cue_ids=(SourceCueId("source-2"), SourceCueId("source-3")),
        context_before_source_cue_ids=(SourceCueId("source-1"),),
        context_after_source_cue_ids=(SourceCueId("source-4"),),
    )

    assert ChunkPlan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.owned_source_cue_ids == ("source-2", "source-3")
    assert plan.context_before_source_cue_ids == ("source-1",)
    assert plan.context_after_source_cue_ids == ("source-4",)


def test_batch_plan_covers_continuous_monologue_exactly_across_64_plus_1() -> None:
    """Context stays read-only while an arbitrary semantic split owns every cue."""
    source = _source_with_cue_count(65)
    plans = (
        _plan("monologue-1", tuple(range(1, 65)), after=(65,)),
        _plan("monologue-2", (65,), before=tuple(range(1, 65))),
    )

    validated = preparation_module.validate_batch_plan(source, plans)

    assert validated == plans
    assert tuple(
        cue_id for plan in validated for cue_id in plan.owned_source_cue_ids
    ) == tuple(cue.cue_id for cue in source.cues)


@pytest.mark.parametrize(
    "plans",
    [
        pytest.param(
            (_plan("chunk-1", (1, 2, 3)), _plan("chunk-2", (3, 4, 5))),
            id="duplicate-ownership",
        ),
        pytest.param(
            (_plan("chunk-1", (1, 2)), _plan("chunk-2", (4, 5))),
            id="one-missing-source-id",
        ),
        pytest.param(
            (_plan("chunk-1", (4, 5)), _plan("chunk-2", (1, 2, 3))),
            id="reversed-chunk-order",
        ),
        pytest.param(
            (_plan("chunk-1", (1, 3)), _plan("chunk-2", (2, 4, 5))),
            id="owned-cue-hole",
        ),
        pytest.param(
            (
                _plan("chunk-1", (1, 2)),
                _plan("chunk-2", (3, 4, 5), before=(1,)),
            ),
            id="context-before-hole",
        ),
        pytest.param(
            (
                _plan("chunk-1", (1, 2)),
                _plan("chunk-2", (3, 4, 5), after=(2,)),
            ),
            id="context-after-before-ownership",
        ),
        pytest.param(
            (
                _plan("chunk-1", (1, 2), before=(3,)),
                _plan("chunk-2", (3, 4, 5)),
            ),
            id="context-before-after-ownership",
        ),
        pytest.param(
            (
                _plan("chunk-1", (1, 2), after=(99,)),
                _plan("chunk-2", (3, 4, 5)),
            ),
            id="foreign-context-id",
        ),
    ],
)
def test_batch_plan_rejects_invalid_partition_or_context(
    plans: tuple[ChunkPlan, ...],
) -> None:
    """A full run rejects malformed ownership and context placement."""
    with pytest.raises(PreparationError):
        _ = preparation_module.validate_batch_plan(_source(), plans)


def test_chunk_plan_rejects_blank_chunk_id() -> None:
    """Chunk identity must contain a visible character at construction."""
    plan_created = False
    validator_reached = False

    def construct_and_validate() -> None:
        nonlocal plan_created, validator_reached
        _ = _plan("   ", (1, 2, 3, 4, 5))
        plan_created = True
        validator_reached = True
        _ = preparation_module.validate_batch_plan(_source(), ())

    with pytest.raises(ValidationError, match="chunk_id must be nonblank"):
        construct_and_validate()

    assert not plan_created
    assert not validator_reached


def test_batch_plan_rejects_duplicate_chunk_ids() -> None:
    """Two executions cannot share one chunk identity."""
    plans = (_plan("same", (1, 2)), _plan("same", (3, 4, 5)))

    with pytest.raises(PreparationError):
        _ = preparation_module.validate_batch_plan(_source(), plans)


@pytest.mark.parametrize("side", ["before", "after"])
def test_batch_plan_rejects_65_context_cues_per_side(side: str) -> None:
    """Read-only context has the same bounded execution ceiling on each side."""
    source = _source_with_cue_count(66)
    if side == "before":
        plans = (
            _plan("chunk-1", tuple(range(1, 65))),
            _plan("chunk-2", (65,)),
            _plan("chunk-3", (66,), before=tuple(range(1, 66))),
        )
    else:
        plans = (
            _plan("chunk-1", (1,), after=tuple(range(2, 67))),
            _plan("chunk-2", tuple(range(2, 66))),
            _plan("chunk-3", (66,)),
        )

    with pytest.raises(PreparationError, match="64"):
        _ = preparation_module.validate_batch_plan(source, plans)


def test_chunk_plan_accepts_64_owned_cues() -> None:
    """The execution cap includes a 64-cue semantic chunk."""
    cue_ids = tuple(SourceCueId(f"source-{index}") for index in range(1, 65))

    plan = ChunkPlan(chunk_id="bounded-chunk", owned_source_cue_ids=cue_ids)

    assert plan.owned_source_cue_ids == cue_ids


def test_chunk_plan_rejects_65_owned_cues() -> None:
    """The execution cap rejects the first oversized chunk."""
    with pytest.raises(ValidationError, match="64"):
        _ = ChunkPlan(
            chunk_id="oversized-chunk",
            owned_source_cue_ids=tuple(
                SourceCueId(f"source-{index}") for index in range(1, 66)
            ),
        )


def test_chunk_plan_rejects_514_owned_cues() -> None:
    """One agent execution cannot own an entire 514-cue video."""
    with pytest.raises(ValidationError, match="64"):
        _ = ChunkPlan(
            chunk_id="whole-video",
            owned_source_cue_ids=tuple(
                SourceCueId(f"source-{index}") for index in range(1, 515)
            ),
        )


def test_prepare_request_preflights_only_included_evidence() -> None:
    """Request preparation does not touch evidence outside its cue plan."""
    request = prepare_request(
        _source(),
        ChunkPlan(chunk_id="chunk-1", owned_source_cue_ids=(SourceCueId("source-1"),)),
        MemoryResolver(resources={}),
    )

    assert request.evidence == ()


def test_prepare_request_carries_draft_source_maturity() -> None:
    """Draft candidates remain identifiable after successful preparation."""
    source = bind_source_digest(
        _source().model_copy(update={"maturity": SourceMaturity.DRAFT})
    )

    request = prepare_request(
        source,
        ChunkPlan(
            chunk_id="draft-chunk",
            owned_source_cue_ids=(SourceCueId("source-1"),),
        ),
        MemoryResolver(resources={}),
    )

    assert request.source_maturity is SourceMaturity.DRAFT


def test_prepare_request_includes_source_context_evidence() -> None:
    """Every chunk receives explicit episode evidence without cue duplication."""
    request = prepare_request(
        _source_with_context_evidence(),
        ChunkPlan(
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-1"),),
        ),
        MemoryResolver(resources={"memory://fixture/frames/frame-1.jpg": FRAME_BYTES}),
    )

    assert request.context_evidence_ids == ("context-frame",)
    assert tuple(entry.evidence_id for entry in request.evidence) == ("context-frame",)
    assert request.owned_cues[0].evidence_ids == ()


def test_prepare_request_rejects_evidence_digest_mismatch() -> None:
    """A request is not prepared when included evidence changed."""
    plan = ChunkPlan(
        chunk_id="chunk-1", owned_source_cue_ids=(SourceCueId("source-2"),)
    )
    resolver = MemoryResolver(
        resources={"memory://fixture/frames/frame-1.jpg": b"wrong"}
    )

    with pytest.raises(PreparationError, match="digest mismatch"):
        _ = prepare_request(_source(), plan, resolver)


def test_prepare_request_rejects_invalid_evidence_pts() -> None:
    """Included evidence PTS is checked again at preparation time."""
    source = _source()
    invalid_entry = source.evidence.entries[0].model_copy(
        update={"pts_ms": source.duration_ms}
    )
    invalid_manifest = source.evidence.model_copy(
        update={"entries": (invalid_entry, *source.evidence.entries[1:])}
    )
    invalid_source = bind_source_digest(
        source.model_copy(update={"evidence": invalid_manifest})
    )
    plan = ChunkPlan(
        chunk_id="chunk-1", owned_source_cue_ids=(SourceCueId("source-2"),)
    )
    resolver = MemoryResolver(
        resources={"memory://fixture/frames/frame-1.jpg": FRAME_BYTES}
    )

    with pytest.raises(PreparationError, match="PTS"):
        _ = prepare_request(invalid_source, plan, resolver)


def test_prepare_request_separates_owned_and_read_only_context() -> None:
    """Prepared requests isolate output ownership from read-only context."""
    request = prepare_request(
        _source(),
        ChunkPlan(
            chunk_id="chunk-1",
            owned_source_cue_ids=(SourceCueId("source-2"), SourceCueId("source-3")),
            context_before_source_cue_ids=(SourceCueId("source-1"),),
            context_after_source_cue_ids=(
                SourceCueId("source-4"),
                SourceCueId("source-5"),
            ),
        ),
        MemoryResolver(resources={"memory://fixture/frames/frame-1.jpg": FRAME_BYTES}),
    )

    assert [cue.cue_id for cue in request.owned_cues] == [
        "source-2",
        "source-3",
    ]
    assert [cue.cue_id for cue in request.context_before] == ["source-1"]
    assert [cue.cue_id for cue in request.context_after] == [
        "source-4",
        "source-5",
    ]
    assert request.context.summary == "토크쇼와 공연"
    assert tuple(cue.context_note for cue in request.owned_cues) == (
        "cue context 2",
        None,
    )
    assert request.context_before[0].context_note == "cue context 1"
    assert request.context_after[0].context_note == "cue context 4"
    assert "cue context" not in request.context.summary
    assert [entry.evidence_id for entry in request.evidence] == ["frame-1"]


def test_prepare_request_rejects_unknown_source_cue() -> None:
    """Chunk plans cannot request absent source cues."""
    plan = ChunkPlan(
        chunk_id="chunk-1",
        owned_source_cue_ids=(SourceCueId("source-2"), SourceCueId("missing")),
    )

    with pytest.raises(PreparationError, match="unknown source cue"):
        _ = prepare_request(_source(), plan, MemoryResolver(resources={}))
