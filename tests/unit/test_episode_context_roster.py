# Copyright 2026 Transcription Module contributors

"""Typed current-context roster contracts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from transcription_module.models import ContextPerson, EpisodeContext

FIXTURES = Path("tests/fixtures")
VALID_CONTEXT = FIXTURES / "episode-context-four-person.v2.json"
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_JSON_ARRAY = TypeAdapter(list[JsonValue])
_ROSTER = TypeAdapter(list[dict[str, JsonValue]])
_STRING = TypeAdapter(str)


def _payload() -> dict[str, JsonValue]:
    return _JSON_OBJECT.validate_json(VALID_CONTEXT.read_bytes())


def _roster(payload: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    return _ROSTER.validate_python(payload["roster"])


def _canonical_digest(payload: dict[str, JsonValue]) -> str:
    canonical = deepcopy(payload)
    del canonical["context_digest"]
    roster = _roster(canonical)
    for person in roster:
        hints = TypeAdapter(list[str]).validate_python(person["appearance_hints"])
        person["appearance_hints"] = _JSON_ARRAY.validate_python(sorted(hints))
    canonical["roster"] = _JSON_ARRAY.validate_python(
        sorted(
            roster,
            key=lambda person: _STRING.validate_python(person["person_id"]),
        )
    )
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_legacy_raw_context_json_remains_readable() -> None:
    """Parse pre-versioned raw context without inventing typed authority."""
    context = EpisodeContext.model_validate_json(
        '{"raw_text":"없음","characters":["화자1"]}'
    )
    stored = _JSON_OBJECT.validate_python(context.model_dump(mode="json"))

    assert stored["schema_version"] == "episode-context.v1"
    assert context.raw_text == "없음"
    assert context.characters == ("화자1",)
    assert stored["roster"] == []
    assert stored["context_digest"] is None


def test_typed_roster_round_trips_canonical_bytes() -> None:
    """Round-trip one grounded four-person roster and its independent digest."""
    payload = _payload()

    context = EpisodeContext.model_validate(payload)
    reparsed = EpisodeContext.model_validate_json(context.model_dump_json())
    stored = _JSON_OBJECT.validate_python(context.model_dump(mode="json"))
    roster = _roster(stored)

    assert reparsed == context
    assert stored["context_digest"] == _canonical_digest(payload)
    assert tuple(_STRING.validate_python(person["person_id"]) for person in roster) == (
        "gabi",
        "kim-jin-man",
        "kim-yoon-a",
        "lee-seon-gyu",
    )


def test_bind_typed_reproduces_the_canonical_fixture() -> None:
    """Bind trusted typed fields without a second persisted payload model."""
    expected = EpisodeContext.model_validate_json(VALID_CONTEXT.read_bytes())

    bound = EpisodeContext.bind_typed(
        raw_text=expected.raw_text,
        roster=expected.roster,
        subtitle_trust_override=expected.subtitle_trust_override,
    )

    assert bound == expected


def test_roster_order_and_hint_order_have_no_authority() -> None:
    """Normalize permutations so tuple order cannot assign a speaker."""
    payload = _payload()
    reversed_payload = deepcopy(payload)
    roster = _roster(reversed_payload)
    roster.reverse()
    reversed_payload["roster"] = _JSON_ARRAY.validate_python(roster)

    canonical = EpisodeContext.model_validate(payload)
    permuted = EpisodeContext.model_validate(reversed_payload)

    assert permuted == canonical
    assert permuted.model_dump(mode="json") == canonical.model_dump(mode="json")


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("person_id", "roster person_id values must be unique"),
        ("display_name", "roster display_name values must be unique"),
    ],
)
def test_typed_context_rejects_duplicate_roster_identity(
    field: str,
    message: str,
) -> None:
    """Reject duplicate stable IDs and duplicate candidate names."""
    payload = _payload()
    roster = _roster(payload)
    roster[1][field] = roster[0][field]
    payload["roster"] = _JSON_ARRAY.validate_python(roster)

    with pytest.raises(ValidationError, match=message):
        _ = EpisodeContext.model_validate(payload)


@pytest.mark.parametrize(
    "hints",
    [[], [""], [" 검은 긴머리"], ["검은 긴머리", "검은 긴머리"]],
)
def test_context_person_rejects_invalid_hints(hints: list[str]) -> None:
    """Reject missing, blank, padded, or repeated appearance excerpts."""
    payload = _payload()
    roster = _roster(payload)
    roster[2]["appearance_hints"] = _JSON_ARRAY.validate_python(hints)
    payload["roster"] = _JSON_ARRAY.validate_python(roster)

    with pytest.raises(ValidationError):
        _ = EpisodeContext.model_validate(payload)


def test_shared_hint_across_people_grants_no_turn_authority() -> None:
    """Allow a shared appearance category without choosing either person."""
    context = EpisodeContext.bind_typed(
        raw_text="루미와 박도현은 모두 검은 머리다.",
        roster=(
            ContextPerson(
                person_id="gabi",
                display_name="루미",
                appearance_hints=("검은 머리",),
            ),
            ContextPerson(
                person_id="kim-jin-man",
                display_name="박도현",
                appearance_hints=("검은 머리",),
            ),
        ),
    )

    assert tuple(person.appearance_hints for person in context.roster) == (
        ("검은 머리",),
        ("검은 머리",),
    )
    assert all(
        "turn" not in person.model_dump(mode="json")
        for person in context.roster
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("person_id", " padded-id"),
        ("display_name", " 한지아"),
        ("display_name", " "),
    ],
)
def test_context_person_rejects_whitespace_values(
    field: str,
    value: str,
) -> None:
    """Reject whitespace rather than silently normalizing roster identity."""
    payload = _payload()
    roster = _roster(payload)
    roster[2][field] = value
    payload["roster"] = _JSON_ARRAY.validate_python(roster)

    with pytest.raises(ValidationError):
        _ = EpisodeContext.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "display_name",
            "외부인",
            "roster display_name is absent from raw_text",
        ),
        (
            "appearance_hints",
            ["빨간 재킷"],
            "roster appearance hint is absent from raw_text",
        ),
    ],
)
def test_typed_context_rejects_ungrounded_roster_text(
    field: str,
    value: JsonValue,
    message: str,
) -> None:
    """Reject names and appearance excerpts absent from current context."""
    payload = _payload()
    roster = _roster(payload)
    roster[0][field] = value
    payload["roster"] = _JSON_ARRAY.validate_python(roster)

    with pytest.raises(ValidationError, match=message):
        _ = EpisodeContext.model_validate(payload)


def test_v2_requires_roster_digest_and_no_legacy_characters() -> None:
    """Require one digest-bound roster and forbid competing legacy names."""
    empty = _payload()
    empty["roster"] = _JSON_ARRAY.validate_python([])
    empty["context_digest"] = _canonical_digest(empty)
    with pytest.raises(ValidationError, match="requires a non-empty roster"):
        _ = EpisodeContext.model_validate(empty)

    missing = _payload()
    del missing["context_digest"]
    with pytest.raises(ValidationError, match="requires context_digest"):
        _ = EpisodeContext.model_validate(missing)

    legacy = _payload()
    legacy["characters"] = _JSON_ARRAY.validate_python(["루미"])
    with pytest.raises(ValidationError, match="cannot contain legacy characters"):
        _ = EpisodeContext.model_validate(legacy)


def test_context_digest_rejects_stale_or_foreign_payload() -> None:
    """Reject a digest transplanted from different context bytes."""
    payload = _payload()
    payload["raw_text"] = f"{payload['raw_text']} 다른 문장"

    with pytest.raises(ValidationError, match="context_digest does not match"):
        _ = EpisodeContext.model_validate(payload)


def test_context_person_forbids_provider_score_and_registry_fields() -> None:
    """Reject authority metadata outside the roster vocabulary boundary."""
    for field in ("provider", "score", "registry"):
        payload = _payload()
        roster = _roster(payload)
        roster[0][field] = "forbidden"
        payload["roster"] = _JSON_ARRAY.validate_python(roster)

        with pytest.raises(ValidationError) as captured:
            _ = EpisodeContext.model_validate(payload)

        assert captured.value.errors()[0]["type"] == "extra_forbidden"
