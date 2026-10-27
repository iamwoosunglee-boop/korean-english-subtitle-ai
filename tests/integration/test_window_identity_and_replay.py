# Copyright 2026 Transcription Module contributors

"""Window artifacts retain exact policy identity across persistence and replay."""

from pathlib import Path

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_mutation_claim_support import replacement_draft
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftArtifact,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import write_model_atomic
from transcription_module.vad_evidence import VadEvidenceArtifact
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
    validate_window_draft_artifact,
)


def test_serialized_artifact_replays_to_the_same_semantic_result(
    tmp_path: Path,
) -> None:
    """Parsing exact published bytes and replaying them changes no semantics."""
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return replacement_draft(data)

    original = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )
    stored = WindowDraftArtifact.model_validate_json(
        fixture.store.paths(fixture.run_id).window_drafts.read_text(encoding="utf-8")
    )

    validate_window_draft_artifact(stored)
    assert stored == original


@pytest.mark.parametrize(
    "field",
    ["policy_digest", "inventory_digest", "source_digest"],
)
def test_foreign_output_identity_is_rejected(field: str, tmp_path: Path) -> None:
    """No draft can import authority from another policy, inventory, or source."""
    data = _load_input(tmp_path)
    foreign = replacement_draft(data).model_copy(update={field: "f" * 64})
    expected = f"foreign {field.removesuffix('_digest')}"

    with pytest.raises(WindowEvidenceError, match=expected):
        validate_window_draft(data, foreign)


def test_legacy_boolean_parses_but_grants_no_policy_authority(tmp_path: Path) -> None:
    """Historical evidence_reviewed remains readable and never authorizes output."""
    data = _load_input(tmp_path)
    legacy = WindowDraft(
        window_index=0,
        cues=(),
        unresolved=(),
        evidence_reviewed=True,
    )

    with pytest.raises(WindowEvidenceError, match="foreign policy"):
        validate_window_draft(data, legacy)


def test_tampered_persisted_receipt_fails_artifact_replay(tmp_path: Path) -> None:
    """Parsing digest-shaped JSON does not bypass semantic receipt replay."""
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return replacement_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )
    path = fixture.store.paths(fixture.run_id).window_drafts
    stored = WindowDraftArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    draft = stored.drafts[0]
    stale = draft.corrections[0].model_copy(update={"input_digest": "f" * 64})
    forged_draft = draft.model_copy(update={"corrections": (stale,)})
    forged = stored.model_copy(update={"drafts": (forged_draft,)})
    reparsed = WindowDraftArtifact.model_validate_json(forged.model_dump_json())

    with pytest.raises(WindowEvidenceError, match=r"receipt.*replay"):
        validate_window_draft_artifact(reparsed)


def test_repeated_frozen_input_publishes_byte_identical_artifact(
    tmp_path: Path,
) -> None:
    """No random IDs or hidden state alter a repeated policy draft."""
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return replacement_draft(data)

    first = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )
    path = fixture.store.paths(fixture.run_id).window_drafts
    first_bytes = path.read_bytes()
    second = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert path.read_bytes() == first_bytes
    assert second == first


@pytest.mark.parametrize("attempt", [1, 2])
def test_policy_worker_failure_never_publishes_partial_windows(
    tmp_path: Path,
    attempt: int,
) -> None:
    """A failed policy validation leaves no partial artifact on repeated runs."""
    fixture = create_policy_window_fixture(tmp_path / f"attempt-{attempt}")
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    vad = VadEvidenceArtifact.model_validate_json(
        paths.vad_artifact.read_text(encoding="utf-8")
    )
    write_model_atomic(
        paths.media_manifest,
        media.model_copy(update={"duration_ms": 610_000}),
    )
    write_model_atomic(
        paths.vad_artifact,
        vad.model_copy(update={"duration_ms": 610_000}),
    )

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        if data.window_index == 1:
            return WindowDraft(
                window_index=data.window_index,
                cues=(),
                unresolved=(),
                evidence_reviewed=True,
            )
        return empty_policy_draft(data)

    with pytest.raises(ExceptionGroup):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert not paths.window_drafts.exists()


def _load_input(tmp_path: Path) -> WindowDraftInput:
    fixture = create_policy_window_fixture(tmp_path)
    captured: list[WindowDraftInput] = []

    async def capture(data: WindowDraftInput) -> WindowDraft:
        captured.append(data)
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        capture,
    )
    return captured[0]
