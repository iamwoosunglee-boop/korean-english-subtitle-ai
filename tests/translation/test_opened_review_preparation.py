# Copyright 2026 Transcription Module contributors

"""Canonical review preparation from explicit frame-open records."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.translation.evidence_open_support import (
    FRAME_A,
    LOCATOR_A,
    LOCATOR_B,
    LedgerResolver,
    candidate_fixture,
    evidence_context,
    source_fixture,
)
from translation_module.canonical.common import DocumentReference
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenError,
    EvidenceOpenRecordError,
)
from translation_module.evidence_open_preparation import (
    EvidenceOpenSelection,
    OpenedReviewInput,
    build_opened_review_request,
)
from translation_module.evidence_open_store import EvidenceOpenStore


def _resolver() -> LedgerResolver:
    return LedgerResolver(
        resources={
            f"memory://evidence/{LOCATOR_A}": FRAME_A,
            f"memory://evidence/{LOCATOR_B}": b"corrupt-b",
        },
        calls=[],
    )


def test_review_requires_open_records_without_implicitly_reading_frames(
    tmp_path: Path,
) -> None:
    """Unopened B fails assembly, and only explicit open B causes its read."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    source_before = source.model_dump_json()
    candidate_before = candidate.model_dump_json()
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)
    _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    inputs = OpenedReviewInput(
        selection=EvidenceOpenSelection(
            source=source,
            evidence_ids=("frame-a", "frame-b"),
            actor_role=EvidenceActorRole.REVIEWER,
            consumer_id="review-1",
            consumer_content_digest=candidate.content_digest,
        ),
        candidate=candidate,
    )

    with pytest.raises(EvidenceOpenRecordError, match="not explicitly opened"):
        _ = build_opened_review_request(inputs, store)
    assert resolver.calls == [LOCATOR_A]

    with pytest.raises(EvidenceOpenError, match="digest mismatch"):
        _ = store.open_frame("frame-b", EvidenceActorRole.REVIEWER, "review-1")
    assert resolver.calls == [LOCATOR_A, LOCATOR_B]
    assert source.model_dump_json() == source_before
    assert candidate.model_dump_json() == candidate_before


def test_review_accepts_only_matching_open_frames_and_nonblocking_asr(
    tmp_path: Path,
) -> None:
    """ASR provenance needs no open proof, while reviewer controls stay parent-bound."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)
    _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    inputs = OpenedReviewInput(
        selection=EvidenceOpenSelection(
            source=source,
            evidence_ids=("frame-a", "asr-ko-vtt"),
            actor_role=EvidenceActorRole.REVIEWER,
            consumer_id="review-1",
            consumer_content_digest=candidate.content_digest,
        ),
        candidate=candidate,
    )

    request = build_opened_review_request(inputs, store)

    assert resolver.calls == [LOCATOR_A]
    assert tuple(record.evidence_id for record in request.evidence) == ("frame-a",)
    assert request.reviewer_id == "review-1"
    assert request.evidence[0].actor_role is EvidenceActorRole.REVIEWER
    assert "rationale" not in request.model_dump()


def test_review_rejects_translator_open_role() -> None:
    """Translator-open proof cannot authorize independent reviewer input."""
    source = source_fixture()
    candidate = candidate_fixture(source)

    with pytest.raises(ValidationError, match="reviewer-open"):
        _ = OpenedReviewInput(
            selection=EvidenceOpenSelection(
                source=source,
                evidence_ids=("frame-a",),
                actor_role=EvidenceActorRole.TRANSLATOR,
                consumer_id="attempt-1",
                consumer_content_digest=candidate.content_digest,
            ),
            candidate=candidate,
        )


def test_review_rejects_stale_candidate_without_frame_reads(tmp_path: Path) -> None:
    """A candidate bound to another source revision cannot consume open proof."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    stale = bind_canonical_digests(
        candidate.model_copy(
            update={
                "source_ref": DocumentReference(
                    document_id=source.document_id,
                    revision_id="older-source-revision",
                    content_digest=source.content_digest,
                )
            }
        )
    )
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)

    with pytest.raises(EvidenceOpenRecordError, match="source binding is stale"):
        _ = build_opened_review_request(
            OpenedReviewInput(
                selection=EvidenceOpenSelection(
                    source=source,
                    evidence_ids=("frame-a",),
                    actor_role=EvidenceActorRole.REVIEWER,
                    consumer_id="review-1",
                    consumer_content_digest=stale.content_digest,
                ),
                candidate=stale,
            ),
            store,
        )
    assert resolver.calls == []


def test_review_rejects_unknown_and_duplicate_evidence_ids(tmp_path: Path) -> None:
    """Malformed evidence scope fails before any resolver access."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)

    with pytest.raises(ValidationError, match="unique"):
        _ = EvidenceOpenSelection(
            source=source,
            evidence_ids=("frame-a", "frame-a"),
            actor_role=EvidenceActorRole.REVIEWER,
            consumer_id="review-1",
            consumer_content_digest=candidate.content_digest,
        )
    with pytest.raises(EvidenceOpenRecordError, match="not in source"):
        _ = build_opened_review_request(
            OpenedReviewInput(
                selection=EvidenceOpenSelection(
                    source=source,
                    evidence_ids=("unknown",),
                    actor_role=EvidenceActorRole.REVIEWER,
                    consumer_id="review-1",
                    consumer_content_digest=candidate.content_digest,
                ),
                candidate=candidate,
            ),
            store,
        )
    assert resolver.calls == []


def test_review_revalidates_store_records_against_supplied_source(
    tmp_path: Path,
) -> None:
    """A store bound to an older locator cannot authorize the supplied source."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)
    _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    moved_entry = source.evidence.entries[0].model_copy(
        update={"locator": "frames/moved-a.jpg"}
    )
    moved_source = bind_canonical_digests(
        source.model_copy(
            update={
                "evidence": source.evidence.model_copy(
                    update={"entries": (moved_entry, *source.evidence.entries[1:])}
                )
            }
        )
    )

    moved_candidate = candidate_fixture(moved_source)
    with pytest.raises(EvidenceOpenRecordError, match="binding"):
        _ = build_opened_review_request(
            OpenedReviewInput(
                selection=EvidenceOpenSelection(
                    source=moved_source,
                    evidence_ids=("frame-a",),
                    actor_role=EvidenceActorRole.REVIEWER,
                    consumer_id="review-1",
                    consumer_content_digest=moved_candidate.content_digest,
                ),
                candidate=moved_candidate,
            ),
            store,
        )
    assert resolver.calls == [LOCATOR_A]
