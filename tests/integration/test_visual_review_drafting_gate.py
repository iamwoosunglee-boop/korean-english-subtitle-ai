# Copyright 2026 Transcription Module contributors

"""Cue-independent visual review gates every window drafter."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, override

import anyio
import pytest

from tests.integration.visual_review_support import (
    TEST_VISUAL_REVIEWER_IDENTITY,
    EmptyVisualReviewer,
    empty_visual_review,
)
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    VisualReviewerIdentity,
    WindowDraft,
    WindowDraftInput,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    WindowVisualReviewer,
    WindowVisualReviewResult,
    create_window_drafts,
)

if TYPE_CHECKING:
    from pathlib import Path


EXPECTED_WINDOW_COUNT = 3


class VisualReviewReachedError(Exception):
    """The mandatory visual reviewer was invoked."""


class SentinelVisualReviewer(WindowVisualReviewer):
    """Stop the workflow as soon as visual review begins."""

    @property
    @override
    def identity(self) -> VisualReviewerIdentity:
        """Return the fixed test reviewer identity."""
        return TEST_VISUAL_REVIEWER_IDENTITY

    @override
    async def __call__(
        self,
        _assignment: WindowVisualReviewAssignment,
    ) -> WindowVisualReviewResult:
        """Stop once the mandatory reviewer is reached."""
        raise VisualReviewReachedError


class MissingFrameVisualReviewer:
    """Return no observations for a non-empty assignment."""

    @property
    def identity(self) -> VisualReviewerIdentity:
        """Return the fixed test reviewer identity."""
        return TEST_VISUAL_REVIEWER_IDENTITY

    async def __call__(
        self,
        assignment: WindowVisualReviewAssignment,
    ) -> WindowVisualReviewResult:
        """Return an invalid result that omits every frame."""
        return WindowVisualReviewResult(
            window_index=assignment.window_index,
            reviewer_input_digest=assignment.reviewer_input_digest,
            frame_observations=(),
        )


def test_visual_review_runs_before_window_drafter(tmp_path: Path) -> None:
    """A window drafter cannot run before cue-independent visual review."""
    fixture = create_policy_window_fixture(tmp_path)
    drafter_called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal drafter_called
        drafter_called = True
        return empty_policy_draft(data)

    with pytest.RaisesGroup(VisualReviewReachedError):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            SentinelVisualReviewer(),
            drafter,
        )

    assert not drafter_called


def test_incomplete_visual_review_blocks_drafter_and_publication(
    tmp_path: Path,
) -> None:
    """Missing frame observations fail before any downstream artifact."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    drafter_called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal drafter_called
        drafter_called = True
        return empty_policy_draft(data)

    with pytest.RaisesGroup(
        pytest.RaisesExc(
            ValueError,
            match="observe every assigned frame",
        )
    ):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            MissingFrameVisualReviewer(),
            drafter,
        )

    assert not drafter_called
    assert not paths.window_visual_review.exists()
    assert not paths.window_drafts.exists()


def test_all_visual_reviews_finish_before_any_window_drafter(
    tmp_path: Path,
) -> None:
    """Every raw window result completes before downstream drafting starts."""
    fixture = create_policy_window_fixture(tmp_path, duration_ms=610_000)
    all_reviews_started = anyio.Event()
    release_reviews = anyio.Event()
    review_indexes: list[int] = []
    draft_indexes: list[int] = []

    class BarrierVisualReviewer:
        @property
        def identity(self) -> VisualReviewerIdentity:
            """Return the fixed test reviewer identity."""
            return TEST_VISUAL_REVIEWER_IDENTITY

        async def __call__(
            self,
            assignment: WindowVisualReviewAssignment,
        ) -> WindowVisualReviewResult:
            review_indexes.append(assignment.window_index)
            if len(review_indexes) == EXPECTED_WINDOW_COUNT:
                all_reviews_started.set()
            await release_reviews.wait()
            return empty_visual_review(assignment)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        draft_indexes.append(data.window_index)
        return empty_policy_draft(data)

    async def scenario() -> None:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(
                create_window_drafts,
                fixture.store,
                fixture.run_id,
                BarrierVisualReviewer(),
                drafter,
            )
            with anyio.fail_after(1):
                await all_reviews_started.wait()
            assert draft_indexes == []
            release_reviews.set()

    anyio.run(scenario)

    assert sorted(review_indexes) == [0, 1, 2]
    assert sorted(draft_indexes) == [0, 1, 2]


def test_visual_review_artifact_binds_frame_bytes_and_typed_drafter_input(
    tmp_path: Path,
) -> None:
    """Persisted raw review identity includes exact frame bytes."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
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

    artifact = WindowVisualReviewArtifact.model_validate_json(
        paths.window_visual_review.read_bytes()
    )
    expected = tuple(
        hashlib.sha256((paths.run_dir / frame.path).read_bytes()).hexdigest()
        for frame in artifact.assignments[0].frames
    )
    assert tuple(frame.sha256 for frame in artifact.assignments[0].frames) == expected
    assert captured[0].visual_review == artifact.results[0]
