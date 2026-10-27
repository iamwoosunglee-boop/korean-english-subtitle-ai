# Copyright 2026 Transcription Module contributors

"""Supporting-only web lookup provenance for uncertain external facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

import pytest
from pydantic import HttpUrl

from transcription_module.drafting import UnresolvedReasonCode
from transcription_module.web_lookup import (
    ExternalFactKind,
    WebLookupArtifact,
    WebLookupHit,
    WebLookupRelation,
    WebLookupRequest,
    WebLookupStatus,
    append_web_lookup_record,
    lookup_external_fact,
    unresolved_web_conflict,
)

if TYPE_CHECKING:
    from pathlib import Path


@final
class FakeLookupProvider:
    """Deterministic provider used only at the injected lookup boundary."""

    def __init__(self, hits: tuple[WebLookupHit, ...]) -> None:
        """Capture deterministic hits and every submitted uncertainty request."""
        self.hits = hits
        self.requests: list[WebLookupRequest] = []

    def search(self, request: WebLookupRequest) -> tuple[WebLookupHit, ...]:
        """Record the request and return the configured typed hits."""
        self.requests.append(request)
        return self.hits


def _request() -> WebLookupRequest:
    return WebLookupRequest(
        request_id="web-lookup-1",
        query="한지아 배우자 이름",
        kind=ExternalFactKind.PROPER_NAME,
        at_ms=172_000,
        target_cue_index=12,
        current_evidence_text="배우자 이름이 불명확함",
        uncertainty_reason="영상 근거만으로 고유명사를 확정할 수 없음",
        source_evidence_ids=("asr:172000", "frame:172000"),
    )


def test_uncertain_fact_lookup_records_typed_supporting_provenance() -> None:
    """A typed uncertainty permits one supporting-only provider lookup."""
    hit = WebLookupHit(
        url=HttpUrl("https://example.com/profile"),
        excerpt="공식 프로필에 배우자 이름이 기재되어 있다.",
        relation=WebLookupRelation.SUPPORTS,
    )
    provider = FakeLookupProvider((hit,))

    record = lookup_external_fact(
        _request(),
        provider,
        looked_up_at="2026-08-24T12:00:00Z",
    )

    assert provider.requests == [_request()]
    assert record.status is WebLookupStatus.SUPPORTING_ONLY
    assert record.hits == (hit,)
    assert unresolved_web_conflict(record) is None


def test_conflicting_web_result_routes_to_user_confirmation() -> None:
    """A web conflict remains unresolved and never replaces video evidence."""
    hit = WebLookupHit(
        url=HttpUrl("https://example.com/conflict"),
        excerpt="다른 이름을 배우자로 소개한다.",
        relation=WebLookupRelation.CONFLICTS,
    )
    record = lookup_external_fact(
        _request(),
        FakeLookupProvider((hit,)),
        looked_up_at="2026-08-24T12:00:00Z",
    )

    unresolved = unresolved_web_conflict(record)

    assert record.status is WebLookupStatus.NEEDS_CONFIRMATION
    assert unresolved is not None
    assert unresolved.reason_code is UnresolvedReasonCode.EXTERNAL_FACT_CONFLICT
    assert unresolved.candidates == ()
    assert unresolved.source_evidence_ids == (
        "asr:172000",
        "frame:172000",
        "web:web-lookup-1",
    )


def test_web_lookup_sidecar_is_append_only_and_rejects_identity_rewrite(
    tmp_path: Path,
) -> None:
    """Persisted query, URL, and excerpt cannot be overwritten by the same ID."""
    path = tmp_path / "web-lookups.json"
    first = lookup_external_fact(
        _request(),
        FakeLookupProvider(
            (
                WebLookupHit(
                    url=HttpUrl("https://example.com/profile"),
                    excerpt="공식 프로필 발췌",
                    relation=WebLookupRelation.SUPPORTS,
                ),
            )
        ),
        looked_up_at="2026-08-24T12:00:00Z",
    )
    _ = append_web_lookup_record(path, "run-web", first)
    before = path.read_bytes()
    changed = first.model_copy(
        update={
            "hits": (
                first.hits[0].model_copy(update={"excerpt": "바뀐 발췌"}),
            )
        }
    )

    with pytest.raises(ValueError, match="request ID"):
        _ = append_web_lookup_record(path, "run-web", changed)

    assert path.read_bytes() == before
    assert WebLookupArtifact.model_validate_json(before).records == (first,)
