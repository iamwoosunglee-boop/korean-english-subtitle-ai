# Copyright 2026 Transcription Module contributors

"""RFC 8785 profile and digest projections for canonical subtitle artifacts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel

from translation_module.canonical.context import ContentContextAsset
from translation_module.canonical.glossary import TranslationGlossaryAsset
from translation_module.canonical.review import TranslationReviewArtifact
from translation_module.canonical.source import SourceTranscriptV2
from translation_module.canonical.translation import TranslationDocumentV2

MAX_SAFE_INTEGER = (1 << 53) - 1
SURROGATE_MIN = 0xD800
SURROGATE_MAX = 0xDFFF

type CanonicalArtifact = (
    SourceTranscriptV2
    | TranslationDocumentV2
    | ContentContextAsset
    | TranslationGlossaryAsset
    | TranslationReviewArtifact
)


class CanonicalizationError(ValueError):
    """Raised when a value is outside the canonical JSON profile."""


class CanonicalDigestMismatchError(ValueError):
    """Raised when stored canonical digests do not match current content."""


def _validate_string(value: str) -> None:
    if not unicodedata.is_normalized("NFC", value):
        message = "canonical JSON strings must be NFC normalized"
        raise CanonicalizationError(message)
    if any(
        SURROGATE_MIN <= ord(character) <= SURROGATE_MAX
        for character in value
    ):
        message = "canonical JSON strings cannot contain lone surrogates"
        raise CanonicalizationError(message)


def _serialize_string(value: str) -> str:
    _validate_string(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utf16_sort_key(value: str) -> bytes:
    _validate_string(value)
    return value.encode("utf-16-be")


def _serialize(value: object) -> str:
    if value is None:
        message = "canonical JSON profile forbids null"
        raise CanonicalizationError(message)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            message = "canonical integers must be I-JSON safe integers"
            raise CanonicalizationError(message)
        return str(value)
    if isinstance(value, float):
        message = "canonical JSON profile forbids floating-point numbers"
        raise CanonicalizationError(message)
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        if any(not isinstance(key, str) for key in mapping):
            message = "canonical JSON object keys must be strings"
            raise CanonicalizationError(message)
        string_mapping = cast("Mapping[str, object]", mapping)
        keys = sorted(string_mapping, key=_utf16_sort_key)
        return "{" + ",".join(
            f"{_serialize_string(key)}:{_serialize(string_mapping[key])}"
            for key in keys
        ) + "}"
    if isinstance(value, (list, tuple)):
        sequence = cast("list[object] | tuple[object, ...]", value)
        return "[" + ",".join(_serialize(item) for item in sequence) + "]"
    message = f"unsupported canonical JSON value: {type(value).__name__}"
    raise CanonicalizationError(message)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one value using RFC 8785 plus the integer-only NFC profile."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    return _serialize(value).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Hash the canonical UTF-8 bytes for one profile value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _semantic_reference(reference: BaseModel) -> dict[str, object]:
    return cast(
        "dict[str, object]",
        reference.model_dump(mode="json", exclude={"path"}, exclude_none=True),
    )


type EvidenceArtifact = (
    SourceTranscriptV2 | ContentContextAsset | TranslationGlossaryAsset
)


def _semantic_evidence(artifact: EvidenceArtifact) -> dict[str, object]:
    entries = cast("tuple[BaseModel, ...]", artifact.evidence.entries)
    return {
        "entries": [
            entry.model_dump(
                mode="json",
                exclude={"locator"},
                exclude_none=True,
            )
            for entry in entries
        ]
    }


def _source_content_projection(artifact: SourceTranscriptV2) -> dict[str, object]:
    return {
        "schema_version": artifact.schema_version,
        "source_locale": artifact.source_locale,
        "duration_ms": artifact.duration_ms,
        "input_context_ref": _semantic_reference(artifact.input_context_ref),
        "evidence": _semantic_evidence(artifact),
        "cues": [
            cue.model_dump(mode="json", exclude_none=True) for cue in artifact.cues
        ],
    }


def _translation_content_projection(
    artifact: TranslationDocumentV2,
) -> dict[str, object]:
    return {
        "schema_version": artifact.schema_version,
        "source_locale": artifact.source_locale,
        "target_locale": artifact.target_locale,
        "source_ref": _semantic_reference(artifact.source_ref),
        "prepared_context_ref": _semantic_reference(
            artifact.prepared_context_ref
        ),
        "glossary_ref": _semantic_reference(artifact.glossary_ref),
        "decision_status": artifact.decision_status.value,
        "cues": [
            cue.model_dump(mode="json", exclude_none=True) for cue in artifact.cues
        ],
        "unresolved_decisions": [
            decision.model_dump(mode="json", exclude_none=True)
            for decision in artifact.unresolved_decisions
        ],
    }


def _context_content_projection(
    artifact: ContentContextAsset,
) -> dict[str, object]:
    projection: dict[str, object] = {
        "schema_version": artifact.schema_version,
        "stage": artifact.stage.value,
        "program": artifact.program,
        "episode_summary": artifact.episode_summary,
        "evidence": _semantic_evidence(artifact),
        "participants": [
            participant.model_dump(mode="json", exclude_none=True)
            for participant in artifact.participants
        ],
        "facts": [
            fact.model_dump(mode="json", exclude_none=True)
            for fact in artifact.facts
        ],
    }
    if artifact.prepared_from_source_ref is not None:
        projection["prepared_from_source_ref"] = _semantic_reference(
            artifact.prepared_from_source_ref
        )
    return projection


def _glossary_content_projection(
    artifact: TranslationGlossaryAsset,
) -> dict[str, object]:
    return {
        "schema_version": artifact.schema_version,
        "prepared_from_source_ref": _semantic_reference(
            artifact.prepared_from_source_ref
        ),
        "evidence": _semantic_evidence(artifact),
        "entries": [
            entry.model_dump(mode="json", exclude_none=True)
            for entry in artifact.entries
        ],
    }


def _review_content_projection(
    artifact: TranslationReviewArtifact,
) -> dict[str, object]:
    return {
        "schema_version": artifact.schema_version,
        "binding": artifact.binding.model_dump(mode="json", exclude_none=True),
        "coverage": artifact.coverage.model_dump(mode="json", exclude_none=True),
        "result": artifact.result.value,
        "findings": [
            finding.model_dump(mode="json", exclude_none=True)
            for finding in artifact.findings
        ],
    }


def canonical_content_projection(
    artifact: CanonicalArtifact,
) -> dict[str, object]:
    """Return the semantic projection used for one artifact content digest."""
    if isinstance(artifact, SourceTranscriptV2):
        return _source_content_projection(artifact)
    if isinstance(artifact, TranslationDocumentV2):
        return _translation_content_projection(artifact)
    if isinstance(artifact, ContentContextAsset):
        return _context_content_projection(artifact)
    if isinstance(artifact, TranslationGlossaryAsset):
        return _glossary_content_projection(artifact)
    return _review_content_projection(artifact)


def canonical_record_projection(
    artifact: CanonicalArtifact,
) -> dict[str, object]:
    """Return the complete stored record excluding its self digest."""
    return cast(
        "dict[str, object]",
        artifact.model_dump(
            mode="json",
            exclude={"record_digest"},
            exclude_none=True,
        ),
    )


def canonical_content_digest(artifact: CanonicalArtifact) -> str:
    """Hash semantic content while excluding locator and lifecycle metadata."""
    return canonical_sha256(canonical_content_projection(artifact))


def canonical_record_digest(artifact: CanonicalArtifact) -> str:
    """Hash the complete stored record except the record digest itself."""
    return canonical_sha256(canonical_record_projection(artifact))


def bind_canonical_digests[ArtifactT: CanonicalArtifact](
    artifact: ArtifactT,
) -> ArtifactT:
    """Return one artifact with current content and record digests bound."""
    with_content = artifact.model_copy(
        update={"content_digest": canonical_content_digest(artifact)}
    )
    bound = with_content.model_copy(
        update={"record_digest": canonical_record_digest(with_content)}
    )
    return cast("ArtifactT", bound)


def verify_canonical_digests(artifact: CanonicalArtifact) -> None:
    """Raise when either stored digest does not match the current artifact."""
    expected_content = canonical_content_digest(artifact)
    if artifact.content_digest != expected_content:
        message = (
            "content digest mismatch: "
            f"stored={artifact.content_digest} expected={expected_content}"
        )
        raise CanonicalDigestMismatchError(message)
    expected_record = canonical_record_digest(artifact)
    if artifact.record_digest != expected_record:
        message = (
            "record digest mismatch: "
            f"stored={artifact.record_digest} expected={expected_record}"
        )
        raise CanonicalDigestMismatchError(message)


__all__ = [
    "MAX_SAFE_INTEGER",
    "CanonicalArtifact",
    "CanonicalDigestMismatchError",
    "CanonicalizationError",
    "bind_canonical_digests",
    "canonical_content_digest",
    "canonical_content_projection",
    "canonical_json_bytes",
    "canonical_record_digest",
    "canonical_record_projection",
    "canonical_sha256",
    "verify_canonical_digests",
]
