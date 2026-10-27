# Copyright 2026 Transcription Module contributors

"""Canonical v2 JSON contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from translation_module import contracts as public_contracts
from translation_module.canonical import (
    ContentContextAsset,
    SourceTranscriptV2,
    TranslationDocumentV2,
    TranslationGlossaryAsset,
    TranslationReviewArtifact,
)
from translation_module.contracts import SourceSnapshot, TranslationDocument

if TYPE_CHECKING:
    from collections.abc import Callable

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "docs" / "contracts" / "examples"
SCHEMAS = ROOT / "docs" / "contracts" / "json-schema"

ARTIFACTS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("source-transcript.v2", SourceTranscriptV2),
    ("translation-document.v2", TranslationDocumentV2),
    ("content-context.v1", ContentContextAsset),
    ("translation-glossary.v1", TranslationGlossaryAsset),
    ("translation-review.v1", TranslationReviewArtifact),
)

JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


def _json_object(text: str) -> dict[str, JsonValue]:
    return JSON_OBJECT_ADAPTER.validate_json(text)


def _object_list(
    payload: dict[str, object],
    key: str,
) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    for item in cast("list[object]", value):
        assert isinstance(item, dict)
    return cast("list[dict[str, object]]", value)


def _schema_const(model: type[BaseModel], field: str) -> JsonValue:
    schema = _json_object(json.dumps(model.model_json_schema()))
    properties = schema["properties"]
    assert isinstance(properties, dict)
    field_schema = properties[field]
    assert isinstance(field_schema, dict)
    return field_schema["const"]


def document_ref() -> dict[str, object]:
    """Build one source document reference payload."""
    return {
        "document_id": "source-document",
        "revision_id": "source-revision",
        "content_digest": DIGEST,
        "path": "../source/source.json",
    }


def asset_ref(asset_id: str) -> dict[str, object]:
    """Build one asset reference payload."""
    return {
        "asset_id": asset_id,
        "revision_id": f"{asset_id}-revision",
        "content_digest": DIGEST,
        "path": f"../assets/{asset_id}.json",
    }


def evidence() -> dict[str, object]:
    """Build one portable evidence manifest payload."""
    return {
        "base_uri": "./evidence/",
        "entries": [
            {
                "evidence_id": "frame-1",
                "kind": "frame",
                "media_type": "image/jpeg",
                "resource_digest": OTHER_DIGEST,
                "locator": "frame-1.jpg",
                "pts_ms": 500,
            }
        ],
    }


def source_payload() -> dict[str, object]:
    """Build a valid source-transcript.v2 payload."""
    return {
        "schema_version": "source-transcript.v2",
        "document_id": "source-document",
        "revision_id": "source-revision",
        "content_digest": DIGEST,
        "record_digest": OTHER_DIGEST,
        "lifecycle_status": "draft",
        "source_locale": "ko-KR",
        "duration_ms": 2_000,
        "input_context_ref": asset_ref("input-context"),
        "evidence": evidence(),
        "cues": [
            {
                "sequence": 1,
                "cue_id": "source:ass-dialogue:000001",
                "start_ms": 0,
                "end_ms": 1_000,
                "text": "토크 라운지에 오신",
                "speakers": ["한지아"],
                "content_kind": "dialogue",
                "evidence_ids": ["frame-1"],
            }
        ],
    }


def target_cue(
    *,
    source_cue_ids: list[str] | None = None,
    lineage_status: str = "confirmed",
) -> dict[str, object]:
    """Build one target cue with a selectable lineage state."""
    return {
        "sequence": 1,
        "cue_id": "target:000001",
        "source_cue_ids": ["source:ass-dialogue:000001"]
        if source_cue_ids is None
        else source_cue_ids,
        "lineage_status": lineage_status,
        "start_ms": 0,
        "end_ms": 1_000,
        "text": "Welcome to Talk Lounge,",
        "speakers": ["한지아"],
        "content_kind": "dialogue",
        "context_ids": [],
        "evidence_ids": [],
    }


def translation_payload() -> dict[str, object]:
    """Build a valid translation-document.v2 payload."""
    return {
        "schema_version": "translation-document.v2",
        "document_id": "translation-document",
        "revision_id": "translation-revision",
        "content_digest": DIGEST,
        "record_digest": OTHER_DIGEST,
        "source_locale": "ko-KR",
        "target_locale": "en-US",
        "source_ref": document_ref(),
        "prepared_context_ref": asset_ref("prepared-context"),
        "glossary_ref": asset_ref("glossary"),
        "lifecycle_status": "draft",
        "decision_status": "clear",
        "cues": [target_cue()],
        "unresolved_decisions": [],
    }


def prepared_context_payload() -> dict[str, object]:
    """Build a valid prepared content-context.v1 payload."""
    return {
        "schema_version": "content-context.v1",
        "asset_id": "prepared-context",
        "revision_id": "prepared-context-revision",
        "content_digest": DIGEST,
        "record_digest": OTHER_DIGEST,
        "stage": "prepared",
        "lifecycle_status": "draft",
        "prepared_from_source_ref": document_ref(),
        "program": "토크라운지",
        "episode_summary": "은하섬과 NOVA NOVA의 음악 토크쇼",
        "evidence": evidence(),
        "participants": [
            {
                "participant_id": "sera",
                "name_ko": "세라",
                "name_en": "Sera",
                "aliases": ["세라"],
                "role": "NOVA NOVA member",
                "evidence_ids": [],
            }
        ],
        "facts": [
            {
                "fact_id": "nickname-rina",
                "statement": "리나는 아리나의 실제 애칭이다.",
                "participant_ids": [],
                "scope_source_cue_ids": [],
                "evidence_ids": [],
                "decision_status": "human_confirmed",
                "blocking": False,
            }
        ],
    }


def glossary_payload() -> dict[str, object]:
    """Build a valid translation-glossary.v1 payload."""
    return {
        "schema_version": "translation-glossary.v1",
        "asset_id": "glossary",
        "revision_id": "glossary-revision",
        "content_digest": DIGEST,
        "record_digest": OTHER_DIGEST,
        "lifecycle_status": "draft",
        "prepared_from_source_ref": document_ref(),
        "evidence": evidence(),
        "entries": [
            {
                "entry_id": "sera-name",
                "source_variants": ["세라"],
                "preferred_target": "Sera",
                "type": "person_name",
                "instruction": "Use the official stage name.",
                "scope_source_cue_ids": [],
                "decision_status": "verified",
                "blocking": False,
                "case_sensitive": True,
                "evidence_ids": [],
                "note": "Official English spelling",
            }
        ],
    }


def review_payload() -> dict[str, object]:
    """Build a valid failed translation-review.v1 payload."""
    return {
        "schema_version": "translation-review.v1",
        "review_id": "review-1",
        "content_digest": DIGEST,
        "record_digest": OTHER_DIGEST,
        "binding": {
            "translation_document_id": "translation-document",
            "translation_revision_id": "translation-revision",
            "candidate_content_digest": DIGEST,
            "source_document_id": "source-document",
            "source_revision_id": "source-revision",
            "source_content_digest": DIGEST,
            "prepared_context_asset_id": "prepared-context",
            "prepared_context_revision_id": "prepared-context-revision",
            "prepared_context_content_digest": DIGEST,
            "glossary_asset_id": "glossary",
            "glossary_revision_id": "glossary-revision",
            "glossary_content_digest": DIGEST,
            "review_profile": {
                "profile_id": "en-us-editorial-review",
                "version": "2.0",
            },
        },
        "coverage": {
            "source_cue_ids": ["source:ass-dialogue:000001"],
            "target_cue_ids": ["target:000001"],
            "target_boundary_pairs": [],
            "glossary_entry_ids": ["sera-name"],
        },
        "result": "failed",
        "findings": [
            {
                "finding_id": "finding-1",
                "affected_source_cue_ids": ["source:ass-dialogue:000001"],
                "affected_target_cue_ids": ["target:000001"],
                "domain": "naturalness",
                "severity": "major",
                "blocking": True,
                "problem": "The line reads like a literal translation.",
                "required_change": "Rewrite it as natural en-US dialogue.",
                "resolution_status": "open",
            }
        ],
        "provenance": {
            "review_kind": "independent_ai",
            "reviewer_execution_ids": ["execution-1"],
            "model": "model-id",
            "completed_at": "2026-08-07T09:00:00Z",
        },
    }


@pytest.mark.parametrize(
    ("model", "payload_factory"),
    [
        (SourceTranscriptV2, source_payload),
        (TranslationDocumentV2, translation_payload),
        (ContentContextAsset, prepared_context_payload),
        (TranslationGlossaryAsset, glossary_payload),
        (TranslationReviewArtifact, review_payload),
    ],
)
def test_canonical_artifacts_round_trip_strict_json(
    model: type[BaseModel],
    payload_factory: Callable[[], dict[str, object]],
) -> None:
    """Every new artifact parses and round-trips without unknown data."""
    restored = model.model_validate(payload_factory())

    assert model.model_validate_json(restored.model_dump_json()) == restored


@pytest.mark.parametrize(
    ("model", "payload_factory"),
    [
        (SourceTranscriptV2, source_payload),
        (TranslationDocumentV2, translation_payload),
        (ContentContextAsset, prepared_context_payload),
        (TranslationGlossaryAsset, glossary_payload),
        (TranslationReviewArtifact, review_payload),
    ],
)
def test_canonical_artifacts_reject_unknown_fields(
    model: type[BaseModel],
    payload_factory: Callable[[], dict[str, object]],
) -> None:
    """Readers never silently discard fields from a newer contract."""
    payload = payload_factory()
    payload["future_field"] = "must fail"

    with pytest.raises(ValidationError, match="future_field"):
        _ = model.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload_factory", "wrong_version"),
    [
        (SourceTranscriptV2, source_payload, "source-transcript.v1"),
        (TranslationDocumentV2, translation_payload, "translation-document.v1"),
        (ContentContextAsset, prepared_context_payload, "content-context.v2"),
        (TranslationGlossaryAsset, glossary_payload, "translation-glossary.v2"),
        (TranslationReviewArtifact, review_payload, "translation-review.v2"),
    ],
)
def test_canonical_artifacts_reject_wrong_schema_version(
    model: type[BaseModel],
    payload_factory: Callable[[], dict[str, object]],
    wrong_version: str,
) -> None:
    """Version dispatch never reinterprets an artifact by shape."""
    payload = payload_factory()
    payload["schema_version"] = wrong_version

    with pytest.raises(ValidationError, match="schema_version"):
        _ = model.model_validate(payload)


def test_source_and_translation_reject_unsupported_locale_profiles() -> None:
    """The initial canonical workflow stays on the declared ko-KR to en-US profile."""
    source = source_payload()
    source["source_locale"] = "en-US"
    with pytest.raises(ValidationError, match="source_locale"):
        _ = SourceTranscriptV2.model_validate(source)

    translation = translation_payload()
    translation["target_locale"] = "ja-JP"
    with pytest.raises(ValidationError, match="target_locale"):
        _ = TranslationDocumentV2.model_validate(translation)


def test_nested_canonical_models_reject_unknown_fields() -> None:
    """Strictness applies to cue and reference objects, not only the root."""
    payload = translation_payload()
    cue = _object_list(payload, "cues")[0]
    cue["future_field"] = "must fail"

    with pytest.raises(ValidationError, match="future_field"):
        _ = TranslationDocumentV2.model_validate(payload)


def test_canonical_contract_rejects_explicit_null() -> None:
    """Optional canonical fields are omitted, never serialized as null."""
    payload = translation_payload()
    source_ref = payload["source_ref"]
    assert isinstance(source_ref, dict)
    source_ref["path"] = None

    with pytest.raises(ValidationError, match="null"):
        _ = TranslationDocumentV2.model_validate(payload)


@pytest.mark.parametrize(
    ("lineage_status", "source_ids", "valid"),
    [
        ("unassigned", [], True),
        ("unassigned", ["source-1"], False),
        ("needs_confirmation", [], False),
        ("needs_confirmation", ["source-1", "source-2"], True),
        ("confirmed", [], False),
        ("confirmed", ["source-1"], True),
    ],
)
def test_target_lineage_status_controls_source_ids(
    lineage_status: str,
    source_ids: list[str],
    *,
    valid: bool,
) -> None:
    """Draft targets can be unassigned or provisional without lying about lineage."""
    payload = translation_payload()
    payload["cues"] = [
        target_cue(source_cue_ids=source_ids, lineage_status=lineage_status)
    ]

    if valid:
        _ = TranslationDocumentV2.model_validate(payload)
    else:
        with pytest.raises(ValidationError, match="lineage"):
            _ = TranslationDocumentV2.model_validate(payload)


def test_context_stage_controls_prepared_source_reference() -> None:
    """Input context precedes source; prepared context depends on source."""
    prepared_without_ref = prepared_context_payload()
    _ = prepared_without_ref.pop("prepared_from_source_ref")
    with pytest.raises(ValidationError, match="prepared"):
        _ = ContentContextAsset.model_validate(prepared_without_ref)

    input_with_ref = prepared_context_payload()
    input_with_ref["stage"] = "input"
    with pytest.raises(ValidationError, match="input"):
        _ = ContentContextAsset.model_validate(input_with_ref)

    input_context = prepared_context_payload()
    input_context["stage"] = "input"
    _ = input_context.pop("prepared_from_source_ref")
    _ = ContentContextAsset.model_validate(input_context)


@pytest.mark.parametrize("decision_status", ["verified", "human_selected"])
def test_resolved_glossary_entry_requires_preferred_target(
    decision_status: str,
) -> None:
    """An enforceable glossary decision always names its target text."""
    payload = glossary_payload()
    entry = _object_list(payload, "entries")[0]
    _ = entry.pop("preferred_target")
    entry["decision_status"] = decision_status

    with pytest.raises(ValidationError, match="preferred_target"):
        _ = TranslationGlossaryAsset.model_validate(payload)


def test_unresolved_glossary_entry_may_omit_preferred_target() -> None:
    """A draft unresolved term can exist before a choice is available."""
    payload = glossary_payload()
    entry = _object_list(payload, "entries")[0]
    _ = entry.pop("preferred_target")
    entry["decision_status"] = "unresolved"

    _ = TranslationGlossaryAsset.model_validate(payload)


def test_translation_decision_status_matches_open_decisions() -> None:
    """Decision status cannot claim clear while an open choice remains."""
    payload = translation_payload()
    payload["unresolved_decisions"] = [
        {
            "decision_id": "decision-1",
            "source_cue_ids": ["source:ass-dialogue:000001"],
            "target_cue_ids": ["target:000001"],
            "domain": "culture",
            "question": "Keep the nickname Rina?",
            "options": [
                {
                    "option_id": "rina",
                    "text": "Rina",
                    "meaning": "The nickname used in the source",
                    "risk": "Differs from the official stage name",
                }
            ],
            "recommendation_id": "rina",
        }
    ]

    with pytest.raises(ValidationError, match="decision_status"):
        _ = TranslationDocumentV2.model_validate(payload)

    payload["decision_status"] = "needs_confirmation"
    _ = TranslationDocumentV2.model_validate(payload)


def _contains_null_schema(value: JsonValue) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "null":
            return True
        return any(_contains_null_schema(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_null_schema(item) for item in value)
    return False


@pytest.mark.parametrize(("schema_name", "model"), ARTIFACTS)
def test_committed_examples_and_schemas_match_models(
    schema_name: str,
    model: type[BaseModel],
) -> None:
    """Public examples parse and committed schemas are generated from Python."""
    example = _json_object(
        (EXAMPLES / f"{schema_name}.example.json").read_text(encoding="utf-8")
    )
    schema = _json_object(
        (SCHEMAS / f"{schema_name}.schema.json").read_text(encoding="utf-8")
    )

    _ = model.model_validate(example)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)
    assert schema == model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert not _contains_null_schema(schema)


def test_input_context_example_matches_the_same_context_contract() -> None:
    """The source dependency has an explicit stage=input context example."""
    example = _json_object(
        (EXAMPLES / "content-context.input.v1.example.json").read_text(
            encoding="utf-8"
        )
    )
    schema = ContentContextAsset.model_json_schema()

    _ = ContentContextAsset.model_validate(example)
    Draft202012Validator(schema).validate(example)
    assert example["stage"] == "input"
    assert "prepared_from_source_ref" not in example


def test_public_contract_facade_exports_canonical_models() -> None:
    """Consumers can import every canonical root model from the stable facade."""
    assert public_contracts.SourceTranscriptV2 is SourceTranscriptV2
    assert public_contracts.TranslationDocumentV2 is TranslationDocumentV2
    assert public_contracts.ContentContextAsset is ContentContextAsset
    assert public_contracts.TranslationGlossaryAsset is TranslationGlossaryAsset
    assert public_contracts.TranslationReviewArtifact is TranslationReviewArtifact


def test_legacy_v1_contracts_remain_v1_only() -> None:
    """Adding canonical v2 does not reinterpret existing v1 classes."""
    assert (
        _schema_const(SourceSnapshot, "schema_version") == "source-transcript.v1"
    )
    assert (
        _schema_const(TranslationDocument, "schema_version")
        == "translation-document.v1"
    )
