# Copyright 2026 Transcription Module contributors

"""Export finalized transcription runs through the translation source boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from transcription_module.models import (
    Cue,
    RunId,
    Transcript,
    Uncertainty,
    ValidationStatus,
    WorkflowStatus,
    YouTubeVideoUrl,
)
from transcription_module.models import (
    EpisodeContext as TranscriptionEpisodeContext,
)
from transcription_module.source_edit_lifecycle import (
    assert_legacy_source_export_available,
)
from transcription_module.speaker_notation import parse_speaker_labels
from transcription_module.storage import RunStore, write_model_atomic
from transcription_module.translation_export_evidence import (
    TranslationExportError,
    context_observations,
    evidence_entries,
)
from translation_module.common import (
    ContentKind,
    EpisodeContext,
    EvidenceId,
    Milliseconds,
    Participant,
    SourceCueId,
)
from translation_module.preparation import bind_source_digest
from translation_module.source import (
    EvidenceManifest,
    SourceCue,
    SourceMaturity,
    SourceSnapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "TranslationExportError",
    "export_translation_source",
    "normalize_speaker_labels",
]


def export_translation_source(
    store: RunStore,
    run_id: RunId,
    output: Path,
    source_maturity: SourceMaturity,
) -> SourceSnapshot:
    """Validate and atomically export one current finalized run."""
    try:
        with store.lock_run(run_id):
            assert_legacy_source_export_available(store, run_id)
            snapshot = _build_snapshot(store, run_id, source_maturity)
            write_model_atomic(output, snapshot)
    except (OSError, ValidationError, ValueError) as error:
        raise TranslationExportError(str(error)) from error
    return snapshot


def _build_snapshot(
    store: RunStore,
    run_id: RunId,
    source_maturity: SourceMaturity,
) -> SourceSnapshot:
    context, transcript, source_url = _exportable_run(store, run_id)
    frame_ids_by_cue, included_frame_ids = referenced_frames(transcript)
    run_dir = store.paths(run_id).run_dir.resolve()
    context_intervals, context_frame_ids = context_observations(
        run_dir / "context-observations.json",
        run_id,
        source_url,
        int(transcript.duration_ms),
    )
    entries, context_evidence_ids = evidence_entries(
        store,
        run_id,
        transcript.duration_ms,
        included_frame_ids,
        (context_frame_ids, context_intervals),
    )
    participants = tuple(
        Participant(participant_id=name, name=name)
        for name in dict.fromkeys(
            item.strip() for item in context.characters if item.strip()
        )
    )
    return bind_source_digest(
        SourceSnapshot(
            document_id=str(run_id),
            content_digest="0" * 64,
            maturity=source_maturity,
            source_locale="ko-KR",
            duration_ms=cast("Milliseconds", cast("object", transcript.duration_ms)),
            context=EpisodeContext(
                summary=context.raw_text,
                participants=participants,
            ),
            context_evidence_ids=tuple(
                cast("EvidenceId", item) for item in context_evidence_ids
            ),
            evidence=EvidenceManifest(
                base_uri=f"{run_dir.as_uri()}/",
                entries=entries,
            ),
            cues=tuple(
                _source_cue(cue, evidence_ids)
                for cue, evidence_ids in zip(
                    transcript.cues,
                    frame_ids_by_cue,
                    strict=True,
                )
            ),
        )
    )


def _exportable_run(
    store: RunStore,
    run_id: RunId,
) -> tuple[TranscriptionEpisodeContext, Transcript, YouTubeVideoUrl]:
    state = store.load_state(run_id)
    transcript = store.load_transcript(run_id)
    if state.run_id != run_id or transcript.run_id != run_id:
        message = "run artifacts do not match the requested run"
        raise TranslationExportError(message)
    if any(
        item.workflow_status is not WorkflowStatus.FINALIZED
        or item.validation_status is not ValidationStatus.VALID
        for item in (state, transcript)
    ):
        message = "run state and transcript must be finalized and valid"
        raise TranslationExportError(message)
    if state.context is None:
        message = "episode context is required"
        raise TranslationExportError(message)
    cue_ids = tuple(cue.cue_id for cue in transcript.cues)
    if any(not cue_id for cue_id in cue_ids) or len(cue_ids) != len(set(cue_ids)):
        message = "cue IDs must be non-null and unique"
        raise TranslationExportError(message)
    if any(cue.uncertainty is not Uncertainty.CERTAIN for cue in transcript.cues):
        message = "all cues must be certain"
        raise TranslationExportError(message)
    return state.context, transcript, state.source_url


def referenced_frames(
    transcript: Transcript,
) -> tuple[list[tuple[str, ...]], list[str]]:
    evidence_by_id = {item.evidence_id: item for item in transcript.vision_evidence}
    if len(evidence_by_id) != len(transcript.vision_evidence):
        message = "vision evidence IDs must be unique"
        raise TranslationExportError(message)
    frame_ids_by_cue: list[tuple[str, ...]] = []
    included_frame_ids: list[str] = []
    for cue in transcript.cues:
        frame_ids: list[str] = []
        for evidence_id in cue.vision_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None or not evidence.frame_ids:
                message = f"cue evidence has no current frame metadata: {evidence_id}"
                raise TranslationExportError(message)
            for frame_id in map(str, evidence.frame_ids):
                if frame_id not in frame_ids:
                    frame_ids.append(frame_id)
                if frame_id not in included_frame_ids:
                    included_frame_ids.append(frame_id)
        frame_ids_by_cue.append(tuple(frame_ids))
    return frame_ids_by_cue, included_frame_ids


def _source_cue(cue: Cue, evidence_ids: tuple[str, ...]) -> SourceCue:
    return SourceCue(
        sequence=cue.sequence,
        cue_id=SourceCueId(str(cue.cue_id)),
        start_ms=cast("Milliseconds", cast("object", cue.start_tc_ms)),
        end_ms=cast("Milliseconds", cast("object", cue.end_tc_ms)),
        text=cue.speech,
        speakers=normalize_speaker_labels(cue.speaker),
        content_kind=ContentKind.UNKNOWN,
        screen_text=cue.screen_text,
        evidence_ids=tuple(cast("EvidenceId", item) for item in evidence_ids),
    )


def normalize_speaker_labels(speaker: str | None) -> tuple[str, ...]:
    """Return ordered labels from canonical speaker metadata."""
    labels = parse_speaker_labels(speaker)
    return labels or ("unknown",)
