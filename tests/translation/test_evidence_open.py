# Copyright 2026 Transcription Module contributors

"""Explicit canonical frame-open evidence tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from tests.translation.evidence_open_support import (
    FRAME_A,
    FRAME_A_DIGEST,
    FRAME_B_DIGEST,
    LOCATOR_A,
    LOCATOR_B,
    LedgerResolver,
    evidence_context,
    source_fixture,
)
from translation_module._cli_io_namespace import BoundOutput
from translation_module.canonical.digest import bind_canonical_digests
from translation_module.evidence_open_models import (
    EvidenceActorRole,
    EvidenceOpenError,
    EvidenceOpenRecord,
    EvidenceOpenRecordError,
    EvidenceOpenRequest,
    bind_evidence_open_digest,
)
from translation_module.evidence_open_store import EvidenceOpenStore

if TYPE_CHECKING:
    from pathlib import Path


def _resolver() -> LedgerResolver:
    return LedgerResolver(
        resources={
            f"memory://evidence/{LOCATOR_A}": FRAME_A,
            f"memory://evidence/{LOCATOR_B}": b"corrupt-b",
        },
        calls=[],
    )


def _record_path(root: Path) -> Path:
    return next(root.glob("evidence-open.*.json"))


def test_open_frame_reads_only_the_named_canonical_evidence(tmp_path: Path) -> None:
    """Given A and corrupt B, opening A reads exactly A and persists proof."""
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source_fixture()), resolver)

    opened = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    replayed = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")

    assert resolver.calls == [LOCATOR_A]
    assert opened == replayed
    assert opened.schema_version == "evidence-open.v1"
    assert opened.declared_resource_digest == FRAME_A_DIGEST
    assert opened.observed_resource_digest == FRAME_A_DIGEST


def test_unopened_and_corrupt_frames_fail_without_false_proof(tmp_path: Path) -> None:
    """Citing unopened B does not read it; explicitly opening B reads then fails."""
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source_fixture()), resolver)
    _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")

    with pytest.raises(EvidenceOpenRecordError, match="not explicitly opened"):
        _ = store.records_for(
            ("frame-a", "frame-b"), EvidenceActorRole.REVIEWER, "review-1"
        )
    assert resolver.calls == [LOCATOR_A]

    with pytest.raises(EvidenceOpenError, match="digest mismatch"):
        _ = store.open_frame("frame-b", EvidenceActorRole.REVIEWER, "review-1")
    assert resolver.calls == [LOCATOR_A, LOCATOR_B]
    with pytest.raises(EvidenceOpenRecordError, match="not explicitly opened"):
        _ = store.records_for(("frame-b",), EvidenceActorRole.REVIEWER, "review-1")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor_role", EvidenceActorRole.TRANSLATOR),
        ("consumer_id", "review-2"),
        ("consumer_content_digest", "e" * 64),
        ("locator", "frames/moved.jpg"),
        ("source_content_digest", "a" * 64),
        ("declared_resource_digest", FRAME_B_DIGEST),
        ("record_digest", "a" * 64),
    ],
)
def test_stale_or_tampered_record_is_rejected(
    tmp_path: Path,
    field: str,
    value: str | EvidenceActorRole,
) -> None:
    """Persisted proof must retain exact source, role, consumer, and digest binding."""
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source_fixture()), resolver)
    record = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    changed = record.model_copy(update={field: value})
    if field != "record_digest":
        changed = bind_evidence_open_digest(changed)
    _ = _record_path(tmp_path).write_text(changed.model_dump_json(), encoding="utf-8")

    with pytest.raises((EvidenceOpenRecordError, ValidationError)):
        _ = store.records_for(("frame-a",), EvidenceActorRole.REVIEWER, "review-1")
    assert resolver.calls == [LOCATOR_A]


def test_declared_observed_mismatch_and_unknown_evidence_are_rejected(
    tmp_path: Path,
) -> None:
    """Malformed proof and evidence absent from source never trigger extra reads."""
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source_fixture()), resolver)
    record = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    payload = record.model_dump(mode="json")
    payload["observed_resource_digest"] = FRAME_B_DIGEST
    _ = _record_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceOpenRecordError, match="invalid"):
        _ = store.records_for(("frame-a",), EvidenceActorRole.REVIEWER, "review-1")
    with pytest.raises(EvidenceOpenError, match="not in source"):
        _ = store.open_frame("unknown", EvidenceActorRole.REVIEWER, "review-1")
    assert resolver.calls == [LOCATOR_A]


def test_stale_source_and_locator_are_rejected_without_rereading(
    tmp_path: Path,
) -> None:
    """A committed record cannot follow source content or locator mutation."""
    source = source_fixture()
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source), resolver)
    _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
    changed_entry = source.evidence.entries[0].model_copy(
        update={"locator": "frames/new-a.jpg"}
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

    with pytest.raises(EvidenceOpenRecordError, match="not explicitly opened"):
        _ = EvidenceOpenStore(
            tmp_path, evidence_context(changed_source), resolver
        ).records_for(("frame-a",), EvidenceActorRole.REVIEWER, "review-1")
    assert resolver.calls == [LOCATOR_A]


def test_bad_role_is_rejected_at_the_open_boundary() -> None:
    """An unknown actor role cannot become an evidence-open request."""
    with pytest.raises(ValidationError):
        _ = EvidenceOpenRequest.model_validate(
            {
                "evidence_id": "frame-a",
                "actor_role": "administrator",
                "consumer_id": "review-1",
            }
        )


def test_repeated_write_interruptions_leave_no_partial_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupted atomic staging leaves no record that can validate or replay."""
    resolver = _resolver()
    store = EvidenceOpenStore(tmp_path, evidence_context(source_fixture()), resolver)

    def interrupt(
        _target: BoundOutput, _name: str, _payload: bytes
    ) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(BoundOutput, "write_new", interrupt)
    for _ in range(2):
        with pytest.raises(KeyboardInterrupt):
            _ = store.open_frame("frame-a", EvidenceActorRole.REVIEWER, "review-1")
        assert tuple(tmp_path.glob("evidence-open.*.json")) == ()


def test_record_model_is_frozen() -> None:
    """Committed evidence-open data cannot be mutated in memory."""
    assert EvidenceOpenRecord.model_config.get("frozen") is True
