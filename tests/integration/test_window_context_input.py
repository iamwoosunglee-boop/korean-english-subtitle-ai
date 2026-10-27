# Copyright 2026 Transcription Module contributors

"""Policy windows retain persisted episode context for semantic drafting."""

from pathlib import Path

import anyio
from pydantic import JsonValue, TypeAdapter

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.models import EpisodeContext

TYPED_CONTEXT = Path("tests/fixtures/episode-context-four-person.v2.json")
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def test_policy_window_drafter_receives_persisted_episode_context(
    tmp_path: Path,
) -> None:
    """A policy drafter receives the roster and appearance hints from context."""
    fixture = create_policy_window_fixture(tmp_path, duration_ms=610_000)
    state = fixture.store.load_state(fixture.run_id)
    context = EpisodeContext.model_validate_json(TYPED_CONTEXT.read_bytes())
    fixture.store.save_state(state.model_copy(update={"context": context}))
    captured: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert tuple(item.window_index for item in captured) == (0, 1, 2)
    assert all(item.episode_context == context for item in captured)
    expected = _JSON_OBJECT.validate_python(context.model_dump(mode="json"))
    assert all(
        item.episode_context is not None
        and _JSON_OBJECT.validate_python(
            item.episode_context.model_dump(mode="json")
        )["context_digest"]
        == expected["context_digest"]
        for item in captured
    )


def test_policy_window_drafter_keeps_legacy_raw_context(
    tmp_path: Path,
) -> None:
    """Legacy text remains readable without synthesizing a typed roster."""
    fixture = create_policy_window_fixture(tmp_path)
    captured: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert captured[0].episode_context == EpisodeContext(raw_text="없음")
