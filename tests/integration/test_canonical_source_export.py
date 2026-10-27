# Copyright 2026 Transcription Module contributors

"""Canonical v2 source handoff adapter tests."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast

import pytest

import transcription_module.canonical_source_export as canonical_export_module
from tests.integration.canonical_source_export_support import canonical_inputs
from transcription_module.canonical_source_export import (
    CanonicalSourceExportError,
    build_canonical_source_pair,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    Milliseconds,
    RunId,
    Uncertainty,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from translation_module.canonical.common import DocumentLifecycleStatus
from translation_module.canonical.digest import verify_canonical_digests
from translation_module.canonical.validation import validate_canonical_save

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from translation_module.source import EvidenceEntry


def test_builds_deterministic_digest_bound_canonical_pair(tmp_path: Path) -> None:
    """Preserve rich cue/context/evidence data with canonical digest bindings."""
    store, run_id, state, transcript, frame_bytes = canonical_inputs(tmp_path)

    context, source = build_canonical_source_pair(
        store,
        run_id,
        state=state,
        transcript=transcript,
    )

    validate_canonical_save(context)
    validate_canonical_save(source)
    verify_canonical_digests(context)
    verify_canonical_digests(source)
    assert context.asset_id == f"{run_id}:context"
    assert state.context is not None
    assert context.episode_summary == state.context.raw_text
    assert [participant.name_ko for participant in context.participants] == [
        "루미",
        "한지아",
    ]
    assert source.lifecycle_status is DocumentLifecycleStatus.DRAFT
    assert source.finalization is None
    assert source.input_context_ref.content_digest == context.content_digest
    assert source.input_context_ref.path == "context.input.json"
    assert source.cues[0].text == "첫 줄\n둘째 줄 日本語 中文"
    assert source.cues[0].speakers == ("루미", "한지아")
    assert source.cues[0].screen_text == "화면 자막"
    assert source.cues[0].evidence_ids == ("frame-000001",)
    assert source.cues[1].evidence_ids == ("frame-000001",)
    assert len(source.evidence.entries) == 1
    entry = source.evidence.entries[0]
    assert entry.locator == "frames/frame-000001.png"
    assert entry.resource_digest == hashlib.sha256(frame_bytes).hexdigest()
    assert entry.pts_ms == Milliseconds(1000)

    other_store, other_id, other_state, other_transcript, _ = canonical_inputs(
        tmp_path / "other"
    )
    other_context, other_source = build_canonical_source_pair(
        other_store,
        other_id,
        state=other_state,
        transcript=other_transcript,
    )
    assert context.model_dump_json() == other_context.model_dump_json()
    assert source.model_dump_json() == other_source.model_dump_json()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_context",
        "uncertain",
        "missing_cue_id",
        "duplicate_cue_id",
        "run_id_mismatch",
        "non_nfc_context",
    ],
)
def test_rejects_invalid_authoritative_inputs_without_writing(
    tmp_path: Path,
    failure: str,
) -> None:
    """Reject invalid authoritative inputs before writing any canonical file."""
    store, run_id, state, transcript, _ = canonical_inputs(tmp_path)
    if failure == "missing_context":
        state = state.model_copy(update={"context": None})
    elif failure == "uncertain":
        bad_cue = transcript.cues[0].model_copy(
            update={"uncertainty": Uncertainty.NEEDS_CONFIRMATION}
        )
        transcript = transcript.model_copy(
            update={"cues": (bad_cue, *transcript.cues[1:])}
        )
    elif failure == "missing_cue_id":
        transcript = transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0].model_copy(update={"cue_id": None}),
                    *transcript.cues[1:],
                )
            }
        )
    elif failure == "duplicate_cue_id":
        transcript = transcript.model_copy(
            update={
                "cues": (
                    transcript.cues[0],
                    transcript.cues[1].model_copy(
                        update={"cue_id": transcript.cues[0].cue_id}
                    ),
                )
            }
        )
    elif failure == "run_id_mismatch":
        state = state.model_copy(
            update={"run_id": RunId("22222222-2222-4222-8222-222222222222")}
        )
    elif failure == "non_nfc_context":
        assert state.context is not None
        state = state.model_copy(
            update={
                "context": state.context.model_copy(update={"raw_text": "Cafe\u0301"})
            }
        )
    with pytest.raises(CanonicalSourceExportError):
        _ = build_canonical_source_pair(
            store,
            run_id,
            state=state,
            transcript=transcript,
        )

    assert not store.paths(run_id).input_context.exists()
    assert not store.paths(run_id).canonical_source.exists()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_frame",
        "source_mismatch",
        "duration_mismatch",
        "absolute_locator",
        "traversing_locator",
        "non_image",
    ],
)
def test_rejects_invalid_media_without_writing(
    tmp_path: Path,
    failure: str,
) -> None:
    """Reject stale or unsafe media before writing canonical files."""
    store, run_id, state, transcript, _ = canonical_inputs(tmp_path)
    paths = store.paths(run_id)
    media = PreparedMediaManifest.model_validate_json(
        paths.media_manifest.read_text(encoding="utf-8")
    )
    if failure == "missing_frame":
        (paths.run_dir / "frames" / "frame-000001.png").unlink()
    elif failure == "source_mismatch":
        state = state.model_copy(
            update={"source_url": YouTubeVideoUrl("https://youtu.be/other")}
        )
    elif failure == "duration_mismatch":
        media = media.model_copy(update={"duration_ms": Milliseconds(4000)})
    elif failure in {"absolute_locator", "traversing_locator"}:
        locator = (
            str((paths.run_dir / "frames" / "frame-000001.png").resolve())
            if failure == "absolute_locator"
            else "frames/../frames/frame-000001.png"
        )
        media = media.model_copy(
            update={"frames": (media.frames[0].model_copy(update={"path": locator}),)}
        )
    else:
        text_path = paths.run_dir / "frames" / "frame-000001.txt"
        _ = text_path.write_bytes(b"not an image")
        media = media.model_copy(
            update={
                "frames": (
                    media.frames[0].model_copy(
                        update={"path": "frames/frame-000001.txt"}
                    ),
                )
            }
        )
    write_model_atomic(paths.media_manifest, media)

    with pytest.raises(CanonicalSourceExportError):
        _ = build_canonical_source_pair(
            store,
            run_id,
            state=state,
            transcript=transcript,
        )

    assert not paths.input_context.exists()
    assert not paths.canonical_source.exists()


def test_rejects_frame_mutation_during_adapter_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame changed after its first hash cannot enter the source pair."""
    store, run_id, state, transcript, _ = canonical_inputs(tmp_path)
    frame_path = store.paths(run_id).run_dir / "frames" / "frame-000001.png"
    original_evidence_entries = cast(
        "Callable[..., tuple[tuple[EvidenceEntry, ...], tuple[str, ...]]]",
        vars(canonical_export_module)["evidence_entries"],
    )
    call_count = 0

    def mutate_between_hashes(
        build_store: RunStore,
        build_run_id: RunId,
        duration_ms: int,
        included_frame_ids: list[str],
        observations: tuple[tuple[str, ...], tuple[tuple[int, int], ...]],
    ) -> tuple[tuple[EvidenceEntry, ...], tuple[str, ...]]:
        nonlocal call_count
        result = original_evidence_entries(
            build_store,
            build_run_id,
            duration_ms,
            included_frame_ids,
            observations,
        )
        call_count += 1
        if call_count == 1:
            _ = frame_path.write_bytes(b"changed during adapter construction")
        return result

    monkeypatch.setattr(
        canonical_export_module,
        "evidence_entries",
        mutate_between_hashes,
    )

    with pytest.raises(CanonicalSourceExportError, match="changed during"):
        _ = build_canonical_source_pair(
            store,
            run_id,
            state=state,
            transcript=transcript,
        )

    assert not store.paths(run_id).input_context.exists()
    assert not store.paths(run_id).canonical_source.exists()
