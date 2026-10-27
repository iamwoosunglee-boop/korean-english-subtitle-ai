# Copyright 2026 Transcription Module contributors

"""Fresh visual review artifacts can be reused without model invocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    PolicyWindowFixture,
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    create_window_drafts,
    create_window_drafts_from_visual_artifact,
)
from transcription_module.visual_review import visual_review_assignment_digest

if TYPE_CHECKING:
    from pathlib import Path


def _review_artifact(
    tmp_path: Path,
) -> tuple[PolicyWindowFixture, WindowVisualReviewArtifact]:
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )
    artifact = WindowVisualReviewArtifact.model_validate_json(
        fixture.store.paths(fixture.run_id).window_visual_review.read_bytes()
    )
    return fixture, artifact


def test_fresh_visual_artifact_reaches_drafter_without_reviewer(
    tmp_path: Path,
) -> None:
    """Reuse starts after complete raw review and supplies the exact result."""
    fixture, artifact = _review_artifact(tmp_path)
    captured: list[WindowDraftInput] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return empty_policy_draft(data)

    replayed = anyio.run(
        create_window_drafts_from_visual_artifact,
        fixture.store,
        fixture.run_id,
        artifact,
        artifact.assignments[0].reviewer,
        drafter,
    )

    assert len(captured) == len(artifact.assignments)
    assert tuple(item.visual_review for item in captured) == artifact.results
    assert replayed.inputs == tuple(captured)


def test_stale_visual_artifact_blocks_drafter(tmp_path: Path) -> None:
    """A self-consistent artifact with changed frame input is rejected."""
    fixture, artifact = _review_artifact(tmp_path)
    assignment = artifact.assignments[0]
    changed_frame = assignment.frames[0].model_copy(
        update={"path": "foreign/frame.jpg"}
    )
    unbound = assignment.model_copy(
        update={
            "frames": (changed_frame, *assignment.frames[1:]),
            "reviewer_input_digest": "0" * 64,
        }
    )
    stale_assignment = WindowVisualReviewAssignment.model_validate(
        unbound.model_copy(
            update={
                "reviewer_input_digest": visual_review_assignment_digest(
                    unbound
                )
            }
        ).model_dump()
    )
    stale = artifact.model_copy(
        update={
            "assignments": (stale_assignment,),
            "results": (
                artifact.results[0].model_copy(
                    update={
                        "reviewer_input_digest": (
                            stale_assignment.reviewer_input_digest
                        )
                    }
                ),
            ),
        }
    )
    drafter_called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal drafter_called
        drafter_called = True
        return empty_policy_draft(data)

    with pytest.raises(ValueError, match="stale"):
        _ = anyio.run(
            create_window_drafts_from_visual_artifact,
            fixture.store,
            fixture.run_id,
            stale,
            artifact.assignments[0].reviewer,
            drafter,
        )

    assert not drafter_called


def test_changed_selected_reviewer_rejects_fresh_artifact(
    tmp_path: Path,
) -> None:
    """Prompt, model, or settings selection is independent freshness input."""
    fixture, artifact = _review_artifact(tmp_path)
    selected = artifact.assignments[0].reviewer.model_copy(
        update={"settings_digest": "f" * 64}
    )
    drafter_called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal drafter_called
        drafter_called = True
        return empty_policy_draft(data)

    with pytest.raises(ValueError, match="stale"):
        _ = anyio.run(
            create_window_drafts_from_visual_artifact,
            fixture.store,
            fixture.run_id,
            artifact,
            selected,
            drafter,
        )

    assert not drafter_called
