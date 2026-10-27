# Copyright 2026 Transcription Module contributors

"""Round-two production boundaries fail closed on unverified ownership."""

from pathlib import Path
from typing import cast

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_claim_support import load_policy_window_input
from tests.integration.window_keep_claim_support import (
    simultaneous_keep_draft,
    simultaneous_unresolved_draft,
)
from tests.integration.window_mutation_claim_support import replacement_draft
from tests.integration.window_policy_support import create_policy_window_fixture
from transcription_module._drafting_models import DiarizationRunArtifact
from transcription_module.drafting import (
    UnresolvedItem,
    UnresolvedReasonCode,
    WindowCueResolution,
    WindowDraft,
    WindowDraftArtifact,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.models import RunId
from transcription_module.storage import write_model_atomic
from transcription_module.visual_evidence_policy import CorrectionAction
from transcription_module.window_evidence import (
    WindowEvidenceError,
    validate_window_draft,
    validate_window_draft_artifact,
)


def test_resolution_state_and_same_start_ambiguity_are_enforced(
    tmp_path: Path,
) -> None:
    """UNRESOLVED needs one typed record; AUTHORIZED cannot retain ambiguity."""
    data = load_policy_window_input(tmp_path)
    original = simultaneous_keep_draft(data)
    first = original.cue_evidence[0]
    missing_record = original.model_copy(
        update={
            "cue_evidence": (
                first.model_copy(update={"resolution": WindowCueResolution.UNRESOLVED}),
                original.cue_evidence[1],
            )
        }
    )
    ambiguity = UnresolvedItem(
        at_ms=original.cues[0].start_tc_ms,
        field="speaker",
        reason="동시 발화 화자 소유권 확인 필요",
        reason_code=UnresolvedReasonCode.SAME_START_TURN_AMBIGUITY,
        target_cue_index=0,
        source_evidence_ids=first.source_evidence_ids,
    )
    authorized_ambiguous = original.model_copy(update={"unresolved": (ambiguity,)})

    rejected: list[bool] = []
    for draft in (missing_record, authorized_ambiguous):
        try:
            validate_window_draft(data, draft)
        except WindowEvidenceError:
            rejected.append(True)
        else:
            rejected.append(False)

    assert rejected == [True, True]


def test_same_cluster_without_verification_publishes_only_typed_holds(
    tmp_path: Path,
) -> None:
    """The supported fallback has unnamed cues and one unresolved record each."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    diarization = DiarizationRunArtifact.model_validate_json(
        paths.diarization_artifact.read_text(encoding="utf-8")
    )
    write_model_atomic(
        paths.diarization_artifact,
        diarization.model_copy(
            update={
                "segments": tuple(
                    item.model_copy(update={"cluster": "SAME_CLUSTER"})
                    for item in diarization.segments
                )
            }
        ),
    )

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return simultaneous_unresolved_draft(data)

    artifact = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )
    draft = artifact.drafts[0]

    assert all(item.speaker is None for item in draft.cues)
    assert all(
        item.resolution is WindowCueResolution.UNRESOLVED
        and item.requested_action is CorrectionAction.HOLD_FOR_CONFIRMATION
        and not item.turn_ids
        for item in draft.cue_evidence
    )
    assert tuple(item.target_cue_index for item in draft.unresolved) == (0,)


def test_foreign_window_artifact_run_id_is_rejected(tmp_path: Path) -> None:
    """Artifact replay binds the envelope run to its policy-bound input run."""
    data = load_policy_window_input(tmp_path)
    artifact = WindowDraftArtifact(
        run_id=RunId("foreign-window-run"),
        inputs=(data,),
        drafts=(replacement_draft(data),),
    )

    with pytest.raises(WindowEvidenceError, match="run"):
        validate_window_draft_artifact(artifact)


def _window_errors(
    error: BaseExceptionGroup[BaseException],
) -> tuple[WindowEvidenceError, ...]:
    found: list[WindowEvidenceError] = []
    for nested in error.exceptions:
        if isinstance(nested, WindowEvidenceError):
            found.append(nested)
        elif isinstance(nested, BaseExceptionGroup):
            found.extend(
                _window_errors(cast("BaseExceptionGroup[BaseException]", nested))
            )
    return tuple(found)
