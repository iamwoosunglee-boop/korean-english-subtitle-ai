# Copyright 2026 Transcription Module contributors

"""Inventory-admitted speaker authority replays from exact current bytes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from tests.integration.window_speaker_authority_support import (
    persist_admitted_speaker_turn_authority,
)
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import write_model_atomic
from transcription_module.window_evidence import WindowEvidenceError
from transcription_module.window_evidence_models import (
    LegacyVerifiedSpeakerTurnArtifact,
    LegacyWindowVerifiedTurn,
    VerifiedSpeakerTurnArtifact,
)

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.models import RunId
    from transcription_module.storage import RunStore


def test_inventory_bound_builder_publishes_verified_simultaneous_speakers(
    tmp_path: Path,
) -> None:
    """Distinct current turns retain independently proven simultaneous names."""
    fixture = create_policy_window_fixture(tmp_path)
    persist_admitted_speaker_turn_authority(fixture.store, fixture.run_id)
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

    assert [(item.turn_id, item.speaker) for item in captured[0].verified_turns] == [
        ("turn:1", "화자A"),
        ("turn:2", "화자A"),
        ("turn:3", "화자B"),
    ]


def test_admission_hash_mutation_fails_before_callback(tmp_path: Path) -> None:
    """Changed inventory admission cannot bless unchanged authority bytes."""
    fixture = create_policy_window_fixture(tmp_path)
    persist_admitted_speaker_turn_authority(fixture.store, fixture.run_id)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    inventory = media.subtitle_inventory
    assert inventory is not None
    admission = inventory.evidence_authorities[0].model_copy(
        update={"sha256": "f" * 64}
    )
    write_model_atomic(
        paths.media_manifest,
        media.model_copy(
            update={
                "subtitle_inventory": inventory.model_copy(
                    update={"evidence_authorities": (admission,)}
                )
            }
        ),
    )
    _assert_fails_before_callback(fixture.store, fixture.run_id, "admission")


@pytest.mark.parametrize("proof", ["observation", "decision"])
def test_proof_mutation_fails_before_callback(tmp_path: Path, proof: str) -> None:
    """Changed observation or decision bytes revoke admitted proof."""
    fixture = create_policy_window_fixture(tmp_path)
    persist_admitted_speaker_turn_authority(fixture.store, fixture.run_id)
    paths = fixture.store.paths(fixture.run_id)
    path = (
        paths.visual_observations_artifact
        if proof == "observation"
        else paths.speaker_turn_decisions
    )
    _ = path.write_bytes(path.read_bytes() + b" ")
    _assert_fails_before_callback(fixture.store, fixture.run_id, "evidence bytes")


def test_derived_mutation_fails_exact_replay(tmp_path: Path) -> None:
    """A changed derived name cannot replay from unchanged authority."""
    fixture = create_policy_window_fixture(tmp_path)
    persist_admitted_speaker_turn_authority(fixture.store, fixture.run_id)
    paths = fixture.store.paths(fixture.run_id)
    derived = VerifiedSpeakerTurnArtifact.model_validate_json(
        paths.verified_speaker_turns_artifact.read_bytes()
    )
    changed = derived.turns[0].model_copy(update={"speaker": "변조"})
    write_model_atomic(
        paths.verified_speaker_turns_artifact,
        derived.model_copy(update={"turns": (changed, *derived.turns[1:])}),
    )
    _assert_fails_before_callback(fixture.store, fixture.run_id, "differs")


def test_unadmitted_derived_sidecar_fails_before_callback(tmp_path: Path) -> None:
    """A derived sidecar grants nothing after inventory admission removal."""
    fixture = create_policy_window_fixture(tmp_path)
    persist_admitted_speaker_turn_authority(fixture.store, fixture.run_id)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    inventory = media.subtitle_inventory
    assert inventory is not None
    write_model_atomic(
        paths.media_manifest,
        media.model_copy(
            update={
                "subtitle_inventory": inventory.model_copy(
                    update={"evidence_authorities": ()}
                )
            }
        ),
    )
    _assert_fails_before_callback(fixture.store, fixture.run_id, "unadmitted")


def test_legacy_self_asserted_sidecar_grants_no_authority(tmp_path: Path) -> None:
    """A structurally readable legacy assertion grants no policy authority."""
    fixture = create_policy_window_fixture(tmp_path)
    persist_admitted_speaker_turn_authority(fixture.store, fixture.run_id)
    paths = fixture.store.paths(fixture.run_id)
    write_model_atomic(
        paths.verified_speaker_turns_artifact,
        LegacyVerifiedSpeakerTurnArtifact(
            run_id=fixture.run_id,
            policy_digest="a" * 64,
            audio_sha256="b" * 64,
            diarization_digest="c" * 64,
            turns=(
                LegacyWindowVerifiedTurn(
                    turn_id="turn:1",
                    start_tc_ms=1_000,
                    end_tc_ms=1_800,
                    cluster="SPEAKER_00",
                    speaker="화자A",
                    verification_id="self-asserted",
                    verified=True,
                ),
            ),
        ),
    )
    _assert_fails_before_callback(fixture.store, fixture.run_id, "legacy")


def _assert_fails_before_callback(
    store: RunStore,
    run_id: RunId,
    match: str,
) -> None:
    called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal called
        called = True
        return empty_policy_draft(data)

    with pytest.raises(WindowEvidenceError, match=match):
        _ = anyio.run(
            create_window_drafts,
            store,
            run_id,
            EmptyVisualReviewer(),
            drafter,
        )
    assert not called
