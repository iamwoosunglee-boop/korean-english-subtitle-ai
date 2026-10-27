# Copyright 2026 Transcription Module contributors

"""Strict translation-document JSON boundary tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from tests.translation.type_builders import milliseconds
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    SourceCueId,
    TargetCueId,
)
from translation_module.document import DocumentStatus, TargetCue, TranslationDocument

if TYPE_CHECKING:
    from pydantic import JsonValue


def _set_json_value(
    payload: dict[str, JsonValue],
    path: tuple[str | int, ...],
    value: JsonValue,
) -> None:
    target: JsonValue = payload
    for part in path[:-1]:
        if isinstance(target, dict) and isinstance(part, str):
            target = target[part]
            continue
        if isinstance(target, list) and isinstance(part, int):
            target = target[part]
            continue
        raise TypeError(path)
    key = path[-1]
    if isinstance(target, dict) and isinstance(key, str):
        target[key] = value
        return
    if isinstance(target, list) and isinstance(key, int):
        target[key] = value
        return
    raise TypeError(path)


def _document() -> TranslationDocument:
    return TranslationDocument(
        document_id="document-1",
        content_digest="0" * 64,
        source_document_id="source-1",
        source_digest="1" * 64,
        source_locale="ko-KR",
        target_locale="en-US",
        context=EpisodeContext(summary="인사"),
        retained_contexts=(),
        cues=(
            TargetCue(
                sequence=1,
                cue_id=TargetCueId("target-1"),
                source_cue_ids=(SourceCueId("source-1"),),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="Hello.",
                speakers=("speaker-1",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
        status=DocumentStatus.FINALIZED,
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("cues", 0, "start_ms"), False),
        (("cues", 0, "end_ms"), 1_000.0),
        (("status",), "finalized"),
        (("cues", 0, "cue_id"), ""),
    ],
)
def test_strict_document_payload_rejects_coercion_and_blank_ids(
    path: tuple[str | int, ...], value: JsonValue
) -> None:
    """Reject Python coercion and blank target cue IDs."""
    payload = cast(
        "dict[str, JsonValue]",
        _document().model_dump(mode="json"),
    )
    _set_json_value(payload, path, value)

    with pytest.raises(ValidationError):
        _ = TranslationDocument.model_validate(payload)


def test_branded_target_cue_id_rejects_python_strings_and_parses_json() -> None:
    """Target cue IDs retain their runtime brand at the public boundary."""
    payload = _document().model_dump(mode="json")
    payload["status"] = DocumentStatus.FINALIZED
    payload["cues"][0]["content_kind"] = ContentKind.DIALOGUE
    payload["cues"][0]["source_cue_ids"] = (SourceCueId("source-1"),)
    with pytest.raises(ValidationError):
        _ = TranslationDocument.model_validate(payload)

    restored = TranslationDocument.model_validate_json(json.dumps(payload))

    assert type(restored.cues[0].cue_id).__name__ == "TargetCueId"


def test_strict_document_json_rejects_unknown_keys_and_non_nfc_text() -> None:
    """Reject invalid canonical document JSON values and keys."""
    payload = _document().model_dump(mode="json") | {"untrusted": "control value"}
    with pytest.raises(ValidationError):
        _ = TranslationDocument.model_validate_json(json.dumps(payload))

    payload = _document().model_dump(mode="json")
    payload["cues"][0]["text"] = "e\u0301"
    with pytest.raises(ValidationError):
        _ = TranslationDocument.model_validate_json(json.dumps(payload))
