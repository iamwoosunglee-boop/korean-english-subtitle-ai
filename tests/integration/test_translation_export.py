# Copyright 2026 Transcription Module contributors

"""Finalized transcription runs export standalone translation sources."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from tests.integration.translation_export_test_support import (
    CONTEXT_FRAME_BYTES,
    CONTEXT_FRAME_PTS_MS,
    FRAME_PTS_MS,
)
from tests.integration.translation_export_test_support import (
    finalized_run_fixture as _finalized_run,
)

if TYPE_CHECKING:
    from pathlib import Path

from transcription_module.models import (
    CueId,
    Milliseconds,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
)
from transcription_module.storage import write_model_atomic
from transcription_module.translation_export import (
    TranslationExportError,
    export_translation_source,
)
from translation_module.contracts import ContentKind, SourceMaturity, SourceSnapshot
from translation_module.preparation import source_digest


def test_export_selects_draft_source_maturity(tmp_path: Path) -> None:
    """A finalized transcription can label its downstream source as draft."""
    store, run_id, _, _ = _finalized_run(tmp_path)
    output = tmp_path / "draft-source.json"

    exported = export_translation_source(
        store,
        run_id,
        output,
        SourceMaturity.DRAFT,
    )

    assert exported.maturity is SourceMaturity.DRAFT
    assert exported.content_digest == source_digest(exported)
    assert (
        SourceSnapshot.model_validate_json(output.read_text(encoding="utf-8"))
        == exported
    )


def test_export_builds_digest_bound_source_from_current_run(tmp_path: Path) -> None:
    """The adapter carries context, speaker plurality and frame evidence."""
    store, run_id, frame_path, frame_bytes = _finalized_run(tmp_path)
    output = tmp_path / "source.json"

    exported = export_translation_source(
        store, run_id, output, SourceMaturity.FINALIZED
    )
    restored = SourceSnapshot.model_validate_json(output.read_text(encoding="utf-8"))

    assert restored == exported
    assert restored.document_id == str(run_id)
    assert restored.content_digest == source_digest(restored)
    assert restored.maturity is SourceMaturity.FINALIZED
    assert restored.context.summary == "은하섬 멤버들이 게스트를 맞이하는 공연 회차"
    assert tuple(item.name for item in restored.context.participants) == (
        "한지아",
        "박도현",
    )
    assert restored.cues[0].speakers == ("미확인 화자",)
    assert restored.cues[1].speakers == ("한지아", "박도현")
    assert all(cue.content_kind is ContentKind.UNKNOWN for cue in restored.cues)
    assert restored.cues[1].evidence_ids == ("f000001",)
    run_dir = store.paths(run_id).run_dir
    assert restored.evidence.base_uri == f"{run_dir.resolve().as_uri()}/"
    evidence = restored.evidence.entries[0]
    assert evidence.evidence_id == "f000001"
    assert evidence.locator == str(frame_path.relative_to(run_dir))
    assert evidence.pts_ms == FRAME_PTS_MS
    assert evidence.resource_digest == hashlib.sha256(frame_bytes).hexdigest()


def test_export_splits_only_canonical_speaker_delimiter(tmp_path: Path) -> None:
    """Canonical speaker boundaries export while dialogue bytes stay untouched."""
    store, run_id, _, _ = _finalized_run(tmp_path)
    transcript = store.load_transcript(run_id)
    dialogue = "한지아 | 박도현\uff5c① · 파이프"
    speaker_cases = (
        ("한지아|박도현", ("한지아", "박도현")),
        ("한지아|박도현|오태우|세라", ("한지아", "박도현", "오태우", "세라")),
    )
    cues = tuple(
        transcript.cues[0].model_copy(
            update={
                "sequence": sequence,
                "cue_id": CueId(f"speaker-variant-{sequence}"),
                "start_tc_ms": Milliseconds((sequence - 1) * 400),
                "end_tc_ms": Milliseconds(sequence * 400),
                "speech": dialogue,
                "speaker": speaker,
            }
        )
        for sequence, (speaker, _) in enumerate(speaker_cases, start=1)
    )
    write_model_atomic(
        store.paths(run_id).transcript,
        transcript.model_copy(update={"cues": cues}),
    )

    exported = export_translation_source(
        store,
        run_id,
        tmp_path / "speaker-source.json",
        SourceMaturity.FINALIZED,
    )

    assert tuple(cue.speakers for cue in exported.cues) == tuple(
        expected for _, expected in speaker_cases
    )
    expected_dialogue_bytes = dialogue.encode()
    assert all(cue.text.encode() == expected_dialogue_bytes for cue in exported.cues)


@pytest.mark.parametrize(
    ("state_status", "validation_status", "cue_id", "uncertainty"),
    [
        (
            WorkflowStatus.PROCESSING,
            ValidationStatus.VALID,
            "cue-unknown",
            Uncertainty.CERTAIN,
        ),
        (
            WorkflowStatus.FINALIZED,
            ValidationStatus.PENDING,
            "cue-unknown",
            Uncertainty.CERTAIN,
        ),
        (
            WorkflowStatus.FINALIZED,
            ValidationStatus.VALID,
            None,
            Uncertainty.CERTAIN,
        ),
        (
            WorkflowStatus.FINALIZED,
            ValidationStatus.VALID,
            "cue-unknown",
            Uncertainty.NEEDS_CONFIRMATION,
        ),
    ],
)
def test_export_rejects_nonfinal_run_without_writing(
    tmp_path: Path,
    state_status: WorkflowStatus,
    validation_status: ValidationStatus,
    cue_id: str | None,
    uncertainty: Uncertainty,
) -> None:
    """Every publication gate is checked before the destination is touched."""
    store, run_id, _, _ = _finalized_run(tmp_path)
    paths = store.paths(run_id)
    state = store.load_state(run_id).model_copy(
        update={
            "workflow_status": state_status,
            "validation_status": validation_status,
        }
    )
    transcript = store.load_transcript(run_id)
    first = transcript.cues[0].model_copy(
        update={"cue_id": cue_id, "uncertainty": uncertainty}
    )
    transcript = transcript.model_copy(
        update={
            "validation_status": validation_status,
            "cues": (first, *transcript.cues[1:]),
        }
    )
    write_model_atomic(paths.state, state)
    write_model_atomic(paths.transcript, transcript)
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(TranslationExportError):
        _ = export_translation_source(store, run_id, output, SourceMaturity.FINALIZED)

    assert not output.exists()


def test_export_uses_observation_intervals_as_source_context_evidence(
    tmp_path: Path,
) -> None:
    """Episode observations select frames without copying them onto cues."""
    store, run_id, _, _ = _finalized_run(tmp_path)
    paths = store.paths(run_id)
    transcript = store.load_transcript(run_id)
    write_model_atomic(
        paths.transcript,
        transcript.model_copy(
            update={
                "cues": tuple(
                    cue.model_copy(update={"vision_evidence_ids": ()})
                    for cue in transcript.cues
                ),
                "vision_evidence": (),
            }
        ),
    )
    _ = paths.run_dir.joinpath("context-observations.json").write_text(
        json.dumps(
            {
                "run_id": str(run_id),
                "source_url": str(store.load_state(run_id).source_url),
                "source_intervals_ms": [[1_400, 1_600]],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "source.json"

    source = export_translation_source(store, run_id, output, SourceMaturity.FINALIZED)

    assert source.context_evidence_ids == ("f000002",)
    assert tuple(entry.evidence_id for entry in source.evidence.entries) == ("f000002",)
    assert all(not cue.evidence_ids for cue in source.cues)
    assert source.evidence.entries[0].pts_ms == CONTEXT_FRAME_PTS_MS
    assert (
        source.evidence.entries[0].resource_digest
        == hashlib.sha256(CONTEXT_FRAME_BYTES).hexdigest()
    )


def test_export_uses_observed_source_frame_ids_as_context_evidence(
    tmp_path: Path,
) -> None:
    """Current observation frame IDs are verified and exported as context."""
    store, run_id, _, _ = _finalized_run(tmp_path)
    paths = store.paths(run_id)
    _ = paths.run_dir.joinpath("context-observations.json").write_text(
        json.dumps(
            {
                "run_id": str(run_id),
                "source_url": str(store.load_state(run_id).source_url),
                "source_frame_ids": ["f000002"],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "source.json"

    source = export_translation_source(store, run_id, output, SourceMaturity.DRAFT)

    assert source.context_evidence_ids == ("f000002",)
    assert tuple(entry.evidence_id for entry in source.evidence.entries) == (
        "f000001",
        "f000002",
    )


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        json.dumps(
            {
                "run_id": "another-run",
                "source_url": "https://www.youtube.com/watch?v=export-fixture",
                "source_intervals_ms": [[1_400, 1_600]],
            }
        ),
        json.dumps(
            {
                "run_id": "translation-export-fixture",
                "source_url": "https://www.youtube.com/watch?v=export-fixture",
                "source_intervals_ms": [[1_600, 1_400]],
            }
        ),
    ],
)
def test_export_rejects_malformed_context_observations_without_writing(
    tmp_path: Path,
    payload: str,
) -> None:
    """Malformed or foreign observation provenance cannot publish output."""
    store, run_id, _, _ = _finalized_run(tmp_path)
    _ = (
        store.paths(run_id)
        .run_dir.joinpath("context-observations.json")
        .write_text(
            payload,
            encoding="utf-8",
        )
    )
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(TranslationExportError):
        _ = export_translation_source(store, run_id, output, SourceMaturity.FINALIZED)

    assert not output.exists()


def test_export_rejects_missing_current_frame_without_writing(tmp_path: Path) -> None:
    """A stale manifest cannot publish evidence with an unverifiable digest."""
    store, run_id, frame_path, _ = _finalized_run(tmp_path)
    frame_path.unlink()
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(TranslationExportError, match="evidence resource"):
        _ = export_translation_source(store, run_id, output, SourceMaturity.FINALIZED)

    assert not output.exists()
