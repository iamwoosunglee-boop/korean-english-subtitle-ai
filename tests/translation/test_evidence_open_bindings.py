# Copyright 2026 Transcription Module contributors

"""Consumer and candidate bindings for explicit frame-open records."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from tests.translation.evidence_open_support import (
    FRAME_A,
    LOCATOR_A,
    LedgerResolver,
    candidate_fixture,
    evidence_context,
    source_fixture,
)
from translation_module import evidence_open_store as store_module
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.cli_io import write_model_atomic
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenRecordError,
)
from translation_module.evidence_open_preparation import (
    EvidenceOpenSelection,
    OpenedReviewInput,
    build_opened_review_request,
)
from translation_module.evidence_open_store import EvidenceOpenStore

EXPECTED_RECORD_COUNT = 2


def test_second_consumer_reads_again_and_gets_a_distinct_record(tmp_path: Path) -> None:
    """Two legitimate reviewers retain separate explicit-open proof."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = LedgerResolver(
        resources={f"memory://evidence/{LOCATOR_A}": FRAME_A},
        calls=[],
    )
    context = evidence_context(source, candidate)
    first = EvidenceOpenStore(tmp_path, context, resolver).open_frame(
        "frame-a", EvidenceActorRole.REVIEWER, "review-1"
    )
    second = EvidenceOpenStore(tmp_path, context, resolver).open_frame(
        "frame-a", EvidenceActorRole.REVIEWER, "review-2"
    )

    assert first.consumer_id == "review-1"
    assert second.consumer_id == "review-2"
    assert resolver.calls == [LOCATOR_A, LOCATOR_A]
    assert len(tuple(tmp_path.glob("evidence-open.*.json"))) == EXPECTED_RECORD_COUNT


def test_changed_candidate_rejects_old_record_for_same_consumer(tmp_path: Path) -> None:
    """A reviewer ID cannot reuse proof opened for different candidate content."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = LedgerResolver(
        resources={f"memory://evidence/{LOCATOR_A}": FRAME_A},
        calls=[],
    )
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)
    _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    changed_target = candidate.cues[0].model_copy(update={"text": "Changed."})
    changed_candidate = bind_canonical_digests(
        candidate.model_copy(update={"cues": (changed_target,)})
    )

    changed_store = EvidenceOpenStore(
        tmp_path,
        evidence_context(source, changed_candidate),
        resolver,
    )
    with pytest.raises(EvidenceOpenRecordError, match="not explicitly opened"):
        _ = build_opened_review_request(
            OpenedReviewInput(
                selection=EvidenceOpenSelection(
                    source=source,
                    evidence_ids=("frame-a",),
                    actor_role=EvidenceActorRole.REVIEWER,
                    consumer_id="review-1",
                    consumer_content_digest=changed_candidate.content_digest,
                ),
                candidate=changed_candidate,
            ),
            changed_store,
        )
    assert resolver.calls == [LOCATOR_A]


def test_same_consumer_with_second_role_gets_distinct_record(tmp_path: Path) -> None:
    """Translator and reviewer opens cannot alias one persisted identity."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = LedgerResolver(
        resources={f"memory://evidence/{LOCATOR_A}": FRAME_A},
        calls=[],
    )
    context = evidence_context(source, candidate)

    _ = EvidenceOpenStore(tmp_path, context, resolver).open_frame(
        "frame-a", EvidenceActorRole.TRANSLATOR, "shared-consumer"
    )
    _ = EvidenceOpenStore(tmp_path, context, resolver).open_frame(
        "frame-a", EvidenceActorRole.REVIEWER, "shared-consumer"
    )

    assert resolver.calls == [LOCATOR_A, LOCATOR_A]
    assert len(tuple(tmp_path.glob("evidence-open.*.json"))) == EXPECTED_RECORD_COUNT


def test_retry_after_post_rename_interrupt_replays_committed_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry revalidates a fully renamed record without a second frame read."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = LedgerResolver(
        resources={f"memory://evidence/{LOCATOR_A}": FRAME_A},
        calls=[],
    )
    store = EvidenceOpenStore(
        tmp_path,
        evidence_context(source, candidate),
        resolver,
    )

    def commit_then_interrupt(path: Path, model: BaseModel) -> None:
        write_model_atomic(path, model)
        raise KeyboardInterrupt

    monkeypatch.setattr(store_module, "write_model_atomic", commit_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")

    replayed = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")

    assert replayed.consumer_content_digest == candidate.content_digest
    assert resolver.calls == [LOCATOR_A]
    assert len(tuple(tmp_path.glob("evidence-open.*.json"))) == 1
