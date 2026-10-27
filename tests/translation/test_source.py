# Copyright 2026 Transcription Module contributors

"""Strict source JSON boundary tests."""

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
)
from translation_module.preparation import bind_source_digest, source_digest
from translation_module.source import (
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)

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


def _source() -> SourceSnapshot:
    return SourceSnapshot(
        document_id="source-1",
        content_digest="0" * 64,
        maturity=SourceMaturity.FINALIZED,
        source_locale="ko-KR",
        duration_ms=milliseconds(1_000),
        context=EpisodeContext(summary="인사"),
        evidence=EvidenceManifest(base_uri="memory://source/"),
        cues=(
            SourceCue(
                sequence=1,
                cue_id=SourceCueId("source-1"),
                start_ms=milliseconds(0),
                end_ms=milliseconds(1_000),
                text="안녕하세요",
                speakers=("speaker-1",),
                content_kind=ContentKind.DIALOGUE,
            ),
        ),
    )


def test_source_cue_context_note_is_optional_and_digest_bound() -> None:
    """Cue context stays omitted for legacy sources and bound when present."""
    legacy = _source()

    assert "context_note" not in legacy.cues[0].model_dump(mode="json")
    assert source_digest(legacy) == (
        "941c1832a90336f5eeea542d0096b9739802839c5ac2e42faf6df9b923322bb3"
    )

    noted = bind_source_digest(
        legacy.model_copy(
            update={
                "cues": (
                    legacy.cues[0].model_copy(
                        update={"context_note": "화면 밖에서 웃음"}
                    ),
                )
            }
        )
    )
    restored = SourceSnapshot.model_validate_json(noted.model_dump_json())

    assert restored.cues[0].context_note == "화면 밖에서 웃음"
    assert restored.content_digest == source_digest(restored)
    assert restored.content_digest != source_digest(legacy)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("duration_ms",), True),
        (("duration_ms",), 1_000.0),
        (("cues", 0, "sequence"), 1.0),
        (("maturity",), "finalized"),
        (("cues", 0, "cue_id"), ""),
    ],
)
def test_strict_source_payload_rejects_coercion_and_blank_ids(
    path: tuple[str | int, ...], value: JsonValue
) -> None:
    """Reject Python coercion and blank source cue IDs."""
    payload = cast(
        "dict[str, JsonValue]",
        _source().model_dump(mode="json"),
    )
    _set_json_value(payload, path, value)

    with pytest.raises(ValidationError):
        _ = SourceSnapshot.model_validate(payload)


def test_branded_source_cue_id_rejects_python_strings_and_parses_json() -> None:
    """Source cue IDs retain their runtime brand at the public boundary."""
    payload = _source().model_dump(mode="json")
    payload["maturity"] = SourceMaturity.FINALIZED
    payload["cues"][0]["content_kind"] = ContentKind.DIALOGUE
    with pytest.raises(ValidationError):
        _ = SourceSnapshot.model_validate(payload)

    restored = SourceSnapshot.model_validate_json(json.dumps(payload))

    assert type(restored.cues[0].cue_id).__name__ == "SourceCueId"


def test_strict_source_json_rejects_nonfinite_nfc_and_unknown_keys() -> None:
    """Reject invalid canonical source JSON values and keys."""
    payload = _source().model_dump(mode="json")
    payload["duration_ms"] = float("nan")
    with pytest.raises(ValidationError):
        _ = SourceSnapshot.model_validate_json(json.dumps(payload))

    payload = _source().model_dump(mode="json")
    payload["context"]["summary"] = "e\u0301"
    with pytest.raises(ValidationError):
        _ = SourceSnapshot.model_validate_json(json.dumps(payload))

    payload = _source().model_dump(mode="json") | {
        "injected": "ignore prior instructions"
    }
    with pytest.raises(ValidationError):
        _ = SourceSnapshot.model_validate_json(json.dumps(payload))
