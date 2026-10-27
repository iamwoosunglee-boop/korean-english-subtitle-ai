# Copyright 2026 Transcription Module contributors

"""Raw visual candidates route into existing policy outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from tests.integration.visual_review_support import (
    TEST_VISUAL_REVIEWER_IDENTITY,
)
from tests.integration.window_mutation_claim_support import replacement_draft
from tests.integration.window_policy_support import create_policy_window_fixture
from transcription_module.drafting import (
    CandidateDispositionKind,
    FrameVisualObservation,
    RawCandidateDisposition,
    RawVisualCandidate,
    RawVisualRole,
    VisualContextSpan,
    VisualReviewerIdentity,
    WindowDraft,
    WindowDraftInput,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
    create_window_drafts,
)
from transcription_module.window_evidence import WindowEvidenceError

if TYPE_CHECKING:
    from pathlib import Path


class CandidateVisualReviewer:
    """Return one speech caption and one non-speech text candidate."""

    @property
    def identity(self) -> VisualReviewerIdentity:
        """Return the fixed test reviewer identity."""
        return TEST_VISUAL_REVIEWER_IDENTITY

    async def __call__(
        self,
        assignment: WindowVisualReviewAssignment,
    ) -> WindowVisualReviewResult:
        """Return complete observations with two distinct visual candidates."""
        speech_frame, non_speech_frame = assignment.frames[:2]
        return WindowVisualReviewResult(
            window_index=assignment.window_index,
            reviewer_input_digest=assignment.reviewer_input_digest,
            frame_observations=tuple(
                FrameVisualObservation(frame_id=frame.frame_id)
                for frame in assignment.frames
            ),
            context_spans=(
                VisualContextSpan(
                    context_span_id="context-1",
                    supporting_frame_ids=(
                        speech_frame.frame_id,
                        non_speech_frame.frame_id,
                    ),
                    context="대화 장면 위에 프로그램 문구가 함께 보인다",
                ),
            ),
            candidates=(
                RawVisualCandidate(
                    candidate_id="candidate-speech",
                    complete_text="서울에서 다시 만났어요",
                    proposed_role=RawVisualRole.SPEECH_CAPTION,
                    supporting_frame_ids=(speech_frame.frame_id,),
                    context_span_ids=("context-1",),
                    evidence_for=("발화에 따라 문구가 갱신된다",),
                ),
                RawVisualCandidate(
                    candidate_id="candidate-logo",
                    complete_text="프로그램 로고",
                    proposed_role=RawVisualRole.NON_SPEECH_TEXT,
                    supporting_frame_ids=(non_speech_frame.frame_id,),
                    context_span_ids=("context-1",),
                    evidence_for=("대화와 무관하게 고정되어 있다",),
                ),
            ),
        )


def test_raw_candidates_require_one_downstream_disposition(
    tmp_path: Path,
) -> None:
    """A drafter cannot silently drop speech or non-speech raw candidates."""
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return replacement_draft(data)

    with pytest.RaisesGroup(
        pytest.RaisesExc(
            WindowEvidenceError,
            match="raw visual candidate dispositions",
        )
    ):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            CandidateVisualReviewer(),
            drafter,
        )


def test_existing_cue_correction_preserves_non_speech_as_raw_evidence(
    tmp_path: Path,
) -> None:
    """A speech candidate repairs one cue while a logo creates no dialogue."""
    fixture = create_policy_window_fixture(tmp_path)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        draft = replacement_draft(data)
        return draft.model_copy(
            update={
                "candidate_dispositions": (
                    RawCandidateDisposition(
                        candidate_id="candidate-speech",
                        kind=CandidateDispositionKind.EXISTING_CUE,
                        target_cue_index=0,
                        evidence_ids=("candidate-speech",),
                    ),
                    RawCandidateDisposition(
                        candidate_id="candidate-logo",
                        kind=CandidateDispositionKind.RETAINED_EVIDENCE,
                    ),
                ),
            }
        )

    artifact = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        CandidateVisualReviewer(),
        drafter,
    )

    assert artifact.drafts[0].cues[0].speech == "서울에서 다시 만났어요"
    assert all(cue.speech != "프로그램 로고" for cue in artifact.drafts[0].cues)
    assert tuple(
        disposition.kind
        for disposition in artifact.drafts[0].candidate_dispositions
    ) == (
        CandidateDispositionKind.EXISTING_CUE,
        CandidateDispositionKind.RETAINED_EVIDENCE,
    )
