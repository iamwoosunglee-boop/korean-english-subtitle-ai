# Copyright 2026 Transcription Module contributors

"""Stored scene observations survive export without becoming human answers."""

from hashlib import sha256
from pathlib import Path

import pytest

from tests.integration.canonical_source_export_support import canonical_inputs
from tests.integration.visual_review_support import TEST_VISUAL_REVIEWER_IDENTITY
from transcription_module.canonical_source_export import (
    CanonicalSourceExportError,
    build_canonical_source_pair,
)
from transcription_module.models import EpisodeContext, FrameId, RunId
from transcription_module.storage import write_model_atomic
from transcription_module.visual_review import (
    FrameVisualObservation,
    VisualContextSpan,
    VisualFrameOwnership,
    VisualReviewFrame,
    WindowVisualReviewArtifact,
    WindowVisualReviewAssignment,
    WindowVisualReviewResult,
    visual_review_assignment_digest,
)
from translation_module.canonical.asset_preparation import prepare_translation_assets
from translation_module.canonical.context import ContextDecisionStatus
from translation_module.canonical.digest import (
    bind_canonical_digests,
    verify_canonical_digests,
)
from translation_module.canonical.source import SourceFinalization
from translation_module.canonical.validation import finalize_source, ready_context_asset


def _visual_context(
    run_id: RunId,
    episode: EpisodeContext,
    frame_bytes: bytes,
) -> WindowVisualReviewArtifact:
    frame_id = FrameId("frame-000001")
    assignment = WindowVisualReviewAssignment.model_construct(
        run_id=run_id,
        window_index=0,
        core_start_ms=0,
        core_end_ms=3_000,
        context_start_ms=0,
        context_end_ms=3_000,
        episode_context=episode,
        reviewer=TEST_VISUAL_REVIEWER_IDENTITY,
        frames=(
            VisualReviewFrame(
                frame_id=frame_id,
                pts_ms=1_000,
                path="frames/frame-000001.png",
                sha256=sha256(frame_bytes).hexdigest(),
                ownership=VisualFrameOwnership.CORE,
            ),
        ),
        reviewer_input_digest="0" * 64,
    )
    assignment = WindowVisualReviewAssignment.model_validate(
        assignment.model_copy(
            update={
                "reviewer_input_digest": visual_review_assignment_digest(assignment)
            }
        ).model_dump()
    )
    return WindowVisualReviewArtifact(
        run_id=run_id,
        assignments=(assignment,),
        results=(
            WindowVisualReviewResult(
                window_index=0,
                reviewer_input_digest=assignment.reviewer_input_digest,
                frame_observations=(FrameVisualObservation(frame_id=frame_id),),
                context_spans=(
                    VisualContextSpan(
                        context_span_id="scene-one",
                        supporting_frame_ids=(frame_id,),
                        context="두 사람은 같은 소파에 앉아 대화하고 있다.",
                    ),
                ),
                candidates=(),
            ),
        ),
    )


def test_candidate_free_scene_context_survives_human_edit_and_preparation(
    tmp_path: Path,
) -> None:
    """Scene evidence survives a changed cue ID without invented speech scope."""
    store, run_id, state, transcript, frame_bytes = canonical_inputs(tmp_path)
    assert state.context is not None
    raw = _visual_context(run_id, state.context, frame_bytes)
    write_model_atomic(store.paths(run_id).window_visual_review, raw)
    transcript = transcript.model_copy(
        update={
            "cues": tuple(
                cue.model_copy(update={"vision_evidence_ids": ()})
                for cue in transcript.cues
            ),
            "vision_evidence": (),
        }
    )

    context, source = build_canonical_source_pair(
        store, run_id, state=state, transcript=transcript
    )
    edited = bind_canonical_digests(
        source.model_copy(
            update={
                "revision_id": "human-edit",
                "supersedes_revision_id": source.revision_id,
                "cues": (source.cues[0].model_copy(update={"cue_id": "human-merged"}),),
            }
        )
    )
    finalized = finalize_source(
        edited,
        SourceFinalization(
            finalized_at="2026-09-08T00:00:00Z",
            finalized_by="human-editor",
        ),
        input_context=context,
    )
    prepared = prepare_translation_assets(finalized, context)
    ready = ready_context_asset(prepared.context, source=finalized)

    assert len(context.facts) == 1
    fact = context.facts[0]
    assert fact.statement == raw.results[0].context_spans[0].context
    assert fact.evidence_ids == ("frame-000001",)
    assert fact.scope_source_cue_ids == ()
    assert fact.participant_ids == ()
    assert fact.decision_status is ContextDecisionStatus.UNRESOLVED
    assert not fact.blocking
    assert ready.facts == context.facts
    assert context.evidence.entries == source.evidence.entries
    assert (
        context.evidence.entries[0].resource_digest == sha256(frame_bytes).hexdigest()
    )
    assert all(cue.context_note is None for cue in source.cues)
    assert tuple(cue.text for cue in source.cues) == tuple(
        cue.speech for cue in transcript.cues
    )
    verify_canonical_digests(context)
    verify_canonical_digests(ready)


@pytest.mark.parametrize("failure", ["foreign_run", "stale_context", "stale_frame"])
def test_scene_context_rejects_stale_or_foreign_inputs(
    tmp_path: Path,
    failure: str,
) -> None:
    """Ordinary artifact reuse mistakes cannot enter the translation context."""
    store, run_id, state, transcript, frame_bytes = canonical_inputs(tmp_path)
    assert state.context is not None
    raw_run_id = (
        RunId("22222222-2222-4222-8222-222222222222")
        if failure == "foreign_run"
        else run_id
    )
    episode = (
        EpisodeContext(raw_text="another episode")
        if failure == "stale_context"
        else state.context
    )
    raw = _visual_context(raw_run_id, episode, frame_bytes)
    paths = store.paths(run_id)
    write_model_atomic(paths.window_visual_review, raw)
    if failure == "stale_frame":
        _ = (paths.run_dir / "frames/frame-000001.png").write_bytes(b"changed")

    with pytest.raises(CanonicalSourceExportError):
        _ = build_canonical_source_pair(
            store, run_id, state=state, transcript=transcript
        )

    assert not paths.canonical_source.exists()
    assert not paths.input_context.exists()
