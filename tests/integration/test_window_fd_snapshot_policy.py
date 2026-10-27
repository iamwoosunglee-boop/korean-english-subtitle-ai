# Copyright 2026 Transcription Module contributors

"""Task 11 snapshots policy-bound authorities and closes held descriptors."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import anyio
import pytest

from tests.integration.visual_review_support import EmptyVisualReviewer
from tests.integration.window_policy_support import (
    create_policy_window_fixture,
    empty_policy_draft,
)
from transcription_module.drafting import (
    WindowDraft,
    WindowDraftInput,
    create_window_drafts,
)
from transcription_module.media import PreparedMediaManifest
from transcription_module.storage import write_model_atomic
from transcription_module.window_evidence_errors import WindowEvidenceError

_STANDARD_DESCRIPTOR_MAX: Final = 2


def test_repository_relative_protected_run_paths_reach_drafter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected-run repository-relative paths remain confined to their run."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    repository_relative = media.model_copy(
        update={
            "video_path": Path(media.video_path).relative_to(tmp_path).as_posix(),
            "audio_path": Path(media.audio_path).relative_to(tmp_path).as_posix(),
            "subtitle_tracks": tuple(
                track.model_copy(
                    update={
                        "path": Path(track.path).relative_to(tmp_path).as_posix(),
                    }
                )
                for track in media.subtitle_tracks
            ),
        }
    )
    write_model_atomic(paths.media_manifest, repository_relative)
    monkeypatch.chdir(tmp_path)
    called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal called
        called = True
        return empty_policy_draft(data)

    _ = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert called






def test_replacement_after_snapshot_fails_at_pre_callback_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement after initial replay is rejected before callback entry."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    original_limiter = anyio.CapacityLimiter
    called = False

    def replace_before_callback(total_tokens: float) -> anyio.CapacityLimiter:
        replacement = paths.asr_transcript.with_suffix(".replacement")
        _ = replacement.write_bytes(paths.asr_transcript.read_bytes())
        _ = replacement.replace(paths.asr_transcript)
        return original_limiter(total_tokens)

    monkeypatch.setattr(anyio, "CapacityLimiter", replace_before_callback)

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal called
        called = True
        return empty_policy_draft(data)

    with pytest.raises(WindowEvidenceError, match="replaced"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert not called
    assert not paths.window_drafts.exists()


def test_replacement_during_policy_callback_keeps_snapshot_out_of_output(
    tmp_path: Path,
) -> None:
    """Callback input stays captured and replacement blocks final publication."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    observed: list[str] = []

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        observed.extend(item.speech for item in data.asr_segments)
        replacement = paths.asr_transcript.with_suffix(".replacement")
        _ = replacement.write_text("not valid ASR bytes", encoding="utf-8")
        _ = replacement.replace(paths.asr_transcript)
        return empty_policy_draft(data)

    with pytest.raises(WindowEvidenceError, match="replaced"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert observed == [
        "서울에서 다시 만났어",
        "시간만 겹치는 무관한 말",
        "동시에 대답해요",
        "동시에 대답해요",
    ]
    assert not paths.window_drafts.exists()


def test_duplicate_inode_identity_fails_before_callback(tmp_path: Path) -> None:
    """Distinct source roles cannot resolve to one device and inode identity."""
    fixture = create_policy_window_fixture(tmp_path)
    paths = fixture.store.paths(fixture.run_id)
    media = PreparedMediaManifest.model_validate_json(paths.media_manifest.read_bytes())
    write_model_atomic(
        paths.media_manifest,
        media.model_copy(update={"audio_path": media.video_path}),
    )
    called = False

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        nonlocal called
        called = True
        return empty_policy_draft(data)

    with pytest.raises(WindowEvidenceError, match=r"duplicate.*identity"):
        _ = anyio.run(
            create_window_drafts,
            fixture.store,
            fixture.run_id,
            EmptyVisualReviewer(),
            drafter,
        )

    assert not called
    assert not paths.window_drafts.exists()


def test_snapshot_descriptors_close_after_success(tmp_path: Path) -> None:
    """Every held descriptor closes after successful publication."""
    fixture = create_policy_window_fixture(tmp_path)
    _ = anyio.run(_warm_anyio)
    before = _open_descriptors()

    async def drafter(data: WindowDraftInput) -> WindowDraft:
        return empty_policy_draft(data)

    artifact = anyio.run(
        create_window_drafts,
        fixture.store,
        fixture.run_id,
        EmptyVisualReviewer(),
        drafter,
    )

    assert artifact.run_id == fixture.run_id
    assert _open_descriptors() == before




async def _warm_anyio() -> None:
    return


def _open_descriptors() -> frozenset[int]:
    return frozenset(
        int(entry.name)
        for entry in Path("/dev/fd").iterdir()
        if entry.name.isdigit() and int(entry.name) > _STANDARD_DESCRIPTOR_MAX
    )
