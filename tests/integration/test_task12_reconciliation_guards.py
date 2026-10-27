# Copyright 2026 Transcription Module contributors

"""Fail-closed guards inside deterministic receipt reconciliation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

import transcription_module._drafting_reconciliation as reconciliation_module
import transcription_module._drafting_reconciliation_resolution as resolution_module
from tests.integration.task12_assembly_support import policy_assembly_fixture
from tests.integration.task12_correction_support import (
    insertion_variant,
    replacement_variant,
)
from tests.integration.window_keep_claim_support import (
    automatic_keep_draft,
    simultaneous_unresolved_draft,
)
from transcription_module._drafting_models import (
    DraftCue,
    WindowDraft,
    WindowDraftInput,
)
from transcription_module._drafting_reconciliation_models import (
    ReconciliationCandidate,
)
from transcription_module.visual_correction import TextSlice
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_authority_allocation import (
    AllocatedCue,
    allocate_window_authority,
)
from transcription_module.window_authority_sources import build_window_authority
from transcription_module.window_evidence import WindowEvidenceError

if TYPE_CHECKING:
    from pathlib import Path


def _candidate(
    data: WindowDraftInput,
    draft: WindowDraft,
) -> ReconciliationCandidate:
    authority = build_window_authority(data, draft)
    allocated = allocate_window_authority(authority, draft)[0]
    unresolved = tuple(item for item in draft.unresolved if item.target_cue_index == 0)
    return ReconciliationCandidate(allocated, unresolved)


def test_missing_or_mixed_digest_identity_fails_closed() -> None:
    """Given no sole digest authority, reconciliation cannot choose one."""
    with pytest.raises(WindowEvidenceError, match="mixed or missing policy"):
        _ = reconciliation_module.require_shared_digest((None,), "policy")
    with pytest.raises(WindowEvidenceError, match="mixed or missing source"):
        _ = reconciliation_module.require_shared_digest(("a", "b"), "source")


def test_reused_identity_with_different_payload_fails_closed() -> None:
    """Given one identity with two payloads, neither may become authority."""
    identities: dict[tuple[str, str], str] = {}
    first = DraftCue(start_tc_ms=0, end_tc_ms=100, speech="첫째")
    second = DraftCue(start_tc_ms=0, end_tc_ms=100, speech="둘째")
    reconciliation_module.register_evidence_identity(identities, ("asr", "1"), first)

    with pytest.raises(WindowEvidenceError, match="conflicting evidence"):
        reconciliation_module.register_evidence_identity(
            identities,
            ("asr", "1"),
            second,
        )


def test_source_claim_without_lexical_identity_fails_closed(tmp_path: Path) -> None:
    """Given a forged KEEP record without ASR/subtitle IDs, grouping stops."""
    fixture = policy_assembly_fixture(tmp_path)
    draft = automatic_keep_draft(fixture.window_input)
    candidate = _candidate(fixture.window_input, draft)
    record = candidate.allocated.record.model_copy(
        update={"asr_ordinals": (), "subtitle_cue_ids": ()}
    )
    forged = replace(
        candidate,
        allocated=AllocatedCue(
            candidate.allocated.cue,
            record,
            candidate.allocated.observation,
            candidate.allocated.receipt,
        ),
    )

    with pytest.raises(WindowEvidenceError, match="lacks a source identity"):
        _ = reconciliation_module.source_group_key(forged)


def test_source_claim_with_unknown_action_fails_closed(tmp_path: Path) -> None:
    """Given a forged action outside the policy enum, grouping stops."""
    fixture = policy_assembly_fixture(tmp_path)
    candidate = _candidate(
        fixture.window_input,
        automatic_keep_draft(fixture.window_input),
    )
    forged = replace(
        candidate,
        allocated=replace(
            candidate.allocated,
            record=candidate.allocated.record.model_copy(
                update={"requested_action": "forged"}
            ),
        ),
    )

    with pytest.raises(WindowEvidenceError, match="unsupported correction action"):
        _ = reconciliation_module.source_group_key(forged)


def test_conflicting_visual_insertions_fail_closed(tmp_path: Path) -> None:
    """Given one insertion identity with unequal receipt text, assembly stops."""
    fixture = policy_assembly_fixture(tmp_path)
    first = _candidate(
        fixture.window_input,
        insertion_variant(
            fixture.window_input,
            observation_id="obs-insert-a",
            turn_id="turn:2",
            speaker="화자A",
        ),
    )
    second = replace(
        first,
        allocated=replace(
            first.allocated,
            receipt=first.allocated.receipt.model_copy(
                update={"result_text": "forged insertion"}
            ),
        ),
    )

    with pytest.raises(WindowEvidenceError, match="conflicting receipts"):
        _ = resolution_module.reconcile_group((first, second))


def test_conflicting_edits_require_one_exact_before_text(tmp_path: Path) -> None:
    """Given disagreeing edits without exact source text, abstention stops."""
    fixture = policy_assembly_fixture(tmp_path)
    candidate = "서울에서 다시 만났어요"
    first = _candidate(
        fixture.window_input,
        replacement_variant(
            fixture.window_input,
            TextSlice(start=len(candidate) - 2, end=len(candidate)),
        ),
    )
    second = _candidate(
        fixture.window_input,
        replacement_variant(
            fixture.window_input,
            TextSlice(start=len(candidate) - 1, end=len(candidate)),
        ),
    )
    forged = replace(
        first,
        allocated=replace(
            first.allocated,
            receipt=first.allocated.receipt.model_copy(update={"before_text": None}),
        ),
    )

    with pytest.raises(WindowEvidenceError, match="exact source text"):
        _ = resolution_module.reconcile_group((forged, second))


def test_conflicting_edit_without_result_text_fails_closed(tmp_path: Path) -> None:
    """Given a legacy-optional result missing from an edit, abstention stops."""
    fixture = policy_assembly_fixture(tmp_path)
    candidate = "서울에서 다시 만났어요"
    first = _candidate(
        fixture.window_input,
        replacement_variant(
            fixture.window_input,
            TextSlice(start=len(candidate) - 2, end=len(candidate)),
        ),
    )
    second = _candidate(
        fixture.window_input,
        replacement_variant(
            fixture.window_input,
            TextSlice(start=len(candidate) - 1, end=len(candidate)),
        ),
    )
    forged = replace(
        first,
        allocated=replace(
            first.allocated,
            receipt=first.allocated.receipt.model_copy(update={"result_text": None}),
        ),
    )

    with pytest.raises(WindowEvidenceError, match="lacks result text"):
        _ = resolution_module.reconcile_group((forged, second))


def test_task11_hold_receipt_remains_the_selected_authority(tmp_path: Path) -> None:
    """Given a validated HOLD claim, reconciliation preserves its uncertainty."""
    fixture = policy_assembly_fixture(tmp_path)
    held = _candidate(
        fixture.window_input,
        simultaneous_unresolved_draft(fixture.window_input),
    )

    reconciled = resolution_module.reconcile_group((held,))

    assert reconciled.receipt.action is CorrectionAction.HOLD_FOR_CONFIRMATION
    assert len(reconciled.unresolved) == 1
