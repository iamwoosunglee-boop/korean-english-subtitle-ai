# Copyright 2026 Transcription Module contributors

"""Persisted Task 11 fixtures used by Task 12 assembly tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    WholeTranscriptArtifact,
    WholeTranscriptDraft,
    WholeTranscriptInput,
    WindowDraft,
    WindowDraftArtifact,
    WindowDraftInput,
    assemble_whole_transcript,
    create_window_drafts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.visual_evidence_policy import (
    canonical_policy_bytes,
    default_evidence_policy,
)
from transcription_module.window_evidence import validate_window_draft_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.models import RunId


@dataclass(frozen=True, slots=True)
class PolicyAssemblyFixture:
    """One persisted policy run and its validated local window input."""

    store: RunStore
    run_id: RunId
    window_input: WindowDraftInput


def policy_assembly_fixture(root: Path) -> PolicyAssemblyFixture:
    """Capture the real Task 11 input boundary for later assembly composition."""
    fixture = create_policy_window_fixture(root)
    paths = fixture.store.paths(fixture.run_id)
    policy_payload = canonical_policy_bytes(default_evidence_policy()) + b"\n"
    _ = paths.visual_policy_artifact.write_bytes(policy_payload)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    if media.subtitle_inventory is None:
        message = "policy fixture lacks subtitle inventory"
        raise AssertionError(message)
    write_model_atomic(paths.subtitle_inventory_artifact, media.subtitle_inventory)
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
    return PolicyAssemblyFixture(fixture.store, fixture.run_id, captured[0])


def persist_policy_windows(
    fixture: PolicyAssemblyFixture,
    drafts: tuple[WindowDraft, ...],
    *,
    validate: bool = True,
) -> WindowDraftArtifact:
    """Persist independent copies after optional Task 11 replay validation."""
    inputs = tuple(
        fixture.window_input.model_copy(update={"window_index": index})
        for index in range(len(drafts))
    )
    indexed = tuple(
        draft.model_copy(update={"window_index": index})
        for index, draft in enumerate(drafts)
    )
    artifact = WindowDraftArtifact(
        run_id=fixture.run_id,
        inputs=inputs,
        drafts=indexed,
    )
    if validate:
        validate_window_draft_artifact(artifact, expected_run_id=fixture.run_id)
    write_model_atomic(fixture.store.paths(fixture.run_id).window_drafts, artifact)
    return artifact


def single_claim(draft: WindowDraft, index: int) -> WindowDraft:
    """Project one already-valid claim into a standalone one-cue draft."""
    evidence = draft.cue_evidence[index].model_copy(update={"cue_index": 0})
    return draft.model_copy(
        update={
            "cues": (draft.cues[index],),
            "unresolved": (),
            "observations": (draft.observations[index],),
            "corrections": (draft.corrections[index],),
            "cue_evidence": (evidence,),
        }
    )


def assemble_without_agent(
    fixture: PolicyAssemblyFixture,
    calls: list[WholeTranscriptInput] | None = None,
) -> WholeTranscriptArtifact:
    """Invoke assembly with a callback that proves policy routing excludes agents."""
    observed = [] if calls is None else calls

    async def forbidden(data: WholeTranscriptInput) -> WholeTranscriptDraft:
        observed.append(data)
        message = "policy-bound assembly called the free assembler"
        raise AssertionError(message)

    return anyio.run(
        assemble_whole_transcript,
        fixture.store,
        fixture.run_id,
        forbidden,
    )


__all__ = [
    "PolicyAssemblyFixture",
    "assemble_without_agent",
    "persist_policy_windows",
    "policy_assembly_fixture",
    "single_claim",
]
