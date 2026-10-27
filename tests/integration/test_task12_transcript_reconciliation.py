# Copyright 2026 Transcription Module contributors

"""Deterministic policy-bound whole-transcript reconciliation behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from tests.integration.task12_assembly_support import (
    assemble_without_agent,
    persist_policy_windows,
    policy_assembly_fixture,
    single_claim,
)
from tests.integration.task12_correction_support import replacement_variant
from tests.integration.window_keep_claim_support import (
    asr_keep_draft,
    automatic_keep_draft,
    simultaneous_keep_draft,
)
from transcription_module.models import (
    Transcript,
    Uncertainty,
    WorkflowStatus,
)
from transcription_module.visual_correction import TextSlice
from transcription_module.visual_evidence_policy import CorrectionAction, EvidenceEra

if TYPE_CHECKING:
    from pathlib import Path

    from transcription_module.drafting import WholeTranscriptInput

EXPECTED_SIMULTANEOUS_CUES: Final = 2


def test_exact_source_overlap_collapses_and_applies_one_receipt(
    tmp_path: Path,
) -> None:
    """Given exact context copies, assembly emits one cue and one applied receipt."""
    fixture = policy_assembly_fixture(tmp_path)
    draft = automatic_keep_draft(fixture.window_input)
    _ = persist_policy_windows(fixture, (draft, draft))
    calls: list[WholeTranscriptInput] = []

    artifact = assemble_without_agent(fixture, calls)

    transcript = fixture.store.load_transcript(fixture.run_id)
    assert tuple(cue.speech for cue in transcript.cues) == ("서울에서 다시 만났어요",)
    assert len(artifact.applied_corrections) == 1
    assert artifact.era is EvidenceEra.POLICY_BOUND
    assert artifact.policy_digest == fixture.window_input.policy_digest
    assert artifact.source_digest == fixture.window_input.source_digest
    assert fixture.store.load_state(fixture.run_id).workflow_status is (
        WorkflowStatus.READY_FOR_VALIDATION
    )
    assert calls == []


def test_text_and_time_alone_do_not_collapse_distinct_source_refs(
    tmp_path: Path,
) -> None:
    """Given equal words and timing, disjoint source identities remain two cues."""
    fixture = policy_assembly_fixture(tmp_path)
    simultaneous = simultaneous_keep_draft(fixture.window_input)
    first = single_claim(simultaneous, 0)
    second = single_claim(simultaneous, 1)
    _ = persist_policy_windows(fixture, (second, first))

    _ = assemble_without_agent(fixture, [])

    transcript = fixture.store.load_transcript(fixture.run_id)
    assert len(transcript.cues) == EXPECTED_SIMULTANEOUS_CUES
    assert len({cue.cue_id for cue in transcript.cues}) == EXPECTED_SIMULTANEOUS_CUES


def test_same_time_same_text_distinct_speakers_keep_turn_order(
    tmp_path: Path,
) -> None:
    """Given independent verified turns, simultaneous speakers both survive."""
    fixture = policy_assembly_fixture(tmp_path)
    draft = simultaneous_keep_draft(fixture.window_input)
    _ = persist_policy_windows(fixture, (draft,))

    _ = assemble_without_agent(fixture, [])

    transcript = fixture.store.load_transcript(fixture.run_id)
    assert tuple(cue.speaker for cue in transcript.cues) == ("화자A", "화자B")
    assert tuple(cue.speech for cue in transcript.cues) == ("동시에 대답해요",) * 2


def test_valid_correction_beats_unchanged_copy(tmp_path: Path) -> None:
    """Given one KEEP and one valid edit of the same source, the edit wins once."""
    fixture = policy_assembly_fixture(tmp_path)
    unchanged = asr_keep_draft(fixture.window_input)
    candidate = "서울에서 다시 만났어요"
    corrected = replacement_variant(
        fixture.window_input,
        TextSlice(start=len(candidate) - 2, end=len(candidate)),
    )
    _ = persist_policy_windows(fixture, (unchanged, corrected))

    artifact = assemble_without_agent(fixture, [])

    transcript = fixture.store.load_transcript(fixture.run_id)
    assert tuple(cue.speech for cue in transcript.cues) == (candidate,)
    assert tuple(item.action for item in artifact.applied_corrections) == (
        CorrectionAction.REPLACE_SPAN,
    )


def test_disagreeing_valid_corrections_abstain_with_complete_candidates(
    tmp_path: Path,
) -> None:
    """Given two valid edits, assembly keeps source and exposes both full choices."""
    fixture = policy_assembly_fixture(tmp_path)
    candidate = "서울에서 다시 만났어요"
    first = replacement_variant(
        fixture.window_input,
        TextSlice(start=len(candidate) - 2, end=len(candidate)),
        observation_id="obs-correction-a",
    )
    second = replacement_variant(
        fixture.window_input,
        TextSlice(start=len(candidate) - 1, end=len(candidate)),
        observation_id="obs-correction-b",
    )
    _ = persist_policy_windows(fixture, (second, first))

    artifact = assemble_without_agent(fixture, [])

    transcript = fixture.store.load_transcript(fixture.run_id)
    unresolved = artifact.unresolved[0]
    expected = tuple(sorted((first.cues[0].speech, second.cues[0].speech)))
    expected_receipts = tuple(
        receipt.receipt_id
        for _, receipt in sorted(
            (
                (first.cues[0].speech, first.corrections[0]),
                (second.cues[0].speech, second.corrections[0]),
            )
        )
    )
    assert transcript.cues[0].speech == "서울에서 다시 만났어"
    assert transcript.cues[0].uncertainty is Uncertainty.NEEDS_CONFIRMATION
    assert unresolved.candidates == expected
    assert unresolved.candidate_receipt_ids == expected_receipts
    assert unresolved.source_evidence_ids == tuple(
        sorted(
            {
                *first.cue_evidence[0].source_evidence_ids,
                *second.cue_evidence[0].source_evidence_ids,
            }
        )
    )
    assert unresolved.reason_code is not None
    assert (
        artifact.applied_corrections[0].action is CorrectionAction.HOLD_FOR_CONFIRMATION
    )
    assert artifact.applied_corrections[0].observation_ids == ("obs-correction-a",)


def test_policy_bound_agent_callback_is_never_invoked(tmp_path: Path) -> None:
    """Given a valid policy window, deterministic code owns whole assembly."""
    fixture = policy_assembly_fixture(tmp_path)
    _ = persist_policy_windows(
        fixture,
        (automatic_keep_draft(fixture.window_input),),
    )
    calls: list[WholeTranscriptInput] = []

    _ = assemble_without_agent(fixture, calls)

    assert calls == []


def test_policy_replay_preserves_every_published_byte(tmp_path: Path) -> None:
    """Given unresolved output, exact replay changes no authority bytes."""
    fixture = policy_assembly_fixture(tmp_path)
    candidate = "서울에서 다시 만났어요"
    drafts = (
        replacement_variant(
            fixture.window_input,
            TextSlice(start=len(candidate) - 2, end=len(candidate)),
        ),
        replacement_variant(
            fixture.window_input,
            TextSlice(start=len(candidate) - 1, end=len(candidate)),
        ),
    )
    _ = persist_policy_windows(fixture, drafts)
    first = assemble_without_agent(fixture, [])
    paths = fixture.store.paths(fixture.run_id)
    published = tuple(
        path.read_bytes()
        for path in (
            paths.transcript,
            paths.transcript_csv,
            paths.state,
            paths.confirmations,
            paths.whole_transcript_artifact,
        )
    )

    _ = persist_policy_windows(fixture, tuple(reversed(drafts)))
    replay = assemble_without_agent(fixture, [])

    assert replay == first
    assert published == tuple(
        path.read_bytes()
        for path in (
            paths.transcript,
            paths.transcript_csv,
            paths.state,
            paths.confirmations,
            paths.whole_transcript_artifact,
        )
    )
    assert Transcript.model_validate_json(
        published[0]
    ) == fixture.store.load_transcript(fixture.run_id)
