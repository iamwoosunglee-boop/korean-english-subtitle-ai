# Copyright 2026 Transcription Module contributors

"""RFC 8785 profile and canonical artifact digest tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import pytest
from pydantic import JsonValue, TypeAdapter

from translation_module.canonical import (
    CanonicalArtifact,
    ContentContextAsset,
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationGlossaryAsset,
    TranslationReviewArtifact,
    bind_canonical_digests,
    canonical_content_digest,
    canonical_json_bytes,
    canonical_record_digest,
    canonical_sha256,
    verify_canonical_digests,
)
from translation_module.canonical.digest import CanonicalizationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "docs" / "contracts" / "examples"
GOLDEN = ROOT / "docs" / "contracts" / "golden" / "canonical-digests.v1.json"
MAX_SAFE_INTEGER = (1 << 53) - 1

ARTIFACTS: tuple[tuple[str, type[CanonicalArtifact]], ...] = (
    ("source-transcript.v2", SourceTranscriptV2),
    ("translation-document.v2", TranslationDocumentV2),
    ("content-context.v1", ContentContextAsset),
    ("content-context.input.v1", ContentContextAsset),
    ("translation-glossary.v1", TranslationGlossaryAsset),
    ("translation-review.v1", TranslationReviewArtifact),
)


class _JcsVector(TypedDict):
    input: JsonValue
    canonical_json: str
    canonical_utf8_hex: str
    sha256: str


class _ArtifactVector(TypedDict):
    artifact: str
    content_projection: JsonValue
    record_projection: JsonValue
    content_digest: str
    record_digest: str
    content_canonical_json: str
    content_utf8_hex: str
    record_canonical_json: str
    record_utf8_hex: str


class _GoldenVectors(TypedDict):
    jcs_vectors: list[_JcsVector]
    artifact_vectors: list[_ArtifactVector]


def _load_artifact[ArtifactT: CanonicalArtifact](
    name: str,
    model: type[ArtifactT],
) -> ArtifactT:
    return model.model_validate_json(
        (EXAMPLES / f"{name}.example.json").read_text(encoding="utf-8")
    )


def _model_payload(model: BaseModel) -> dict[str, object]:
    return cast(
        "dict[str, object]",
        model.model_dump(mode="json", exclude_none=True),
    )


def _replace_path(payload: object, new_path: str) -> None:
    if isinstance(payload, dict):
        mapping = cast("dict[str, object]", payload)
        if "path" in mapping:
            mapping["path"] = new_path
        for value in mapping.values():
            _replace_path(value, new_path)
    elif isinstance(payload, list):
        for value in cast("list[object]", payload):
            _replace_path(value, new_path)


def test_jcs_uses_utf16_property_sorting_with_the_nfc_profile() -> None:
    """The RFC 8785 sort vector remains valid after replacing a non-NFC key."""
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "\ue000": "Private Use After Surrogate Pair",
        "1": "One",
        "😀": "Emoji: Grinning Face",
        "\x80": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    expected = (
        '{"\\r":"Carriage Return","1":"One","\x80":"Control",'
        '"ö":"Latin Small Letter O With Diaeresis","€":"Euro Sign",'
        '"😀":"Emoji: Grinning Face",'
        '"\ue000":"Private Use After Surrogate Pair"}'
    ).encode()

    assert canonical_json_bytes(value) == expected


def test_jcs_serializes_strings_and_nested_objects_without_whitespace() -> None:
    """Strings use ECMAScript-compatible escapes and arrays retain order."""
    value = {
        "z": [{"b": 2, "a": 1}, True],
        "string": "€$\x0f\nA'B\"\\\\\"/",
    }
    expected = (
        '{"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/",'
        '"z":[{"a":1,"b":2},true]}'
    ).encode()

    canonical = canonical_json_bytes(value)

    assert canonical == expected
    assert not canonical.startswith(b"\xef\xbb\xbf")
    assert canonical_sha256(value) == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize(
    "value",
    [
        None,
        1.0,
        float("nan"),
        float("inf"),
        MAX_SAFE_INTEGER + 1,
        -(MAX_SAFE_INTEGER + 1),
        {"e\u0301": "not NFC"},
        {"bad\ud800": "lone surrogate"},
        {1: "non-string key"},
    ],
)
def test_canonical_profile_rejects_values_outside_contract(value: object) -> None:
    """The cross-language profile rejects ambiguous or unsupported JSON values."""
    with pytest.raises(CanonicalizationError):
        _ = canonical_json_bytes(value)


def test_safe_integer_boundaries_are_canonical() -> None:
    """Integers exactly representable by I-JSON binary64 remain supported."""
    assert canonical_json_bytes(MAX_SAFE_INTEGER) == str(MAX_SAFE_INTEGER).encode()
    assert canonical_json_bytes(-MAX_SAFE_INTEGER) == str(-MAX_SAFE_INTEGER).encode()


@pytest.mark.parametrize(("name", "model"), ARTIFACTS)
def test_bind_and_verify_digests_for_every_public_example(
    name: str,
    model: type[CanonicalArtifact],
) -> None:
    """Every canonical artifact has a deterministic content and record digest."""
    artifact = _load_artifact(name, model)

    bound = bind_canonical_digests(artifact)

    verify_canonical_digests(bound)
    assert bound.content_digest == canonical_content_digest(bound)
    assert bound.record_digest == canonical_record_digest(bound)
    assert bound.content_digest != bound.record_digest


def test_translation_locator_and_lifecycle_do_not_change_content_digest() -> None:
    """Locator and human lifecycle metadata affect only record identity."""
    artifact = _load_artifact("translation-document.v2", TranslationDocumentV2)
    base = bind_canonical_digests(artifact)
    changed_payload = _model_payload(base)
    _replace_path(changed_payload, "../../relocated/reference.json")
    changed_payload["lifecycle_status"] = "finalized"
    changed_payload["finalization"] = {
        "finalized_at": "2026-08-07T10:00:00Z",
        "finalized_by": "human-editor",
        "review_acknowledgement": "not_run_acknowledged",
        "finding_dispositions": [],
    }
    changed_payload["content_digest"] = "0" * 64
    changed_payload["record_digest"] = "0" * 64
    changed = bind_canonical_digests(
        TranslationDocumentV2.model_validate(changed_payload)
    )

    assert changed.content_digest == base.content_digest
    assert changed.record_digest != base.record_digest


def test_translation_semantic_edits_change_content_digest() -> None:
    """Text, timing, speaker, lineage, and note all belong to candidate identity."""
    artifact = _load_artifact("translation-document.v2", TranslationDocumentV2)
    base = bind_canonical_digests(artifact)
    baseline_digest = base.content_digest

    mutations: tuple[Callable[[dict[str, object]], None], ...] = (
        lambda cue: cue.__setitem__("text", "Welcome back."),
        lambda cue: cue.__setitem__("end_ms", 999),
        lambda cue: cue.__setitem__("speakers", ["박도현"]),
        lambda cue: cue.__setitem__(
            "source_cue_ids",
            ["source:ass-dialogue:000002"],
        ),
        lambda cue: cue.__setitem__("translation_note", "Human wording choice."),
    )

    for mutate in mutations:
        payload = _model_payload(base)
        cues = cast("list[dict[str, object]]", payload["cues"])
        mutate(cues[0])
        payload["content_digest"] = "0" * 64
        payload["record_digest"] = "0" * 64
        changed = bind_canonical_digests(
            TranslationDocumentV2.model_validate(payload)
        )
        assert changed.content_digest != baseline_digest


def test_evidence_locator_and_resource_identity_use_different_digests() -> None:
    """Portable paths are hints; evidence bytes and timestamps are semantic."""
    artifact = _load_artifact("source-transcript.v2", SourceTranscriptV2)
    base = bind_canonical_digests(artifact)

    locator_payload = _model_payload(base)
    evidence = cast("dict[str, object]", locator_payload["evidence"])
    evidence["base_uri"] = "../../moved/evidence/"
    entries = cast("list[dict[str, object]]", evidence["entries"])
    entries[0]["locator"] = "frames/renamed.jpg"
    locator_payload["content_digest"] = "0" * 64
    locator_payload["record_digest"] = "0" * 64
    moved = bind_canonical_digests(SourceTranscriptV2.model_validate(locator_payload))

    assert moved.content_digest == base.content_digest
    assert moved.record_digest != base.record_digest

    semantic_payload = _model_payload(base)
    semantic_evidence = cast("dict[str, object]", semantic_payload["evidence"])
    semantic_entries = cast(
        "list[dict[str, object]]",
        semantic_evidence["entries"],
    )
    semantic_entries[0]["pts_ms"] = 501
    semantic_payload["content_digest"] = "0" * 64
    semantic_payload["record_digest"] = "0" * 64
    retimed = bind_canonical_digests(
        SourceTranscriptV2.model_validate(semantic_payload)
    )

    assert retimed.content_digest != base.content_digest

    resource_payload = _model_payload(base)
    resource_evidence = cast("dict[str, object]", resource_payload["evidence"])
    resource_entries = cast(
        "list[dict[str, object]]",
        resource_evidence["entries"],
    )
    resource_entries[0]["resource_digest"] = "c" * 64
    resource_payload["content_digest"] = "0" * 64
    resource_payload["record_digest"] = "0" * 64
    replaced = bind_canonical_digests(
        SourceTranscriptV2.model_validate(resource_payload)
    )

    assert replaced.content_digest != base.content_digest


def test_review_provenance_changes_only_record_digest() -> None:
    """Reviewer execution metadata is auditable but not review semantic identity."""
    artifact = _load_artifact(
        "translation-review.v1",
        TranslationReviewArtifact,
    )
    base = bind_canonical_digests(artifact)
    payload = _model_payload(base)
    provenance = cast("dict[str, object]", payload["provenance"])
    provenance["model"] = "different-model"
    provenance["completed_at"] = "2026-08-07T11:00:00Z"
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    changed = bind_canonical_digests(
        TranslationReviewArtifact.model_validate(payload)
    )

    assert changed.content_digest == base.content_digest
    assert changed.record_digest != base.record_digest


def test_review_finding_changes_review_content_digest() -> None:
    """Problem statements and required changes are part of review identity."""
    artifact = _load_artifact(
        "translation-review.v1",
        TranslationReviewArtifact,
    )
    base = bind_canonical_digests(artifact)
    payload = _model_payload(base)
    findings = cast("list[dict[str, object]]", payload["findings"])
    findings[0]["required_change"] = "Use the official stage name Sera."
    payload["content_digest"] = "0" * 64
    payload["record_digest"] = "0" * 64
    changed = bind_canonical_digests(
        TranslationReviewArtifact.model_validate(payload)
    )

    assert changed.content_digest != base.content_digest


def test_self_referential_digest_fields_are_excluded_from_their_projection() -> None:
    """Stored digest placeholders never feed back into the digest they define."""
    artifact = _load_artifact("source-transcript.v2", SourceTranscriptV2)
    base_content = canonical_content_digest(artifact)
    base_record = canonical_record_digest(artifact)
    changed_content_field = artifact.model_copy(update={"content_digest": "f" * 64})
    changed_record_field = artifact.model_copy(update={"record_digest": "f" * 64})

    assert canonical_content_digest(changed_content_field) == base_content
    assert canonical_content_digest(changed_record_field) == base_content
    assert canonical_record_digest(changed_record_field) == base_record
    assert canonical_record_digest(changed_content_field) != base_record


def test_committed_examples_form_one_digest_bound_dependency_graph() -> None:
    """Example refs point to the exact bound revision used by the next stage."""
    input_context = _load_artifact(
        "content-context.input.v1",
        ContentContextAsset,
    )
    source = _load_artifact("source-transcript.v2", SourceTranscriptV2)
    context = _load_artifact("content-context.v1", ContentContextAsset)
    glossary = _load_artifact(
        "translation-glossary.v1",
        TranslationGlossaryAsset,
    )
    translation = _load_artifact(
        "translation-document.v2",
        TranslationDocumentV2,
    )
    review = _load_artifact(
        "translation-review.v1",
        TranslationReviewArtifact,
    )

    for artifact in (input_context, source, context, glossary, translation, review):
        verify_canonical_digests(artifact)
    assert source.input_context_ref.content_digest == input_context.content_digest
    assert context.prepared_from_source_ref is not None
    assert context.prepared_from_source_ref.content_digest == source.content_digest
    assert glossary.prepared_from_source_ref.content_digest == source.content_digest
    assert translation.source_ref.content_digest == source.content_digest
    assert translation.prepared_context_ref.content_digest == context.content_digest
    assert translation.glossary_ref.content_digest == glossary.content_digest
    assert review.binding.candidate_content_digest == translation.content_digest
    assert review.binding.source_content_digest == source.content_digest
    assert review.binding.prepared_context_content_digest == context.content_digest
    assert review.binding.glossary_content_digest == glossary.content_digest


def test_committed_golden_vectors_match_reference_implementation() -> None:
    """C# consumers receive exact canonical UTF-8 bytes and SHA-256 vectors."""
    vectors = TypeAdapter(_GoldenVectors).validate_json(
        GOLDEN.read_text(encoding="utf-8")
    )

    for vector in vectors["jcs_vectors"]:
        canonical = canonical_json_bytes(vector["input"])
        assert canonical.decode() == vector["canonical_json"]
        assert canonical.hex() == vector["canonical_utf8_hex"]
        assert hashlib.sha256(canonical).hexdigest() == vector["sha256"]

    for vector in vectors["artifact_vectors"]:
        model = dict(ARTIFACTS)[vector["artifact"]]
        artifact = _load_artifact(vector["artifact"], model)
        bound = bind_canonical_digests(artifact)
        assert bound.content_digest == vector["content_digest"]
        assert bound.record_digest == vector["record_digest"]
        content_bytes = canonical_json_bytes(vector["content_projection"])
        record_bytes = canonical_json_bytes(vector["record_projection"])
        assert content_bytes.decode() == vector["content_canonical_json"]
        assert content_bytes.hex() == vector["content_utf8_hex"]
        assert record_bytes.decode() == vector["record_canonical_json"]
        assert record_bytes.hex() == vector["record_utf8_hex"]
