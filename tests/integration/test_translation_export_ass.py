# Copyright 2026 Transcription Module contributors

"""Authoritative ASS adapter tests at the public translation-source seam."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Never

import pytest

from transcription_module.media import PreparedMediaManifest
from transcription_module.models import (
    EpisodeContext,
    RunId,
    RunState,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.translation_export_ass import export_ass_translation_source
from transcription_module.translation_export_evidence import TranslationExportError
from translation_module.common import ContentKind
from translation_module.preparation import source_digest
from translation_module.source import EvidenceKind, SourceMaturity, SourceSnapshot

EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)
FIXTURE_DURATION_MS = 4_000
FULLWIDTH_PIPE = "\uFF5C"


def _ass_payload() -> bytes:
    return (
        "[Script Info]\nTitle: fixture\n\n[Events]\n"
        f"{EVENT_FORMAT}\n"
        f"Dialogue: 0,0:00:02.00,0:00:03.00,Default, A | B{FULLWIDTH_PIPE}A ,"
        "0,0,0,웃음,"
        "literal | text ①, comma\n"
        "Dialogue: 0,0:00:00.01,0:00:01.00,Default,B · C,0,0,0,,먼저\n"
    ).encode()


def _finalized_ass_run(tmp_path: Path) -> tuple[RunStore, RunId, bytes]:
    store = RunStore(tmp_path / "runs")
    run_id = RunId("ass-export-fixture")
    source_url = YouTubeVideoUrl("https://www.youtube.com/watch?v=ass-export")
    paths = store.create(
        RunState(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.FINALIZED,
            validation_status=ValidationStatus.VALID,
            context=EpisodeContext(raw_text="source state context"),
        )
    )
    write_model_atomic(
        paths.media_manifest,
        PreparedMediaManifest(
            run_id=run_id,
            source_url=source_url,
            workflow_status=WorkflowStatus.PROCESSING,
            video_path="input/source.mp4",
            audio_path="input/audio.wav",
            duration_ms=FIXTURE_DURATION_MS,
            frame_count=0,
        ),
    )
    _ = (paths.run_dir / "context-observations.json").write_text(
        json.dumps(
            {
                "run_id": str(run_id),
                "source_url": str(source_url),
                "episode": "episode observation",
                "structure": "opening then talk",
                "identity_rule": "use names, voice, and frames",
                "people": [
                    {"name": "A", "role": "host", "appearance": "black shirt"},
                    {"name": "B", "role": "guest", "appearance": "white shirt"},
                ],
            }
        ),
        encoding="utf-8",
    )
    payload = _ass_payload()
    _ = paths.transcript_ass.write_bytes(payload)
    _ = paths.transcript.write_text("{deliberately stale", encoding="utf-8")
    return store, run_id, payload


def test_ass_export_maps_context_order_provenance_without_stale_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASS events alone become deterministic cue-bound translation source."""
    store, run_id, payload = _finalized_ass_run(tmp_path)
    output = tmp_path / "source.json"
    def fail_stale_read(_store: RunStore, _run_id: RunId) -> Never:
        pytest.fail("ASS adapter read stale transcript.json")

    monkeypatch.setattr(RunStore, "load_transcript", fail_stale_read)

    exported = export_ass_translation_source(
        store,
        run_id,
        output,
        hashlib.sha256(payload).hexdigest(),
    )
    first_bytes = output.read_bytes()
    repeated = export_ass_translation_source(
        store,
        run_id,
        output,
        hashlib.sha256(payload).hexdigest(),
    )

    assert output.read_bytes() == first_bytes
    assert repeated == exported == SourceSnapshot.model_validate_json(first_bytes)
    assert exported.schema_version == "source-transcript.v1"
    assert exported.maturity is SourceMaturity.FINALIZED
    assert exported.content_digest == source_digest(exported)
    assert exported.duration_ms == FIXTURE_DURATION_MS
    assert tuple(cue.cue_id for cue in exported.cues) == (
        "ass-export-fixture:ass-dialogue:000002",
        "ass-export-fixture:ass-dialogue:000001",
    )
    assert tuple(cue.sequence for cue in exported.cues) == (1, 2)
    assert exported.cues[1].text == "literal | text ①, comma"
    assert exported.cues[1].speakers == ("A", "B")
    assert exported.cues[1].context_note == "웃음"
    assert exported.cues[0].context_note is None
    assert all(cue.content_kind is ContentKind.UNKNOWN for cue in exported.cues)
    assert "웃음" not in exported.context.summary
    assert exported.context.summary == (
        "회차: episode observation\n"
        "구성: opening then talk\n"
        "신원 규칙: use names, voice, and frames\n"
        "인물: A | host | black shirt\n"
        "인물: B | guest | white shirt\n"
        "보충: source state context"
    )
    assert tuple(person.name for person in exported.context.participants) == ("A", "B")
    assert exported.context_evidence_ids == ()
    assert len(exported.evidence.entries) == 1
    ass_evidence = exported.evidence.entries[0]
    assert ass_evidence.evidence_id == "source-ass"
    assert ass_evidence.kind is EvidenceKind.TEXT
    assert ass_evidence.media_type == "text/x-ssa"
    assert ass_evidence.locator == "transcript.ass"
    assert ass_evidence.resource_digest == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("failed_gate", ["state", "context", "media"])
def test_ass_export_requires_current_finalized_contextual_media_run(
    tmp_path: Path, failed_gate: str
) -> None:
    """Every run gate fails before an output can be published."""
    store, run_id, payload = _finalized_ass_run(tmp_path)
    paths = store.paths(run_id)
    if failed_gate == "state":
        write_model_atomic(
            paths.state,
            store.load_state(run_id).model_copy(
                update={"validation_status": ValidationStatus.PENDING}
            ),
        )
    elif failed_gate == "context":
        (paths.run_dir / "context-observations.json").unlink()
    else:
        media = PreparedMediaManifest.model_validate_json(
            paths.media_manifest.read_bytes()
        )
        write_model_atomic(
            paths.media_manifest,
            media.model_copy(update={"frame_count": 1}),
        )
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(TranslationExportError):
        _ = export_ass_translation_source(
            store, run_id, output, hashlib.sha256(payload).hexdigest()
        )

    assert not output.exists()


def test_ass_export_hash_and_alias_failures_preserve_both_files(tmp_path: Path) -> None:
    """Digest mismatch and output alias never alter source or destination."""
    store, run_id, payload = _finalized_ass_run(tmp_path)
    paths = store.paths(run_id)
    output = tmp_path / "existing.json"
    _ = output.write_bytes(b"existing")

    with pytest.raises(TranslationExportError, match="SHA-256"):
        _ = export_ass_translation_source(store, run_id, output, "0" * 64)
    with pytest.raises(TranslationExportError, match="output"):
        _ = export_ass_translation_source(
            store,
            run_id,
            paths.transcript_ass,
            hashlib.sha256(payload).hexdigest(),
        )

    assert output.read_bytes() == b"existing"
    assert paths.transcript_ass.read_bytes() == payload


def test_ass_export_detects_source_mutation_before_atomic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source changed after parsing cannot publish a mixed-generation output."""
    store, run_id, payload = _finalized_ass_run(tmp_path)
    ass_path = store.paths(run_id).transcript_ass
    output = tmp_path / "must-not-exist.json"
    real_read_bytes = Path.read_bytes
    ass_reads = 0

    def mutate_after_first_read(path: Path) -> bytes:
        nonlocal ass_reads
        contents = real_read_bytes(path)
        if path == ass_path and ass_reads == 0:
            ass_reads += 1
            _ = path.write_bytes(contents + b"\n")
        return contents

    monkeypatch.setattr(Path, "read_bytes", mutate_after_first_read)

    with pytest.raises(TranslationExportError, match="changed"):
        _ = export_ass_translation_source(
            store, run_id, output, hashlib.sha256(payload).hexdigest()
        )

    assert ass_reads == 1
    assert not output.exists()
