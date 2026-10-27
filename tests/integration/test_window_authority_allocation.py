# Copyright 2026 Transcription Module contributors

"""Whole-window allocation prevents every form of shared source authority."""

from pathlib import Path

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_claim_support import (
    DraftBuilder,
    load_policy_window_input,
)
from tests.integration.window_keep_claim_support import (
    asr_keep_draft,
    automatic_keep_draft,
    simultaneous_keep_draft,
)
from tests.integration.window_mutation_claim_support import (
    insertion_draft,
    replacement_draft,
)
from tests.integration.window_policy_support import create_policy_window_fixture
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
)


@pytest.mark.parametrize(
    "builder",
    [
        automatic_keep_draft,
        asr_keep_draft,
        replacement_draft,
        insertion_draft,
        simultaneous_keep_draft,
    ],
    ids=("automatic-keep", "asr-keep", "replace", "insert", "simultaneous"),
)
def test_disjoint_authority_claims_publish_through_the_real_boundary(
    tmp_path: Path,
    builder: DraftBuilder,
) -> None:
    """Legitimate actions and two same-time speakers retain exact authority."""
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return builder(data)

    artifact = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert artifact.drafts[0].cue_evidence
    stored = fixture.store.paths(fixture.run_id).window_drafts.read_bytes()
    assert stored == artifact.model_dump_json(indent=2).encode() + b"\n"


def test_distinct_receipts_cannot_reuse_turn_asr_or_subtitle_authority(
    tmp_path: Path,
) -> None:
    """New receipt IDs cannot duplicate one source event into a second speaker."""
    data = load_policy_window_input(tmp_path)
    original = simultaneous_keep_draft(data)
    first = original.cue_evidence[0]
    second = original.cue_evidence[1].model_copy(
        update={
            "asr_ordinals": first.asr_ordinals,
            "subtitle_cue_ids": first.subtitle_cue_ids,
            "turn_ids": first.turn_ids,
            "source_evidence_ids": (
                "asr:3",
                "subtitle:automatic-ko-orig:2",
                "vision:obs-same-b",
            ),
        }
    )
    second_observation = original.observations[1].model_copy(
        update={"turn_ids": ("turn:2",)}
    )
    forged = original.model_copy(
        update={
            "observations": (original.observations[0], second_observation),
            "cue_evidence": (first, second),
        }
    )

    with pytest.raises(WindowEvidenceError, match="reused across cue claims"):
        validate_window_draft(data, forged)


def test_distinct_receipts_cannot_share_one_observation(tmp_path: Path) -> None:
    """One observation cannot independently authorize two output identities."""
    data = load_policy_window_input(tmp_path)
    original = simultaneous_keep_draft(data)
    second_receipt = original.corrections[1].model_copy(
        update={"observation_ids": ("obs-same-a",)}
    )
    second = original.cue_evidence[1].model_copy(
        update={
            "observation_ids": ("obs-same-a",),
            "source_evidence_ids": (
                "asr:4",
                "subtitle:automatic-ko-orig:3",
                "vision:obs-same-a",
            ),
        }
    )
    forged = original.model_copy(
        update={
            "corrections": (original.corrections[0], second_receipt),
            "cue_evidence": (original.cue_evidence[0], second),
        }
    )

    with pytest.raises(WindowEvidenceError, match=r"observation.*reused"):
        validate_window_draft(data, forged)


def test_one_receipt_cannot_be_claimed_by_two_output_cues(tmp_path: Path) -> None:
    """A set comparison cannot collapse duplicate receipt applications."""
    data = load_policy_window_input(tmp_path)
    original = simultaneous_keep_draft(data)
    second = original.cue_evidence[1].model_copy(
        update={"correction_receipt_id": original.corrections[0].receipt_id}
    )
    forged = original.model_copy(
        update={"cue_evidence": (original.cue_evidence[0], second)}
    )

    with pytest.raises(WindowEvidenceError, match="used exactly once"):
        validate_window_draft(data, forged)


def test_duplicate_source_identity_is_rejected_before_allocation(
    tmp_path: Path,
) -> None:
    """Duplicate input ordinals never collapse into one map entry."""
    data = load_policy_window_input(tmp_path)
    duplicate = data.asr_segments[0].model_copy(update={"speech": "다른 내용"})
    forged_input = data.model_copy(
        update={"asr_segments": (*data.asr_segments, duplicate)}
    )

    with pytest.raises(WindowEvidenceError, match="ASR identities must be unique"):
        validate_window_draft(forged_input, replacement_draft(data))
