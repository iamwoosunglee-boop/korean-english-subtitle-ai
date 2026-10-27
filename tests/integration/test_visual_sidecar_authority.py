# Copyright 2026 Transcription Module contributors

"""Semantic visual sidecars publish atomically and replay byte-identically."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

import transcription_module.visual_sidecar_authority as sidecar_authority
from transcription_module.asr import AsrRunArtifact, AsrSegment, AsrTranscript
from transcription_module.correction_provenance import (
    build_correction_receipt,
    text_digest,
)
from transcription_module.correction_receipts import CorrectionReceiptSet
from transcription_module.cue_provenance import (
    CueIdentityInput,
    CueProvenanceRecord,
    CueProvenanceSet,
    assign_cue_ids,
)
from transcription_module.models import (
    Cue,
    EpisodeContext,
    FrameId,
    Milliseconds,
    RunId,
    RunState,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.subtitle_inventory import (
    SubtitleInventory,
    SubtitleTrackIdentity,
)
from transcription_module.visual_correction import (
    CandidateOrigin,
    TextSlice,
    VisualCorrectionRequest,
    validate_visual_correction,
)
from transcription_module.visual_evidence_policy import (
    CorrectionAction,
    EvidenceEra,
    EvidenceState,
    SemanticRelation,
    VisualRole,
    default_evidence_policy,
    policy_digest,
)
from transcription_module.visual_observations import (
    VisualObservation,
    VisualObservationSet,
)
from transcription_module.visual_sidecar_authority import (
    VisualSidecarBundle,
    VisualSidecarPublicationError,
    publish_visual_sidecars,
)

if TYPE_CHECKING:
    from pathlib import Path

RUN_ID: Final = RunId("visual-sidecar-run")
SOURCE_URL: Final = YouTubeVideoUrl("https://youtube.test/visual-sidecars")
SOURCE_SHA: Final = "a" * 64
POLICY = default_evidence_policy()
POLICY_SHA: Final = policy_digest(POLICY)
BEFORE: Final = "서울에서 다시 만났어"
RESULT: Final = "서울에서 다시 만났어요"
FAILURE_CALL_INDEX: Final = 2
SIDECAR_PATH_NAMES: Final = (
    "visual-evidence-policy.json",
    "subtitle-inventory.json",
    "visual-observations.json",
    "correction-receipts.json",
    "cue-provenance.json",
)


def _observation() -> VisualObservation:
    return VisualObservation(
        observation_id="observation-local",
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        role=VisualRole.SPEECH_SUPPORTING,
        state=EvidenceState.SUPPORTED,
        semantic_relation=SemanticRelation.EXACT,
        frame_ids=(FrameId("f001"),),
        frame_pts_ms=(1_200,),
        start_tc_ms=1_000,
        end_tc_ms=1_800,
        complete_text=RESULT,
        vad_interval_ids=("vad-1",),
        turn_ids=("turn-1",),
        caption_regime_supported=True,
        temporal_alignment_supported=True,
        speech_presence_supported=True,
        evidence_for=("exact_ko_orig", "vad_speech", "verified_turn"),
    )


def _bundle() -> tuple[VisualSidecarBundle, Transcript, AsrRunArtifact]:
    observation = _observation()
    request = VisualCorrectionRequest(
        policy_digest=POLICY_SHA,
        requested_action=CorrectionAction.REPLACE_SPAN,
        observation=observation,
        candidate_origin=CandidateOrigin.KO_ORIG,
        before_text=BEFORE,
        submitted_result=RESULT,
        source_slice=TextSlice(start=len(BEFORE) - 1, end=len(BEFORE)),
        candidate_slice=TextSlice(start=len(RESULT) - 2, end=len(RESULT)),
        speaker="루미",
    )
    correction = build_correction_receipt(
        SOURCE_SHA,
        request,
        validate_visual_correction(request),
    )
    source_evidence_ids = (
        "asr:3",
        "subtitle:sub-orig-2",
        "vision:observation-local",
    )
    cue_id = assign_cue_ids(
        RUN_ID,
        POLICY_SHA,
        (
            CueIdentityInput(
                era=EvidenceEra.POLICY_BOUND,
                source_evidence_ids=source_evidence_ids,
                start_tc_ms=1_000,
                end_tc_ms=1_800,
                speech=RESULT,
            ),
        ),
    )[0]
    transcript = Transcript(
        run_id=RUN_ID,
        workflow_status=WorkflowStatus.PROCESSING,
        validation_status=ValidationStatus.PENDING,
        duration_ms=Milliseconds(2_000),
        cues=(
            Cue(
                sequence=1,
                cue_id=cue_id,
                start_tc_ms=Milliseconds(1_000),
                end_tc_ms=Milliseconds(1_800),
                speech=RESULT,
                speaker="루미",
                uncertainty=Uncertainty.CERTAIN,
            ),
        ),
        vision_evidence=(),
    )
    asr = AsrRunArtifact(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        audio_sha256="b" * 64,
        transcript=AsrTranscript(
            text=RESULT,
            language="ko",
            segments=(
                AsrSegment(
                    source_ordinal=3,
                    start_ms=1_000,
                    end_ms=1_800,
                    speech=RESULT,
                ),
            ),
        ),
    )
    inventory = SubtitleInventory(
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        tracks=(
            SubtitleTrackIdentity(
                track_id="automatic-ko-orig",
                variant="ko-orig",
                source="automatic",
                path="input/media/subtitles/automatic.ko.ko-orig.vtt",
                sha256="c" * 64,
                eligible_for_speech=True,
            ),
        ),
    )
    observations = VisualObservationSet(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        observations=(observation,),
    )
    corrections = CorrectionReceiptSet(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        receipts=(correction,),
    )
    provenance = CueProvenanceSet(
        run_id=RUN_ID,
        era=EvidenceEra.POLICY_BOUND,
        policy_digest=POLICY_SHA,
        records=(
            CueProvenanceRecord(
                cue_id=cue_id,
                era=EvidenceEra.POLICY_BOUND,
                policy_digest=POLICY_SHA,
                source_digest=SOURCE_SHA,
                source_evidence_ids=source_evidence_ids,
                asr_ordinals=(3,),
                subtitle_track_ids=("automatic-ko-orig",),
                subtitle_cue_ids=("sub-orig-2",),
                observation_ids=("observation-local",),
                correction_receipt_id=correction.receipt_id,
                start_tc_ms=1_000,
                end_tc_ms=1_800,
                speech_digest=text_digest(RESULT),
                speaker="루미",
                existing_cue_preserved=False,
            ),
        ),
    )
    return (
        VisualSidecarBundle(
            run_id=RUN_ID,
            source_digest=SOURCE_SHA,
            policy=POLICY,
            inventory=inventory,
            observations=observations,
            corrections=corrections,
            provenance=provenance,
        ),
        transcript,
        asr,
    )


def _store(root: Path) -> tuple[RunStore, VisualSidecarBundle]:
    bundle, transcript, asr = _bundle()
    store = RunStore(root / "runs")
    paths = store.create(
        RunState(
            run_id=RUN_ID,
            source_url=SOURCE_URL,
            workflow_status=WorkflowStatus.PROCESSING,
            context=EpisodeContext(raw_text="없음"),
        )
    )
    store.save_transcript(transcript)
    write_model_atomic(paths.asr_transcript, asr)
    return store, bundle


def _sidecar_bytes(store: RunStore) -> tuple[bytes, ...]:
    run_dir = store.paths(RUN_ID).run_dir
    return tuple((run_dir / name).read_bytes() for name in SIDECAR_PATH_NAMES)


def test_publication_and_replay_are_byte_identical_across_isolated_runs(
    tmp_path: Path,
) -> None:
    """The same frozen run identity emits exact semantic sidecar JSON bytes."""
    first_store, first_bundle = _store(tmp_path / "first")
    second_store, second_bundle = _store(tmp_path / "second")

    first_receipt = publish_visual_sidecars(first_store, first_bundle)
    second_receipt = publish_visual_sidecars(second_store, second_bundle)
    first_bytes = _sidecar_bytes(first_store)
    first_inodes = tuple(
        (first_store.paths(RUN_ID).run_dir / name).stat().st_ino
        for name in SIDECAR_PATH_NAMES
    )
    replay = publish_visual_sidecars(first_store, first_bundle)

    assert first_receipt == second_receipt == replay
    assert first_bytes == _sidecar_bytes(second_store)
    assert first_bytes == _sidecar_bytes(first_store)
    assert first_inodes == tuple(
        (first_store.paths(RUN_ID).run_dir / name).stat().st_ino
        for name in SIDECAR_PATH_NAMES
    )


@pytest.mark.parametrize(
    "mutation",
    ["foreign_policy", "missing_observation", "unknown_track", "stale_asr"],
)
def test_stale_or_missing_references_fail_before_any_sidecar_write(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Cross-policy and unclosed evidence references leave prior absence exact."""
    store, bundle = _store(tmp_path / mutation)
    if mutation == "foreign_policy":
        mutated = bundle.model_copy(
            update={
                "provenance": bundle.provenance.model_copy(
                    update={"policy_digest": "d" * 64}
                )
            }
        )
    elif mutation == "missing_observation":
        mutated = bundle.model_copy(
            update={
                "observations": bundle.observations.model_copy(
                    update={"observations": ()}
                )
            }
        )
    else:
        record = bundle.provenance.records[0]
        update = (
            {"subtitle_track_ids": ("foreign-track",)}
            if mutation == "unknown_track"
            else {"asr_ordinals": (99,)}
        )
        mutated = bundle.model_copy(
            update={
                "provenance": bundle.provenance.model_copy(
                    update={"records": (record.model_copy(update=update),)}
                )
            }
        )

    with pytest.raises(VisualSidecarPublicationError):
        _ = publish_visual_sidecars(store, mutated)

    assert not any(
        (store.paths(RUN_ID).run_dir / name).exists()
        for name in SIDECAR_PATH_NAMES
    )


def test_injected_partial_write_failure_restores_complete_prior_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process-visible write failure cannot leave a partial sidecar bundle."""
    store, bundle = _store(tmp_path / "rollback")
    real_write = sidecar_authority.write_visual_sidecar
    calls = 0

    def fail_second(path: Path, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == FAILURE_CALL_INDEX:
            message = "injected sidecar write failure"
            raise OSError(message)
        real_write(path, payload)

    monkeypatch.setattr(sidecar_authority, "write_visual_sidecar", fail_second)

    with pytest.raises(OSError, match="injected sidecar"):
        _ = publish_visual_sidecars(store, bundle)

    assert not any(
        (store.paths(RUN_ID).run_dir / name).exists()
        for name in SIDECAR_PATH_NAMES
    )


def test_conflicting_existing_sidecar_is_rejected_without_overwrite(
    tmp_path: Path,
) -> None:
    """Existing nonmatching authority bytes are never silently replaced."""
    store, bundle = _store(tmp_path / "conflict")
    path = store.paths(RUN_ID).visual_policy_artifact
    prior = b'{"foreign":"authority"}\n'
    _ = path.write_bytes(prior)

    with pytest.raises(VisualSidecarPublicationError, match="conflicts"):
        _ = publish_visual_sidecars(store, bundle)

    assert path.read_bytes() == prior
    assert not any(
        (store.paths(RUN_ID).run_dir / name).exists()
        for name in SIDECAR_PATH_NAMES[1:]
    )
