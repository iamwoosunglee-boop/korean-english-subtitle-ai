# Copyright 2026 Transcription Module contributors

"""Shared immutable values for translation JSON boundaries."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, NewType, cast

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler, model_validator
from pydantic_core import core_schema


class _CueId(str):
    """Runtime-distinct cue ID accepted as a JSON string only."""

    __slots__: tuple[str, ...] = ()

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(
                cls,
                core_schema.str_schema(min_length=1),
            ),
            python_schema=core_schema.is_instance_schema(cls),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str,
                when_used="json",
            ),
        )


class SourceCueId(_CueId):
    """Runtime-distinct source cue ID."""


class TargetCueId(_CueId):
    """Runtime-distinct target cue ID."""


EvidenceId = Annotated[NewType("EvidenceId", str), Field(min_length=1, strict=True)]
ContextId = Annotated[NewType("ContextId", str), Field(min_length=1, strict=True)]
Milliseconds = Annotated[NewType("Milliseconds", int), Field(strict=True)]

NonNegativeMilliseconds = Annotated[Milliseconds, Field(ge=0)]
PositiveMilliseconds = Annotated[Milliseconds, Field(gt=0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
LocaleCode = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")]
SourceLocale = Literal["ko-KR"]
TargetLocale = Literal["en-US"]


class FrozenModel(BaseModel):
    """Immutable, strict JSON boundary model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False
    )

    @model_validator(mode="before")
    @classmethod
    def public_values_are_canonical(cls, value: object) -> object:
        """Reject values that cannot have one public JSON representation."""
        _validate_public_value(value)
        return value


def _validate_public_value(value: object, key: str | None = None) -> None:
    if isinstance(value, str):
        if not unicodedata.is_normalized("NFC", value):
            message = "public strings must be NFC normalized"
            raise ValueError(message)
        if not value.strip():
            message = f"{key or 'public string'} must be nonblank"
            raise ValueError(message)
    elif isinstance(value, float):
        if not math.isfinite(value):
            message = "public numbers must be finite"
            raise ValueError(message)
    elif isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        for item_key, item_value in mapping.items():
            _validate_public_value(item_value, str(item_key))
    elif isinstance(value, (list, tuple)):
        values = cast("list[object] | tuple[object, ...]", value)
        _validate_unique_public_list(values, key)
        for item in values:
            _validate_public_value(item)


def _validate_unique_public_list(
    values: list[object] | tuple[object, ...],
    key: str | None,
) -> None:
    unique_list_keys = {
        "evidence_ids",
        "context_ids",
        "context_evidence_ids",
        "source_cue_ids",
        "target_cue_ids",
    }
    if key in unique_list_keys and len(values) != len(set(values)):
        message = f"{key} values must be unique"
        raise ValueError(message)


def canonical_json_bytes(
    model: BaseModel,
    *,
    exclude: set[str] | None = None,
) -> bytes:
    """Serialize a public model to stable canonical UTF-8 JSON bytes."""
    return json.dumps(
        model.model_dump(mode="json", exclude=exclude),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(model: BaseModel, *, exclude: set[str] | None = None) -> str:
    """Hash canonical public JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(model, exclude=exclude)).hexdigest()


class ContentKind(StrEnum):
    """Source and target content policy category."""

    DIALOGUE = "dialogue"
    LYRICS = "lyrics"
    RECORDED_VOCAL = "recorded_vocal"
    UNKNOWN = "unknown"


class Participant(FrozenModel):
    """Person or group named in episode context."""

    participant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    english_name: str | None = None


class EpisodeContext(FrozenModel):
    """Episode-wide facts retained in translation output."""

    summary: str = Field(min_length=1)
    program: str | None = None
    participants: tuple[Participant, ...] = ()
