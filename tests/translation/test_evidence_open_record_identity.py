# Copyright 2026 Transcription Module contributors

"""Deterministic full-authority evidence-open record identity."""

from pathlib import Path
from typing import Literal

import pytest

from tests.translation.evidence_open_support import (
    FRAME_A,
    LOCATOR_A,
    LedgerResolver,
    candidate_fixture,
    evidence_context,
    source_fixture,
)
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.evidence_open_identity import (
    evidence_open_identity,
    evidence_open_record_name,
)
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenRequest,
)
from translation_module.evidence_open_store import EvidenceOpenStore

AuthorityField = Literal[
    "source_content_digest",
    "locator",
    "declared_resource_digest",
    "observed_resource_digest",
]


def _request(candidate_digest: str) -> EvidenceOpenRequest:
    return EvidenceOpenRequest(
        evidence_id="frame-a",
        actor_role=EvidenceActorRole.REVIEWER,
        consumer_id="review-1",
        consumer_content_digest=candidate_digest,
    )


@pytest.mark.parametrize(
    "field",
    [
        "source_content_digest",
        "locator",
        "declared_resource_digest",
        "observed_resource_digest",
    ],
)
def test_record_path_changes_for_every_authority_field(
    field: AuthorityField,
) -> None:
    """Changing exactly one immutable identity field selects another path."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    identity = evidence_open_identity(
        evidence_context(source, candidate),
        _request(candidate.content_digest),
        source.evidence.entries[0].resource_digest,
    )
    updates = {
        "source_content_digest": {"source_content_digest": "e" * 64},
        "locator": {"normalized_locator": "frames/other.jpg"},
        "declared_resource_digest": {"declared_resource_digest": "e" * 64},
        "observed_resource_digest": {"observed_resource_digest": "e" * 64},
    }

    changed = identity.model_copy(update=updates[field])

    assert evidence_open_record_name(changed) != evidence_open_record_name(identity)


def test_exact_identity_replay_keeps_path_and_bytes(tmp_path: Path) -> None:
    """Exact replay fully revalidates one unchanged canonical identity."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    resolver = LedgerResolver(
        resources={f"memory://evidence/{LOCATOR_A}": FRAME_A},
        calls=[],
    )
    store = EvidenceOpenStore(tmp_path, evidence_context(source, candidate), resolver)
    request = _request(candidate.content_digest)
    path = store.record_path(request)

    first = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    committed = path.read_bytes()
    replayed = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")

    assert replayed == first
    assert store.record_path(request) == path
    assert path.read_bytes() == committed
    assert resolver.calls == [LOCATOR_A]


def test_equivalent_locator_spelling_has_one_normalized_identity(
    tmp_path: Path,
) -> None:
    """Equivalent POSIX locator spelling hashes to one canonical path."""
    source = source_fixture()
    candidate = candidate_fixture(source)
    request = _request(candidate.content_digest)
    original = EvidenceOpenStore(
        tmp_path, evidence_context(source, candidate), None
    ).record_path(request)
    changed_entry = source.evidence.entries[0].model_copy(
        update={"locator": "frames//a.jpg"}
    )
    changed_source = bind_canonical_digests(
        source.model_copy(
            update={
                "evidence": source.evidence.model_copy(
                    update={"entries": (changed_entry, *source.evidence.entries[1:])}
                )
            }
        )
    )
    changed_candidate = candidate_fixture(changed_source)

    normalized = EvidenceOpenStore(
        tmp_path,
        evidence_context(changed_source, changed_candidate),
        None,
    ).record_path(_request(changed_candidate.content_digest))

    assert normalized == original
