# Copyright 2026 Transcription Module contributors

"""Shared strict values for canonical subtitle artifacts."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Self, cast, override

from pydantic import Field, GetJsonSchemaHandler, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from translation_module.common import ContentKind, FrozenModel, Sha256Digest

CanonicalId = Annotated[str, Field(min_length=1, strict=True)]
CanonicalMilliseconds = Annotated[int, Field(ge=0, strict=True)]
PositiveCanonicalMilliseconds = Annotated[int, Field(gt=0, strict=True)]
UtcTimestamp = Annotated[
    str,
    Field(
        pattern=(
            r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
            r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
        ),
        strict=True,
    ),
]


def _is_none(value: object) -> bool:
    return value is None


def _reject_explicit_null(value: object, path: str = "$") -> None:
    if value is None:
        message = f"canonical JSON must omit optional fields instead of null: {path}"
        raise ValueError(message)
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        for key, item in mapping.items():
            _reject_explicit_null(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        sequence = cast("list[object] | tuple[object, ...]", value)
        for index, item in enumerate(sequence):
            _reject_explicit_null(item, f"{path}[{index}]")


def _schema_without_null(value: object) -> object:
    if isinstance(value, list):
        sequence = cast("list[object]", value)
        return [_schema_without_null(item) for item in sequence]
    if not isinstance(value, dict):
        return value

    mapping = cast("dict[str, object]", value)
    result: dict[str, object] = {
        key: _schema_without_null(item)
        for key, item in mapping.items()
        if key not in {"default", "description", "title"}
    }
    schema_type = result.get("type")
    if isinstance(schema_type, list):
        schema_types = cast("list[object]", schema_type)
        without_null: list[object] = [
            item for item in schema_types if item != "null"
        ]
        result["type"] = (
            without_null[0] if len(without_null) == 1 else without_null
        )

    for union_key in ("anyOf", "oneOf"):
        options_value = result.get(union_key)
        if not isinstance(options_value, list):
            continue
        options = cast("list[object]", options_value)
        without_null_options: list[object] = [
            option
            for option in options
            if not (
                isinstance(option, dict)
                and cast("dict[str, object]", option).get("type") == "null"
            )
        ]
        if len(without_null_options) == 1 and isinstance(
            without_null_options[0], dict
        ):
            _ = result.pop(union_key)
            inner = cast("dict[str, object]", without_null_options[0])
            for key, item in inner.items():
                _ = result.setdefault(key, item)
        else:
            result[union_key] = without_null_options
    return result


class CanonicalModel(FrozenModel):
    """Strict canonical boundary that forbids explicit JSON nulls."""

    @model_validator(mode="before")
    @classmethod
    def explicit_null_is_not_canonical(cls, value: object) -> object:
        """Reject explicit null while allowing an omitted optional field."""
        _reject_explicit_null(value)
        return value

    @classmethod
    @override
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """Publish optional omitted fields without nullable schema branches."""
        schema = handler(core_schema)
        return cast("JsonSchemaValue", _schema_without_null(schema))


class DocumentLifecycleStatus(StrEnum):
    """Human-owned lifecycle for source and translation revisions."""

    DRAFT = "draft"
    FINALIZED = "finalized"


class AssetLifecycleStatus(StrEnum):
    """Preparation lifecycle for context and glossary revisions."""

    DRAFT = "draft"
    READY = "ready"


class DocumentReference(CanonicalModel):
    """Identity plus optional relative locator for one document revision."""

    document_id: CanonicalId
    revision_id: CanonicalId
    content_digest: Sha256Digest
    path: str | None = Field(default=None, min_length=1, exclude_if=_is_none)


class AssetReference(CanonicalModel):
    """Identity plus optional relative locator for one asset revision."""

    asset_id: CanonicalId
    revision_id: CanonicalId
    content_digest: Sha256Digest
    path: str | None = Field(default=None, min_length=1, exclude_if=_is_none)


class CanonicalEvidenceKind(StrEnum):
    """External evidence resource category."""

    FRAME = "frame"
    IMAGE = "image"
    TEXT = "text"


class CanonicalEvidenceEntry(CanonicalModel):
    """One evidence resource with content identity and a locator hint."""

    evidence_id: CanonicalId
    kind: CanonicalEvidenceKind
    media_type: str = Field(min_length=1)
    resource_digest: Sha256Digest
    locator: str = Field(min_length=1)
    pts_ms: CanonicalMilliseconds | None = Field(default=None, exclude_if=_is_none)

    @model_validator(mode="after")
    def frame_has_pts(self) -> Self:
        """Require a presentation timestamp for frame evidence."""
        if self.kind is CanonicalEvidenceKind.FRAME and self.pts_ms is None:
            message = "frame evidence requires pts_ms"
            raise ValueError(message)
        return self


class CanonicalEvidenceManifest(CanonicalModel):
    """Portable evidence locator table."""

    base_uri: str = Field(min_length=1)
    entries: tuple[CanonicalEvidenceEntry, ...]

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> Self:
        """Reject ambiguous evidence identity within one manifest."""
        evidence_ids = tuple(entry.evidence_id for entry in self.entries)
        if len(evidence_ids) != len(set(evidence_ids)):
            message = "evidence IDs must be unique"
            raise ValueError(message)
        return self


def validate_contiguous_sequences(sequences: tuple[int, ...], kind: str) -> None:
    """Require one-based contiguous display sequence values."""
    if sequences != tuple(range(1, len(sequences) + 1)):
        message = f"{kind} sequences must be contiguous"
        raise ValueError(message)


def validate_unique_ids(values: tuple[str, ...], kind: str) -> None:
    """Reject repeated stable identifiers."""
    if len(values) != len(set(values)):
        message = f"{kind} IDs must be unique"
        raise ValueError(message)


def validate_evidence_references(
    evidence_ids: tuple[str, ...],
    manifest: CanonicalEvidenceManifest,
    kind: str,
) -> None:
    """Require every evidence reference to resolve in its owning manifest."""
    known = {entry.evidence_id for entry in manifest.entries}
    unknown = set(evidence_ids) - known
    if unknown:
        message = f"{kind} references unknown evidence: {unknown}"
        raise ValueError(message)


__all__ = [
    "AssetLifecycleStatus",
    "AssetReference",
    "CanonicalEvidenceEntry",
    "CanonicalEvidenceKind",
    "CanonicalEvidenceManifest",
    "CanonicalId",
    "CanonicalMilliseconds",
    "CanonicalModel",
    "ContentKind",
    "DocumentLifecycleStatus",
    "DocumentReference",
    "PositiveCanonicalMilliseconds",
    "UtcTimestamp",
    "validate_contiguous_sequences",
    "validate_evidence_references",
    "validate_unique_ids",
]
